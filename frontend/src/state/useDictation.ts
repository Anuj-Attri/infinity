// Push-to-talk voice dictation via the platform Web Speech API
// (SpeechRecognition / webkitSpeechRecognition). EXPLICIT only: recognition
// starts on a tap and auto-stops at end-of-utterance — NEVER ambient/always-on
// (the hard mic rule). No new dependency; degrades to unsupported (button
// hidden) where the API isn't present.
//
// Path note: this is platform STT, not a Wispr Flow SDK — Wispr Flow ships a
// desktop app + (waitlisted) API, with no public browser/Capacitor SDK to embed,
// so the platform speech engine is the functional equivalent. On an Android
// WebView that lacks Web Speech, the @capacitor-community/speech-recognition
// native plugin is the drop-in upgrade behind this same hook.

import { useCallback, useEffect, useRef, useState } from "react";

/* eslint-disable @typescript-eslint/no-explicit-any */
function getSR(): any {
  if (typeof window === "undefined") return null;
  return (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition || null;
}

export function dictationSupported(): boolean {
  return !!getSR();
}

/** onText(text, isFinal): `text` is the running transcript for the current
 *  utterance (interim until isFinal). The caller composes it into the field. */
export function useDictation(onText: (text: string, isFinal: boolean) => void) {
  const [listening, setListening] = useState(false);
  const recRef = useRef<any>(null);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  const stop = useCallback(() => {
    try { recRef.current?.stop(); } catch { /* ignore */ }
  }, []);

  const start = useCallback(() => {
    const SR = getSR();
    if (!SR || recRef.current) return;
    const rec = new SR();
    rec.lang = (typeof navigator !== "undefined" && navigator.language) || "en-US";
    rec.interimResults = true;
    rec.continuous = false;   // ONE utterance → auto-stops on silence (push-to-talk)
    rec.onresult = (e: any) => {
      let interim = "", final = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) final += t; else interim += t;
      }
      if (final) onTextRef.current(final.trim(), true);
      else if (interim) onTextRef.current(interim.trim(), false);
    };
    const end = () => { setListening(false); recRef.current = null; };
    rec.onend = end;
    rec.onerror = end;
    recRef.current = rec;
    try { rec.start(); setListening(true); } catch { end(); }
  }, []);

  const toggle = useCallback(() => {
    if (recRef.current) stop(); else start();
  }, [start, stop]);

  // Release the mic if the component unmounts mid-listen.
  useEffect(() => () => { try { recRef.current?.abort?.(); } catch { /* ignore */ } }, []);

  return { listening, toggle, supported: dictationSupported() };
}
