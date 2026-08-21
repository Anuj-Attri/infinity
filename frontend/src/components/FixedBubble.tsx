// Fixed bubble — the single, persistent input pill at the bottom of the
// whiteboard. The ONLY place to send messages. Holds:
//   - orb (with subtle particle trail)
//   - text input (with `/tile` slash-command autocomplete)
//   - model switcher pill + no_think toggle
//   - history glyph (placeholder)
//   - settings glyph
//   - send button
//
// On submit: any `/tile` tokens trigger a context injection — the tile's
// live data is fetched and prepended to the prompt sent to Nova for that
// turn (see src/state/tileRegistry.ts).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Orb from "./Orb";
import ModelControls from "./ModelControls";
import { useChat } from "../state/ChatContext";
import { getAllTiles, subscribeExtraTiles, type TileSpec } from "../state/tileRegistry";
import { SLASH_COMMANDS } from "../state/slashCommands";
import { isMobileRuntime } from "../state/device";
import { useDictation } from "../state/useDictation";

// Unified autocomplete entry. A slash-command (leading verb, e.g. /summarize)
// and a slash-tile-reference (e.g. /notes) both render + insert the same way;
// `kind` only drives the little badge so the two are visually distinct.
type SlashItem = { id: string; label: string; description: string; kind: "command" | "tile" };

type Props = {
  onOpenSettings: () => void;
  onOpenHistory?: () => void;
  onReturnToLanding: () => void;
};

// Find the slash token "under" the cursor (or just before it). Returns
// {start, end, query} if the cursor is inside/after a clean /word token,
// where `start` is the slash position. Returns null otherwise.
function findSlashTokenAt(
  text: string,
  cursor: number,
): { start: number; end: number; query: string; leading: boolean } | null {
  const slash = text.lastIndexOf("/", cursor - 1);
  if (slash === -1) return null;
  // Must start a new token: either string start, or preceded by whitespace.
  if (slash > 0 && !/\s/.test(text[slash - 1])) return null;
  // From slash+1 to next whitespace OR end of string.
  let end = slash + 1;
  while (end < text.length && /[\w-]/.test(text[end])) end++;
  // Cursor must be within the token (slash..end inclusive).
  if (cursor < slash || cursor > end) return null;
  // Leading = this is the message's first token (only whitespace before it).
  // Only a leading token may be a slash-COMMAND (/summarize); later tokens are
  // tile references (/notes) regardless of what precedes them.
  const leading = /^\s*$/.test(text.slice(0, slash));
  return { start: slash, end, query: text.slice(slash + 1, end), leading };
}

export default function FixedBubble({ onOpenSettings, onOpenHistory, onReturnToLanding }: Props) {
  const { busy, onSubmit, modelId, setModelId, noThink, setNoThink } = useChat();
  const [value, setValue] = useState("");
  const [slashOpen, setSlashOpen] = useState(false);
  const [slashQuery, setSlashQuery] = useState("");
  const [slashLeading, setSlashLeading] = useState(false);
  const [slashIdx, setSlashIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  // Mobile-only: condensed bar (secondary controls tuck behind an overflow
  // button) + no autofocus (so the keyboard opens only on an explicit tap).
  const mobile = isMobileRuntime();

  // Voice dictation (push-to-talk). Tapping the mic captures the field as the
  // base, then live-previews interim words and commits finals after it.
  const dictBaseRef = useRef("");
  const { listening, toggle: toggleDictation, supported: micSupported } = useDictation((text, isFinal) => {
    setValue(dictBaseRef.current + (dictBaseRef.current && text ? " " : "") + text);
    if (isFinal) dictBaseRef.current = (dictBaseRef.current + (dictBaseRef.current ? " " : "") + text).trim();
  });
  const onMic = () => {
    if (!listening) dictBaseRef.current = value.trimEnd();
    toggleDictation();
  };

  // The hold-to-talk loop (record -> Whisper -> reply -> Kokoro speaks) rode on
  // `useVoice`, which needs backend voice.py. That is deferred, so the mic is
  // dictation-only for now: tap to dictate into the field, tap to stop.
  //
  // Note what survived and why: dictation is the browser's own SpeechRecognition
  // API, so it needs no backend at all. It does need a secure context — like
  // getUserMedia and navigator.clipboard, it does not exist on a plain-http
  // origin, which is one more reason everything is served over HTTPS.

  // Available tiles for the slash-autocomplete = built-in tiles +
  // any runtime extras (e.g. attached file tiles). We subscribe to
  // tileRegistry's listener so the popup picks up newly-attached files
  // without needing a manual refresh.
  const [allTiles, setAllTiles] = useState<TileSpec[]>(() => getAllTiles());
  useEffect(() => {
    const off = subscribeExtraTiles(() => setAllTiles(getAllTiles()));
    return off;
  }, []);

  // Refocus after stream completes so the cursor stays in the bubble.
  // Skipped on mobile: autofocusing here pops the soft keyboard on mount, after
  // every stream, and whenever the deep-dive closes and the bar remounts. On the
  // phone the keyboard should appear ONLY when the user taps the input.
  useEffect(() => {
    if (mobile) return;
    if (!busy) inputRef.current?.focus();
  }, [busy, mobile]);

  const filtered: SlashItem[] = useMemo(() => {
    const q = slashQuery.trim().toLowerCase();
    // Commands are only candidates for the LEADING token (the message verb).
    const cmds: SlashItem[] = slashLeading
      ? SLASH_COMMANDS.map((c) => ({ id: c.key, label: c.label, description: c.description, kind: "command" as const }))
      : [];
    const tiles: SlashItem[] = allTiles.map((t) => ({ id: t.id, label: t.label, description: t.description, kind: "tile" as const }));
    const pool = [...cmds, ...tiles];   // commands first
    if (!q) return pool;
    return pool.filter((t) =>
      t.id.startsWith(q) || t.id.includes(q) || t.label.toLowerCase().includes(q)
    );
  }, [slashQuery, slashLeading, allTiles]);

  // Reset selection when the filter list changes.
  useEffect(() => { setSlashIdx(0); }, [slashQuery]);

  const recomputeSlash = useCallback(() => {
    const el = inputRef.current;
    if (!el) { setSlashOpen(false); return; }
    const cursor = el.selectionStart ?? el.value.length;
    const tok = findSlashTokenAt(el.value, cursor);
    if (!tok) { setSlashOpen(false); return; }
    setSlashOpen(true);
    setSlashQuery(tok.query);
    setSlashLeading(tok.leading);
  }, []);

  const onChangeValue = (v: string) => {
    setValue(v);
    // Defer until after the input updates its selectionStart.
    requestAnimationFrame(recomputeSlash);
  };

  // If the user moves the cursor without typing, still sync the popup.
  const onSelectOrClick = () => {
    requestAnimationFrame(recomputeSlash);
  };

  const insertSlashChoice = (item: SlashItem) => {
    const el = inputRef.current;
    if (!el) return;
    const cursor = el.selectionStart ?? el.value.length;
    const tok = findSlashTokenAt(el.value, cursor);
    if (!tok) return;
    const insert = `/${item.id} `;
    const next = el.value.slice(0, tok.start) + insert + el.value.slice(tok.end);
    setValue(next);
    setSlashOpen(false);
    setSlashQuery("");
    // Set cursor right after the inserted token + space.
    const newCursor = tok.start + insert.length;
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(newCursor, newCursor);
    });
  };

  const submit = () => {
    const v = value.trim();
    if (!v || busy) return;
    setValue("");
    setSlashOpen(false);
    setSlashQuery("");
    setSlashLeading(false);
    // Send the RAW user text. App.startStream owns the silent injection of
    // any /slash-command tile data into Nova's prompt — the transcript only
    // ever shows what the user actually typed.
    onSubmit(v);
  };

  return (
    <div className="fixed-bubble">
      <button
        type="button"
        className="fb-orb-wrap fb-orb-btn"
        onClick={onReturnToLanding}
        title="Return to landing"
        aria-label="Return to landing"
      >
        <Orb size={36} breath="4.4s" spin="14s" trail />
      </button>

      <div className="fb-input-wrap">
        <input
          ref={inputRef}
          className="fb-input"
          type="text"
          value={value}
          placeholder="Type / for help"
          onChange={(e) => onChangeValue(e.target.value)}
          onClick={onSelectOrClick}
          onKeyUp={(e) => {
            // Arrow keys move the cursor without firing onChange — sync popup.
            if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "Home" || e.key === "End") {
              recomputeSlash();
            }
          }}
          onKeyDown={(e) => {
            // Popup navigation has priority over submit/blur when open.
            if (slashOpen && filtered.length > 0) {
              if (e.key === "ArrowDown") { e.preventDefault(); setSlashIdx((i) => (i + 1) % filtered.length); return; }
              if (e.key === "ArrowUp")   { e.preventDefault(); setSlashIdx((i) => (i - 1 + filtered.length) % filtered.length); return; }
              if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
                e.preventDefault();
                insertSlashChoice(filtered[slashIdx]);
                return;
              }
              if (e.key === "Escape") { e.preventDefault(); setSlashOpen(false); return; }
            }
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {slashOpen && (
          <div className="fb-slash-pop">
            {filtered.length === 0 ? (
              <div className="fb-slash-empty">No match for "{slashQuery}".</div>
            ) : (
              filtered.map((t, i) => (
                <button
                  type="button"
                  key={`${t.kind}:${t.id}`}
                  className={`fb-slash-item ${i === slashIdx ? "active" : ""}`}
                  onMouseDown={(e) => { e.preventDefault(); insertSlashChoice(t); }}
                  onMouseEnter={() => setSlashIdx(i)}
                >
                  <span className="fb-slash-key">{t.label}</span>
                  <span className="fb-slash-desc">{t.description}</span>
                  {t.kind === "command" && <span className="fb-slash-badge">cmd</span>}
                </button>
              ))
            )}
          </div>
        )}
      </div>

      {/* Controls row. On mobile it DOCKS as its own row in the bubble (a 2-row
          grid — see CSS), instead of the old floating popover that overlapped the
          Canvas/Feed/Focus segment + input. Desktop keeps the inline single row. */}
      <div className={`fb-controls${mobile ? " fb-controls-row" : ""}`}>
        <ModelControls
          modelId={modelId}
          setModelId={setModelId}
          noThink={noThink}
          setNoThink={setNoThink}
          disabled={busy}
        />
        {onOpenHistory && (
          <button type="button" className="fb-glyph" onClick={() => onOpenHistory()} title="History (Ctrl+H)" aria-label="History">
            <svg viewBox="0 0 14 14" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.3">
              <path d="M2 7 A5 5 0 1 0 4 3 L2 5 M2 3 V5 H4" />
              <path d="M7 4 V7 L9.5 8.5" />
            </svg>
          </button>
        )}
        <button type="button" className="fb-glyph" onClick={() => onOpenSettings()} title="Settings" aria-label="Settings">
          <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.2">
            <circle cx="8" cy="8" r="2.4" />
            <path d="M8 1.5 V3.5 M8 12.5 V14.5 M1.5 8 H3.5 M12.5 8 H14.5 M3.5 3.5 L4.9 4.9 M11.1 11.1 L12.5 12.5 M3.5 12.5 L4.9 11.1 M11.1 4.9 L12.5 3.5" />
          </svg>
        </button>
        {micSupported && (
          <button
            type="button"
            className={`fb-glyph fb-mic${listening ? " on" : ""}`}
            onClick={onMic}
            title={listening ? "Stop dictation" : "Dictate"}
            aria-label="Dictate"
            aria-pressed={listening}
          >
            <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.3">
              <rect x="6" y="1.5" width="4" height="8" rx="2" />
              <path d="M3.5 7.5 a4.5 4.5 0 0 0 9 0 M8 12 V14.5 M5.5 14.5 H10.5" strokeLinecap="round" />
            </svg>
          </button>
        )}
      </div>

      <button type="button" className="fb-send" onClick={submit} disabled={busy || !value.trim()} title="Send (Enter)" aria-label="Send">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M3 8 H13 M9 4 L13 8 L9 12" />
        </svg>
      </button>
    </div>
  );
}
