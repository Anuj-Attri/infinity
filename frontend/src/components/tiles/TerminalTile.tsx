// Terminal — placeholder until the cloud sandbox lands (M6).
//
// Mark 3's terminal was a real host PowerShell over Tauri's ConPTY: the tile
// called pty_spawn / pty_write / pty_resize through Tauri IPC and piped bytes
// into xterm.js. None of that can exist in a browser, and it should not — a
// hosted product must never hand a page a shell on the machine serving it.
//
// The replacement is not a lesser terminal, it is a different one: a per-account
// sandbox container (Modal), started on demand, with the account's cloud
// workspace synced in. The xterm.js frontend survives almost unchanged; only the
// transport moves from Tauri IPC to a streamed backend channel.
//
// Until then this says so plainly. A tile that renders an inert prompt and
// swallows every keystroke is worse than one that admits it is not connected.

export default function TerminalTile() {
  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title">Terminal</span>
        <span className="tile-title-meta">not connected</span>
      </div>
      <div className="tile-body no-drag" style={{ display: "grid", placeItems: "center", padding: "24px" }}>
        <div style={{ maxWidth: "34ch", textAlign: "center", color: "var(--ink-2)" }}>
          <p style={{ margin: "0 0 8px", color: "var(--ink-1)" }}>
            No sandbox attached
          </p>
          <p style={{ margin: 0, fontSize: "0.85em", lineHeight: 1.5 }}>
            The terminal runs against your cloud workspace, not this machine.
            It arrives with the workbench.
          </p>
        </div>
      </div>
    </div>
  );
}
