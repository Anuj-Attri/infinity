// Scratchpad. Persists to localStorage across sessions.

import { useEffect, useRef, useState } from "react";
import { onTileWrite } from "../../state/tileActions";
import TileGlyph from "../TileGlyph";

const KEY = "supernova.notes.v1"; // key kept for backwards compatibility

export default function NotesTile() {
  const [text, setText] = useState<string>(() => {
    try { return localStorage.getItem(KEY) ?? ""; } catch { return ""; }
  });
  const writeTimer = useRef<number | null>(null);

  useEffect(() => {
    if (writeTimer.current) window.clearTimeout(writeTimer.current);
    writeTimer.current = window.setTimeout(() => {
      try { localStorage.setItem(KEY, text); } catch { /* ignore */ }
    }, 250);
    return () => {
      if (writeTimer.current) window.clearTimeout(writeTimer.current);
    };
  }, [text]);

  // Reflect external writes (Nova's write_tile) live.
  useEffect(() => onTileWrite("notes", () => {
    try { setText(localStorage.getItem(KEY) ?? ""); } catch { /* ignore */ }
  }), []);

  return (
    <div className="tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Notes</span>
        <span className="tile-title-meta">{text.length} chars</span>
      </div>
      <div
        className="tile-body tnotes-body no-drag"
        onMouseDown={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        <textarea
          className="tnotes-area"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Scratchpad"
          spellCheck={false}
        />
      </div>
    </div>
  );
}
