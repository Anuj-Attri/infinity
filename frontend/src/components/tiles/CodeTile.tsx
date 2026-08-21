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
    setSaveErr(null); setSaving("idle");
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
    } catch (e) { setSaveErr(e instanceof Error ? e.message : String(e)); }
  }, [tabs]);

  const closeTab = useCallback((path: string) => {
    setTabs((prev) => {
      const idx = prev.findIndex((t) => t.path === path);
      const next = prev.filter((t) => t.path !== path);
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

  const indicator = saving === "saving" ? "saving…"
    : saving === "saved" ? "saved"
    : saving === "error" ? `error: ${saveErr}`
    : dirty ? "● unsaved" : "";

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><span className="tile-title-dot" /> Code</span>
        <span className="tile-title-meta">
          {tree ? tree.path.split(/[\\/]/).pop() : "no folder"}
          {indicator && <span className={`code-indicator code-indicator-${saving === "saved" ? "ok" : dirty ? "dirty" : saving === "error" ? "err" : ""}`}> · {indicator}</span>}
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
              <TreeNode node={tree} depth={0} current={activePath} onPick={openFile} />
            </div>
            <div className="tcode-main">
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
                    theme={resolvedTheme === "dark" ? "vs-dark" : "vs"}
                    language={language}
                    value={active.content}
                    onChange={onChange}
                    onMount={onMount}
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
                  <div className="tcode-noopen">Pick a file from the tree.</div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
