// Infinity landing — italic-serif greeting, breathing-border input, always-on
// background pools. The orb sits inside the input as the companion anchor.

import { useEffect, useRef } from "react";
import Orb from "./Orb";
import Background from "./Background";
import { isMobileRuntime } from "../state/device";

type Props = {
  fading?: boolean;
  value: string;
  onChange: (v: string) => void;
  onSubmit: (v: string) => void;
  busy?: boolean;
};

export default function Landing({
  fading = false,
  value,
  onChange,
  onSubmit,
  busy = false,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    // On mobile, don't autofocus — it pops the soft keyboard the instant the app
    // opens. The user taps the field to start typing. Desktop autofocuses as before.
    if (isMobileRuntime()) return;
    if (!fading) {
      const t = setTimeout(() => inputRef.current?.focus(), 80);
      return () => clearTimeout(t);
    }
  }, [fading]);

  const submit = () => {
    if (busy) return;
    const v = value.trim();
    if (v) onSubmit(v);
  };

  return (
    <div className={`landing ${fading ? "fading" : ""}`}>
      <Background />
      <div className="landing-stage">
        <div className="landing-greet">
          Ready when you are.
        </div>
        <div className="landing-input-wrap">
          <span className="landing-orb" aria-hidden="true">
            <Orb size={30} breath="4.4s" spin="14s" />
          </span>
          <input
            ref={inputRef}
            type="text"
            value={value}
            placeholder="Ask anything…"
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                submit();
              }
            }}
            disabled={busy}
          />
          <button
            className="landing-send"
            onClick={submit}
            disabled={busy || !value.trim()}
            title="Send (Enter)"
            type="button"
          >
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M3 8 H13 M9 4 L13 8 L9 12" />
            </svg>
          </button>
        </div>
      </div>
      <div className="landing-foot">
        Press <kbd>Enter</kbd> to begin · <kbd>Ctrl</kbd>+<kbd>K</kbd> for commands
      </div>
    </div>
  );
}
