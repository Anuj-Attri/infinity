// Code tile — Monaco editor + filesystem tree + multi-file tabs.
//
//   folder → left tree; click a file → opens a TAB. Multiple tabs, each with
//   its own unsaved-edits state. Ctrl/Cmd+S (or the Save button) writes the
//   ACTIVE tab via /fs/write and clears its dirty dot. Cursor/PyCharm bar.
//
// The old save silently no-op'd: onMount bound Ctrl+S to a stale `save`
// closure captured when no file was open. Fixed here via saveRef.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
// Side-effect import, and it must come before the first <Editor> mounts: it
// points the loader at our bundled Monaco instead of a CDN, registers the web
// workers, and defines the two themes below. See lib/monacoSetup.ts.
import "../../lib/monacoSetup";
import { useSettings } from "../../state/SettingsContext";
import { fsTree, fsRead, fsWrite } from "../../api";

type FsNode =
  | { name: string; path: string; kind: "dir"; children: FsNode[] }
  | { name: string; path: string; kind: "file"; size: number };

type Tab = { path: string; name: string; content: string; original: string };

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

function TreeNode({ node, depth, current, onPick }: {
  node: FsNode; depth: number; current: string | null; onPick: (path: string) => void;
}) {
  const [open, setOpen] = useState(depth === 0);
  const pad = { paddingLeft: 8 + depth * 12 };
  if (node.kind === "dir") {
    return (
      <div className="code-tree-dir">
        <div className="code-tree-row code-tree-dirrow" style={pad} onClick={() => setOpen((v) => !v)}>
          <span className="code-tree-chev">{open ? "▾" : "▸"}</span>
          <span className="code-tree-name">{node.name || "/"}</span>
        </div>
        {open && (
          <div className="code-tree-children">
            {node.children.map((c) => (
              <TreeNode key={c.path} node={c} depth={depth + 1} current={current} onPick={onPick} />
            ))}
          </div>
        )}
      </div>
    );
  }
  const active = current === node.path;
  return (
    <div className={`code-tree-row code-tree-filerow ${active ? "active" : ""}`} style={pad}
         onClick={() => onPick(node.path)} title={node.path}>
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

  const active = tabs.find((t) => t.path === activePath) || null;
  const dirty = !!active && active.content !== active.original;

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
    setTabs((prev) => (prev.some((t) => t.path === path) ? prev : prev));  // no-op guard
    // focus if already open
    if (tabs.some((t) => t.path === path)) { setActivePath(path); return; }
    try {
      const body = await fsRead(path);
      const name = path.split(/[\\/]/).pop() || path;
      setTabs((prev) => prev.some((t) => t.path === body.path)
        ? prev
        : [...prev, { path: body.path, name, content: body.content, original: body.content }]);
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
  }, [tabs]);

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
      const leaf = name.split("/").pop() || name;
      setTabs((prev) => prev.some((t) => t.path === r.path)
        ? prev
        : [...prev, { path: r.path, name: leaf, content: "", original: "" }]);
      setActivePath(r.path);
    } catch (e) {
      setNewErr(e instanceof Error ? e.message : String(e));
    }
  }, [newName, openRoot]);

  // Read through a ref so the confirm below can see the CURRENT tabs without
  // putting `tabs` in the dependency list. This file already documents the
  // stale-closure bug that pattern caused for Ctrl+S; the same trap is one
  // `tabs.find(...)` away here.
  const tabsRef = useRef(tabs);
  useEffect(() => { tabsRef.current = tabs; }, [tabs]);

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

  const save = useCallback(async () => {
    const t = tabs.find((x) => x.path === activePath);
    if (!t || t.content === t.original) return;
    setSaving("saving"); setSaveErr(null);
    try {
      await fsWrite(t.path, t.content);
      setTabs((prev) => prev.map((x) => x.path === t.path ? { ...x, original: t.content } : x));
      setSaving("saved");
      window.setTimeout(() => setSaving("idle"), 1400);
    } catch (e) {
      setSaving("error");
      setSaveErr(e instanceof Error ? e.message : String(e));
    }
  }, [tabs, activePath]);

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
            <div className="tcode-tree">
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
              {tree.kind === "dir" && tree.children.length === 0 && newName === null && (
                <div className="tcode-tree-empty">
                  Nothing here yet — <button type="button" className="tcode-linkbtn"
                    onClick={() => { setNewErr(null); setNewName(""); }}>create a file</button>.
                </div>
              )}
              <TreeNode node={tree} depth={0} current={activePath} onPick={openFile} />
            </div>
            <div className="tcode-main">
              {openErr && (
                <div className="tcode-openerr">
                  {openErr}
                  <button type="button" className="tcode-openerr-x"
                          onClick={() => setOpenErr(null)} aria-label="Dismiss">×</button>
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
