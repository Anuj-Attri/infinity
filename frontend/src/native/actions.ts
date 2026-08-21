// Copy / open seams.
//
// Mark 3 carried three branches here — Capacitor (Android WebView), Tauri
// (desktop shell), and plain browser — because it shipped as all three. Mark 4
// is a web app, so only the browser path remains.
//
// The clipboard fallback stays, and it is not paranoia: `navigator.clipboard`
// does not exist outside a secure context. https:// and http://localhost are
// secure; anything else served over plain http is not, and the API is simply
// undefined there. Serving HTTPS everywhere is what makes this a non-issue —
// the fallback covers the case where it isn't.

/** Copy text to the clipboard. Resolves true on success. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Non-secure origin, or the user denied permission. Hidden textarea +
    // execCommand still works in every current browser.
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

/** Open an external http(s) URL in a new tab.
 *  Internal links (the `tile:` scheme) must NOT come through here. */
export async function openExternal(url: string): Promise<void> {
  if (!/^https?:\/\//i.test(url)) return;
  // noopener is load-bearing, not boilerplate: without it the opened page gets
  // a live `window.opener` handle back into this one.
  window.open(url, "_blank", "noopener,noreferrer");
}

/** Fire a small app-level toast. */
export function appToast(message: string): void {
  try {
    window.dispatchEvent(new CustomEvent("infinity:toast", { detail: { message } }));
  } catch { /* ignore */ }
}

/** Copy + toast in one tap — the shared affordance handler. */
export async function copyWithToast(text: string, what = "Copied"): Promise<void> {
  const ok = await copyText(text);
  appToast(ok ? what : "Copy failed");
}
