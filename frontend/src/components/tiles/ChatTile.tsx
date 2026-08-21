// Chat tile — READ-ONLY display of the conversation. All typing happens in
// the fixed bubble at the bottom of the whiteboard; this surface is just
// where you read the back-and-forth (full markdown, scrollable, all turns).

import { useEffect, useRef } from "react";
import MarkdownView from "../MarkdownView";
import TileGlyph from "../TileGlyph";
import { useChat } from "../../state/ChatContext";

function fmtTs(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export default function ChatTile() {
  const { transcript, pending, error } = useChat();
  const scrollRef = useRef<HTMLDivElement>(null);
  // Auto-follow the stream ONLY while the user is parked at the bottom.
  // As soon as they scroll up, we stop pinning; when they scroll back to
  // the bottom, follow resumes. Ref (not state) so handlers see fresh value.
  const followRef = useRef(true);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    followRef.current = (el.scrollHeight - el.scrollTop - el.clientHeight) < 8;
  };

  useEffect(() => {
    const el = scrollRef.current;
    if (el && followRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [transcript.length, pending?.text]);

  const empty = transcript.length === 0 && !pending && !error;

  return (
    <div className="tile chat-tile">
      <div className="tile-head">
        <span className="tile-title"><TileGlyph />Chat</span>
        <span className="tile-title-meta">
          {transcript.length} {transcript.length === 1 ? "turn" : "turns"}
        </span>
      </div>

      <div className="tile-body chat-body">
        {/* Stop drag from initiating inside the scroll/text area — RGL's
            cancel selector isn't reliably defeating mousedown-drag for
            text-selection on this version. onMouseDown stopPropagation
            keeps the event away from RGL entirely. */}
        <div
          className="chat-scroll no-drag"
          ref={scrollRef}
          onScroll={onScroll}
          onMouseDown={(e) => e.stopPropagation()}
          onTouchStart={(e) => e.stopPropagation()}
        >
          {empty ? (
            <div className="chat-empty">Nothing yet. Type in the bubble below to start.</div>
          ) : (
            <>
              {transcript.map((m, i) => (
                <div key={i} className={`chat-msg chat-msg-${m.role}`}>
                  {m.role === "agent" && (
                    <div className="chat-msg-label">
                      <span className="chat-nova">Nova</span>
                      {m.model ? ` · ${m.model}` : ""} · {fmtTs(m.ts)}
                    </div>
                  )}
                  <div className="chat-msg-text md-host">
                    <MarkdownView text={m.text} />
                  </div>
                </div>
              ))}
              {pending && (
                <div className="chat-msg chat-msg-agent">
                  <div className="chat-msg-label">
                    <span className="chat-nova">Nova</span>
                    {pending.model ? ` · ${pending.model}` : ""} · streaming…
                  </div>
                  <div className="chat-msg-text md-host">
                    <MarkdownView text={pending.text} />
                    <span className="stream-caret" />
                  </div>
                </div>
              )}
              {error && (
                <div className="chat-msg chat-msg-agent">
                  <div className="chat-msg-label">error</div>
                  <pre className="chat-msg-error">{error}</pre>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
