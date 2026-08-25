// Code tile — Monaco editor + workspace tree + multi-file tabs.
//
//   left tree; click a file → opens a TAB. `+` creates one (a name with a slash
//   creates the folders too). Right-click a row to rename or delete. Edits
//   AUTOSAVE after a typing pause; Ctrl/Cmd+S and the Save button still work.
//
// Three things in here are load-bearing and easy to undo by accident:
//
//   1. AUTOSAVE + CONFLICT are one mechanism. Every tab carries the server's
//      `version` of what it last saw and sends it on every write. If the file
//      changed underneath, the server refuses and the tab enters `conflict`,
//      where autosave STOPS until the user chooses. Autosave without that check
//      is a machine for silently destroying whichever copy saved first.
//
//   2. Anything reading `tabs` from a timer or a keybinding must go through
//      `tabsRef`, not a dependency. This file already shipped that bug once:
//      Ctrl+S was bound at mount to a `save` closure captured when no file was
//      open, so it silently did nothing.
//
//   3. Tabs follow the filesystem. A rename retargets them (by PREFIX, so a
//      folder rename moves the files under it) and a delete closes them. A tab
//      left pointing at a deleted path is the worst case: the next autosave
//      recreates the file the user just deleted.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
// Side-effect import, and it must come before the first <Editor> mounts: it
// points the loader at our bundled Monaco instead of a CDN, registers the web
// workers, and defines the two themes below. See lib/monacoSetup.ts.
import "../../lib/monacoSetup";
import { useSettings } from "../../state/SettingsContext";
import {
  fsTree, fsRead, fsWrite, fsRename, fsDelete, fsEntryInfo, FsConflictError,
} from "../../api";

type FsNode =
  | { name: string; path: string; kind: "dir"; children: FsNode[] }
  | { name: string; path: string; kind: "file"; size: number };

type Tab = {
  path: string;
  name: string;
  content: string;
  /** What is on the server, as far as we know. `content !== original` is dirty. */
  original: string;
  /** The server's version of `original`. Sent on every save so somebody else's
   *  work cannot be silently overwritten. "" means "this file did not exist". */
  version: string;
  /** Set when the server refused a save because the file moved underneath us.
   *  While set, autosave stops — retrying automatically would either lose their
   *  change or lose ours, and the choice is the user's. */
  conflict?: string | null;
};

function languageFor(name: string): string {
  const ext = (name.split(".").pop() || "").toLowerCase();
  return {
    ts: "typescript", tsx: "typescript",
    js: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
    py: "python", rs: "rust", go: "go",
    json: "json", yaml: "yaml", yml: "yaml", toml: "ini",
    md: "markdown", mdx: "markdown",
    html: "html", css: "css", scss: "scss",
    sh: "shell", ps1: "powershell", sql: "sql",
    java: "java", c: "c", cpp: "cpp", h: "cpp", hpp: "cpp",
    rb: "ruby", php: "php",
  }[ext] || "plaintext";
}

function fileIcon(name: string): string {
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["ts", "tsx", "js", "jsx", "mjs", "cjs"].includes(ext)) return "TS";
  if (["py"].includes(ext)) return "PY";
  if (["rs"].includes(ext)) return "RS";
  if (["md", "mdx"].includes(ext)) return "MD";
  if (["json"].includes(ext)) return "JSON";
  if (["html"].includes(ext)) return "HTML";
  if (["css", "scss"].includes(ext)) return "CSS";
  if (["yaml", "yml", "toml"].includes(ext)) return "CFG";
  if (["ps1", "sh"].includes(ext)) return "SH";
  return "·";
}

/** Every ancestor directory of a workspace-relative path. "a/b/c.ts" -> ["a","a/b"]. */
function ancestorsOf(path: string): string[] {
  const parts = path.split("/").filter(Boolean);
  parts.pop();                                   // drop the leaf
  const out: string[] = [];
  for (let i = 0; i < parts.length; i++) out.push(parts.slice(0, i + 1).join("/"));
  return out;
}

// Expansion lives in the PARENT, keyed by path, not in each node's own useState.
//
// It survived a refresh either way — the nodes are keyed by path, so React
// reuses the instances — but local state cannot be opened from the outside, and
// the tile needs exactly that: after creating "src/greeting.ts" the tree
// refreshed with `src` still collapsed, so the file you had just made was
// invisible in the tree while sitting open in a tab. Same for a rename that
// moves a file into a folder.
function TreeNode({ node, depth, current, expanded, onToggle, onPick, onContext }: {
  node: FsNode; depth: number; current: string | null;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  onPick: (path: string) => void;
  onContext: (e: React.MouseEvent, node: FsNode) => void;
}) {
  const pad = { paddingLeft: 8 + depth * 12 };
  if (node.kind === "dir") {
    // One meaning for the set: "in here" is expanded. The root starts in it (the
    // state is seeded with ""), so there is no special case to get backwards.
    const open = expanded.has(node.path);
    return (
      <div className="code-tree-dir">
        <div className="code-tree-row code-tree-dirrow" style={pad}
             onClick={() => onToggle(node.path)}
             onContextMenu={(e) => onContext(e, node)}>
          <span className="code-tree-chev">{open ? "▾" : "▸"}</span>
          <span className="code-tree-name">{node.name || "/"}</span>
        </div>
        {open && (
          <div className="code-tree-children">
            {node.children.map((c) => (
              <TreeNode key={c.path} node={c} depth={depth + 1} current={current}
                        expanded={expanded} onToggle={onToggle} onPick={onPick}
                        onContext={onContext} />
            ))}
          </div>
        )}
      </div>
    );
  }
  const active = current === node.path;
  return (
    <div className={`code-tree-row code-tree-filerow ${active ? "active" : ""}`} style={pad}
         onClick={() => onPick(node.path)}
         onContextMenu={(e) => onContext(e, node)}
         title={node.path}>
      <span className="code-tree-fileicon">{fileIcon(node.name)}</span>
      <span className="code-tree-name">{node.name}</span>
    </div>
  );
}

export default function CodeTile() {
  const { resolvedTheme } = useSettings();
  const [rootInput, setRootInput] = useState("");
  const [tree, setTree] = useState<FsNode | null>(null);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [saving, setSaving] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveErr, setSaveErr] = useState<string | null>(null);
  // Creating a file. The backend has always supported this — /fs/write creates
  // parent directories and writes — but nothing in the UI called it, so a new
  // account opened the tile onto an empty tree with no way to put anything in
  // it. That is a dead end, not an empty state.
  const [newName, setNewName] = useState<string | null>(null);
  const [newErr, setNewErr] = useState<string | null>(null);
  const [openErr, setOpenErr] = useState<string | null>(null);
  // Which directories are open, by workspace-relative path. "" is the root.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set([""]));

  // Right-click menu over a tree row.
  //
  // Positioned ABSOLUTELY INSIDE the scrolling tree container, not fixed to the
  // viewport. The tile lives on a canvas with `transform: translate() scale()`,
  // and a transformed ancestor becomes the containing block for position:fixed
  // — so a "fixed" menu placed at clientX/clientY lands somewhere else entirely,
  // and moves when you pan. Anchoring inside the tree also means the menu
  // scrolls with the row it belongs to.
  const treeRef = useRef<HTMLDivElement | null>(null);
  const [menu, setMenu] = useState<
    { path: string; name: string; kind: "file" | "dir"; top: number; left: number } | null
  >(null);
  const [renaming, setRenaming] = useState<{ path: string; value: string } | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);

  const onTreeContext = useCallback((e: React.MouseEvent, node: FsNode) => {
    e.preventDefault();
    e.stopPropagation();
    if (!node.path) return;                 // the workspace root has no actions
    const box = treeRef.current?.getBoundingClientRect();
    setActionErr(null);
    setMenu({
      path: node.path,
      name: node.name,
      kind: node.kind,
      top: e.clientY - (box?.top ?? 0) + (treeRef.current?.scrollTop ?? 0),
      left: e.clientX - (box?.left ?? 0),
    });
  }, []);

  // Any click anywhere else dismisses it.
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("click", close);
    window.addEventListener("keydown", close);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", close);
    };
  }, [menu]);

  // The live tabs, readable from callbacks that must NOT depend on `tabs`.
  //
  // Anything that lists `tabs` as a dependency is rebuilt on every keystroke —
  // which re-binds it into every tree row as you type — and anything that omits
  // it without this ref reads a stale array. Ctrl+S already shipped that bug
  // once (see saveRef below); autosave, closeTab and openFile all need the same
  // discipline.
  const tabsRef = useRef(tabs);
  useEffect(() => { tabsRef.current = tabs; }, [tabs]);

  const active = tabs.find((t) => t.path === activePath) || null;
  const dirty = !!active && active.content !== active.original;

  const toggleDir = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  }, []);

  /** Open every directory above a path, so a file the user just made or moved
   *  is actually visible in the tree rather than hidden inside a closed folder. */
  const reveal = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const a of ancestorsOf(path)) next.add(a);
      return next;
    });
  }, []);

  // No path opens the workspace, which is the only place the backend will serve
  // from. Asking the user for an absolute path made sense on the desktop build,
  // where it was their own machine; hosted, there is exactly one directory and
  // they have no way to know what it is called.
  const openRoot = useCallback(async (raw?: string) => {
    setTreeError(null);
    try {
      const body = await fsTree(raw?.trim() || undefined);
      setTree(body.tree as FsNode);
    } catch (e) { setTreeError(e instanceof Error ? e.message : String(e)); }
  }, []);

  useEffect(() => { void openRoot(); }, [openRoot]);

  const openFile = useCallback(async (path: string) => {
    setSaveErr(null); setSaving("idle"); setOpenErr(null);
    // Focus it if it is already open. (A `setTabs` that returned `prev` either
    // way used to sit above this line, doing nothing at all.)
    if (tabsRef.current.some((t) => t.path === path)) { setActivePath(path); return; }
    try {
      const body = await fsRead(path);
      const name = path.split(/[\\/]/).pop() || path;
      setTabs((prev) => prev.some((t) => t.path === body.path)
        ? prev
        : [...prev, { path: body.path, name, content: body.content,
                      original: body.content, version: body.version, conflict: null }]);
      setActivePath(body.path);
    } catch (e) {
      // Its own state, NOT saveErr. saveErr is only rendered when
      // `saving === "error"`, and this path never sets `saving` — so a failed
      // open wrote to a variable nothing could display, and clicking a file
      // that would not open did visibly nothing at all.
      //
      // Reusing saveErr would also be wrong in the other direction: a later
      // successful save clears it, wiping a read error the user never saw.
      setOpenErr(e instanceof Error ? e.message : String(e));
    }
    // No `tabs` dependency: it reads through tabsRef instead. Depending on
    // `tabs` rebuilt this callback on every keystroke, which re-bound it into
    // every tree row on every character typed.
  }, []);

  // Create an empty file and open it. `path` is workspace-relative, so a name
  // containing a slash creates the folders on the way — which is the whole of
  // "new folder" without a second control for it.
  const createFile = useCallback(async () => {
    const name = (newName || "").trim().replace(/^\/+/, "");
    if (!name) { setNewName(null); return; }
    setNewErr(null);
    try {
      const r = await fsWrite(name, "");
      setNewName(null);
      await openRoot();                       // the tree has to show it
      reveal(r.path);                         // ...and it has to be VISIBLE in it
      const leaf = name.split("/").pop() || name;
      setTabs((prev) => prev.some((t) => t.path === r.path)
        ? prev
        // The version comes from the write that just created it, so the first
        // real save carries a version the server recognises. Without it the tab
        // would send "" — the version of a file that does NOT exist — and every
        // save after the first would conflict with itself.
        : [...prev, { path: r.path, name: leaf, content: "", original: "",
                      version: r.version, conflict: null }]);
      setActivePath(r.path);
    } catch (e) {
      setNewErr(e instanceof Error ? e.message : String(e));
    }
  }, [newName, openRoot, reveal]);

  const closeTab = useCallback((path: string) => {
    // The close affordance IS the dirty dot — it renders ● while there are
    // unsaved edits and × otherwise, so the single most likely click on a tab
    // you are actively editing was the one that threw the edits away. Nothing
    // was persisted, nothing was recoverable.
    //
    // The glyph is not the problem (VS Code renders the same way); the missing
    // confirm was. Deliberately OUTSIDE the setTabs updater: StrictMode
    // double-invokes updaters, which would show this dialog twice.
    const t = tabsRef.current.find((x) => x.path === path);
    if (t && t.content !== t.original) {
      if (!window.confirm(`Discard unsaved changes to ${t.name}?`)) return;
    }
    setTabs((prev) => {
      const idx = prev.findIndex((t2) => t2.path === path);
      const next = prev.filter((t2) => t2.path !== path);
      setActivePath((cur) => {
        if (cur !== path) return cur;
        if (!next.length) return null;
        return next[Math.max(0, idx - 1)].path;
      });
      return next;
    });
  }, []);

  const onChange = useCallback((v: string | undefined) => {
    setTabs((prev) => prev.map((t) => t.path === activePath ? { ...t, content: v ?? "" } : t));
  }, [activePath]);

  // Unsaved work must survive a stray Ctrl+W or a browser Back. Tab state is
  // local React state and nothing persists it, so closing the tab took every
  // dirty file with it, silently and unrecoverably.
  //
  // Registered ONLY while something is actually dirty, and torn down the moment
  // that stops being true. An unconditional beforeunload prompts on every
  // navigation for no reason, which is a worse bug than the one it fixes —
  // people learn to dismiss it, and then it does not protect anything.
  const anyDirty = tabs.some((t) => t.content !== t.original);
  useEffect(() => {
    if (!anyDirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [anyDirty]);

  // One save path for the button, Ctrl+S and autosave.
  //
  // `force` is the "overwrite theirs" branch: it sends the server's CURRENT
  // version instead of ours, which is the only way past the conflict check, and
  // is only ever reached by the user clicking it.
  const saveTab = useCallback(async (path: string, force = false) => {
    const t = tabsRef.current.find((x) => x.path === path);
    if (!t) return;
    if (t.content === t.original && !force) return;
    if (t.conflict && !force) return;          // never autosave over a conflict

    setSaving("saving"); setSaveErr(null);
    const sending = t.content;
    try {
      const r = await fsWrite(t.path, sending, force ? undefined : t.version);
      setTabs((prev) => prev.map((x) => x.path === t.path
        // `original: sending`, not x.content — the user may have typed more
        // while the request was in flight, and claiming THAT is saved would
        // clear the dirty marker on text the server has never seen.
        ? { ...x, original: sending, version: r.version, conflict: null }
        : x));
      setSaving("saved");
      window.setTimeout(() => setSaving("idle"), 1400);
    } catch (e) {
      if (e instanceof FsConflictError) {
        // Not an error state — a decision. Park it on the tab, stop autosaving
        // it, and let the user choose. Silently retrying loses somebody's work
        // either way, and picking for them is the one thing we must not do.
        setTabs((prev) => prev.map((x) => x.path === t.path
          ? { ...x, conflict: e.message, version: e.currentVersion || x.version }
          : x));
        setSaving("idle");
        return;
      }
      setSaving("error");
      setSaveErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const save = useCallback(async () => {
    if (activePath) await saveTab(activePath);
  }, [activePath, saveTab]);

  /** Re-read a file from the server, throwing away local edits. The "theirs
   *  wins" half of a conflict, and the only way to clear one without writing. */
  const openFileFresh = useCallback(async (path: string) => {
    try {
      const body = await fsRead(path);
      setTabs((prev) => prev.map((t) => t.path === path
        ? { ...t, content: body.content, original: body.content,
            version: body.version, conflict: null }
        : t));
      setSaving("idle"); setSaveErr(null);
    } catch (e) {
      setOpenErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  // ── Rename ────────────────────────────────────────────────────────────────
  const doRename = useCallback(async (from: string, to: string) => {
    const target = to.trim().replace(/^\/+/, "");
    if (!target || target === from) { setRenaming(null); return; }
    setActionErr(null);
    try {
      const r = await fsRename(from, target);
      setRenaming(null);
      // Every open tab under the old path follows it. A DIRECTORY rename moves
      // everything beneath it, so this is a prefix match, not an equality one —
      // otherwise renaming a folder leaves its open files pointing at paths
      // that no longer exist, and the next save 404s or, worse, recreates them
      // at the old location.
      setTabs((prev) => prev.map((t) => {
        if (t.path === r.from) {
          return { ...t, path: r.to, name: r.to.split("/").pop() || r.to };
        }
        if (t.path.startsWith(r.from + "/")) {
          const moved = r.to + t.path.slice(r.from.length);
          return { ...t, path: moved, name: moved.split("/").pop() || moved };
        }
        return t;
      }));
      setActivePath((cur) => {
        if (cur === r.from) return r.to;
        if (cur && cur.startsWith(r.from + "/")) return r.to + cur.slice(r.from.length);
        return cur;
      });
      await openRoot();
      reveal(r.to);
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    }
  }, [openRoot, reveal]);

  // ── Delete ────────────────────────────────────────────────────────────────
  const doDelete = useCallback(async (path: string, kind: "file" | "dir") => {
    setActionErr(null);
    try {
      // Ask the SERVER what is in there before asking the user. The tree hides
      // dotfiles and skips node_modules/.git/dist, so a folder can render as
      // empty and hold a great deal — and "delete this empty folder" is the
      // easiest way here to destroy something you did not know existed.
      let recursive = false;
      if (kind === "dir") {
        const info = await fsEntryInfo(path);
        if (info.over_limit) {
          setActionErr(`${path} holds ${info.entries} items — too many to delete at once. `
                     + `Remove some of it first.`);
          return;
        }
        if (info.entries > 0) {
          const hidden = info.hidden_from_tree
            ? "\n\nThe file tree shows this folder as empty. It is not — it contains "
              + "hidden or ignored files (dotfiles, node_modules, build output)."
            : "";
          if (!window.confirm(
                `Delete ${path} and everything in it?\n\n`
                + `${info.entries} item(s) will be permanently removed.${hidden}\n\n`
                + `This cannot be undone.`)) return;
          recursive = true;
        } else if (!window.confirm(`Delete the empty folder ${path}?`)) {
          return;
        }
      } else {
        const t = tabsRef.current.find((x) => x.path === path);
        const unsaved = t && t.content !== t.original
          ? "\n\nIt has unsaved changes in an open tab." : "";
        if (!window.confirm(`Delete ${path}?${unsaved}\n\nThis cannot be undone.`)) return;
      }

      await fsDelete(path, recursive);

      // Close any tab on the deleted path — or under it, for a directory.
      // Leaving one open means the next autosave RECREATES the file the user
      // just deleted, which is the most confusing possible outcome.
      const gone = (p: string) => p === path || p.startsWith(path + "/");
      setTabs((prev) => {
        const next = prev.filter((t) => !gone(t.path));
        // Fall back to a REMAINING tab rather than to nothing: clearing
        // activePath while other tabs are open shows "No file open" above a
        // full tab bar.
        setActivePath((cur) => (cur && gone(cur)) ? (next[0]?.path ?? null) : cur);
        return next;
      });
      await openRoot();
    } catch (e) {
      setActionErr(e instanceof Error ? e.message : String(e));
    }
  }, [openRoot]);

  // ── Autosave ──────────────────────────────────────────────────────────────
  //
  // Deliberately NOT a keystroke-by-keystroke write. It fires once the typing
  // stops, and only for a tab that is dirty and not in conflict.
  //
  // Autosave is a real trade in THIS product: there is no undo across a
  // session, no git and no backup, so writing automatically removes the ability
  // to walk away from an edit. It is on because the alternative is worse — the
  // tile had no persistence at all, and work was lost by closing a tab — but
  // that is exactly why the conflict check above is not optional, and why a
  // failed autosave is loud rather than retried in silence.
  const AUTOSAVE_MS = 1200;
  const dirtySignature = tabs
    .filter((t) => t.content !== t.original && !t.conflict)
    .map((t) => t.path + ":" + t.content.length)
    .join("|");

  useEffect(() => {
    if (!dirtySignature) return;
    const id = window.setTimeout(() => {
      for (const t of tabsRef.current) {
        if (t.content !== t.original && !t.conflict) void saveTab(t.path);
      }
    }, AUTOSAVE_MS);
    return () => window.clearTimeout(id);
    // Keyed on the SIGNATURE, not on `tabs`: the timer must restart on every
    // edit (so it fires after a pause, not during one) but must NOT restart
    // when something unrelated to the text changes.
  }, [dirtySignature, saveTab]);

  // FIX: Ctrl/Cmd+S must call the LIVE save, not the closure captured at mount.
  const saveRef = useRef(save);
  useEffect(() => { saveRef.current = save; }, [save]);
  const onMount: OnMount = useCallback((editor, monaco) => {
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => { saveRef.current(); });
  }, []);

  const language = useMemo(() => (active ? languageFor(active.name) : "plaintext"), [active]);

  // `error` is tested BEFORE `dirty`. It used to come last, and since a save
  // error is only reachable while a tab is dirty, `dirty` always won: the
  // message read "● unsaved" and the failure never appeared at all. The class
  // below had the same ordering bug, so the error colour was unreachable too.
  const indicator = saving === "saving" ? "saving…"
    : saving === "error" ? `error: ${saveErr}`
    : saving === "saved" ? "saved"
    : dirty ? "● unsaved" : "";
  const indicatorKind = saving === "error" ? "err"
    : saving === "saved" ? "ok"
    : dirty ? "dirty" : "";

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><span className="tile-title-dot" /> Code</span>
        <span className="tile-title-meta">
          {/* `tree.name`, not the last segment of `tree.path` — the path is
              workspace-relative now and the root's is "", and when it was
              absolute this line rendered the account's UUID. */}
          {tree ? tree.name : "no folder"}
          {indicator && <span className={`code-indicator code-indicator-${indicatorKind}`}> · {indicator}</span>}
        </span>
      </div>
      <div className="tile-body tcode-body no-drag" onMouseDown={(e) => e.stopPropagation()}>
        {!tree ? (
          <div className="tcode-empty">
            <div className="tcode-empty-h">Workspace</div>
            <div className="tcode-empty-sub">Files open as tabs; Ctrl+S saves.</div>
            <div className="tcode-empty-bar">
              <input type="text" className="tcode-input" value={rootInput}
                     onChange={(e) => setRootInput(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Enter") openRoot(rootInput); }}
                     placeholder="subfolder (optional)" spellCheck={false} />
              <button type="button" className="tcode-btn" onClick={() => openRoot(rootInput)}>Open</button>
            </div>
            {treeError && <div className="tcode-error">{treeError}</div>}
          </div>
        ) : (
          <div className="tcode-split">
            <div className="tcode-tree" ref={treeRef}>
              <div className="tcode-tree-bar">
                <span className="tcode-tree-label">Files</span>
                <button type="button" className="tcode-new" title="New file"
                        onClick={() => { setNewErr(null); setNewName(""); }}>+</button>
              </div>
              {newName !== null && (
                <div className="tcode-new-row">
                  <input
                    className="tcode-new-input" autoFocus value={newName} spellCheck={false}
                    placeholder="name.ts — or folder/name.ts"
                    onChange={(e) => setNewName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") { e.preventDefault(); void createFile(); }
                      if (e.key === "Escape") { setNewName(null); setNewErr(null); }
                    }}
                    onBlur={() => { if (!newName.trim()) { setNewName(null); setNewErr(null); } }}
                  />
                </div>
              )}
              {newErr && <div className="tcode-new-err">{newErr}</div>}
              {actionErr && (
                <div className="tcode-new-err" role="alert">
                  {actionErr}
                  <button type="button" className="tcode-linkbtn" style={{ marginLeft: 6 }}
                          onClick={() => setActionErr(null)}>dismiss</button>
                </div>
              )}
              {renaming && (
                <div className="tcode-new-row">
                  <input
                    className="tcode-new-input" autoFocus value={renaming.value} spellCheck={false}
                    placeholder="new name — or folder/new-name"
                    onChange={(e) => setRenaming({ ...renaming, value: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") { e.preventDefault(); void doRename(renaming.path, renaming.value); }
                      if (e.key === "Escape") { setRenaming(null); }
                    }}
                    onBlur={() => setRenaming(null)}
                  />
                </div>
              )}
              {tree.kind === "dir" && tree.children.length === 0 && newName === null && (
                <div className="tcode-tree-empty">
                  Nothing here yet — <button type="button" className="tcode-linkbtn"
                    onClick={() => { setNewErr(null); setNewName(""); }}>create a file</button>.
                </div>
              )}
              <TreeNode node={tree} depth={0} current={activePath}
                        expanded={expanded} onToggle={toggleDir} onPick={openFile}
                        onContext={onTreeContext} />
              {menu && (
                <div className="tcode-menu" style={{ top: menu.top, left: menu.left }}
                     onClick={(e) => e.stopPropagation()}>
                  <div className="tcode-menu-path">{menu.path}</div>
                  <button type="button" className="tcode-menu-item"
                          onClick={() => { setRenaming({ path: menu.path, value: menu.path }); setMenu(null); }}>
                    Rename…
                  </button>
                  <button type="button" className="tcode-menu-item tcode-menu-danger"
                          onClick={() => { const m = menu; setMenu(null); void doDelete(m.path, m.kind); }}>
                    Delete…
                  </button>
                </div>
              )}
            </div>
            <div className="tcode-main">
              {openErr && (
                <div className="tcode-openerr">
                  {openErr}
                  <button type="button" className="tcode-openerr-x"
                          onClick={() => setOpenErr(null)} aria-label="Dismiss">×</button>
                </div>
              )}
              {/* A conflict is a DECISION, not an error, so it is not
                  dismissible: autosave stays off for this tab until one of
                  these is chosen. Both options are destructive in opposite
                  directions, so both say plainly whose work is lost. */}
              {active?.conflict && (
                <div className="tcode-conflict" role="alert">
                  <span className="tcode-conflict-msg">
                    <strong>{active.name}</strong> changed on the server since you opened it.
                  </span>
                  <button type="button" className="tcode-conflict-btn"
                          onClick={() => void openFileFresh(active.path)}>
                    Discard mine, load theirs
                  </button>
                  <button type="button" className="tcode-conflict-btn tcode-conflict-danger"
                          onClick={() => void saveTab(active.path, true)}>
                    Overwrite theirs
                  </button>
                </div>
              )}
              {tabs.length > 0 && (
                <div className="tcode-tabs">
                  {tabs.map((t) => {
                    const d = t.content !== t.original;
                    return (
                      <div key={t.path}
                           className={`tcode-tab ${t.path === activePath ? "active" : ""}`}
                           onClick={() => setActivePath(t.path)} title={t.path}>
                        <span className="tcode-tab-ic">{fileIcon(t.name)}</span>
                        <span className="tcode-tab-name">{t.name}</span>
                        <span className="tcode-tab-x" onClick={(e) => { e.stopPropagation(); closeTab(t.path); }}>
                          {d ? "●" : "×"}
                        </span>
                      </div>
                    );
                  })}
                  <button className="tcode-save" onClick={() => void save()} disabled={!dirty}
                          title="Save (Ctrl+S)">save</button>
                </div>
              )}
              <div className="tcode-editor">
                {active ? (
                  <Editor
                    height="100%"
                    path={active.path}
                    theme={resolvedTheme === "dark" ? "infinity-dark" : "infinity-light"}
                    language={language}
                    value={active.content}
                    onChange={onChange}
                    onMount={onMount}
                    // The wrapper's default loading node is unstyled black-on-
                    // transparent text. It renders BEFORE monaco exists, so the
                    // `theme` prop above cannot reach it — it has to be themed
                    // here or it flashes as an unreadable smudge.
                    loading={<div className="tcode-loading">Loading editor…</div>}
                    options={{
                      minimap: { enabled: false },
                      fontSize: 13, lineNumbers: "on", wordWrap: "on",
                      automaticLayout: true, scrollBeyondLastLine: false,
                      smoothScrolling: true, tabSize: 2,
                      bracketPairColorization: { enabled: true },
                      fontFamily: "'JetBrains Mono', Consolas, ui-monospace, monospace",
                    }}
                  />
                ) : (
                  <div className="tcode-noopen">
                    <div className="tcode-noopen-h">No file open</div>
                    <div className="tcode-noopen-sub">
                      Choose one from the tree on the left. Edits save with
                      <span className="tcode-noopen-kbd">Ctrl</span>
                      <span className="tcode-noopen-kbd">S</span>.
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
