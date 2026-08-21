// Static gate for the frontend. Runs in CI and fails the build.
//
// The backend has one (backend/tests/test_security_invariants.py); this is the
// half that was missing, and it is not the less important half — the XSS found
// today was produced by the backend but EXECUTED here, because a server string
// reaches the DOM through dangerouslySetInnerHTML.
//
//   node e2e/audit.mjs
//
// Same principle as the backend gate: the dangerous surfaces are PINNED with a
// written reason each, so widening one fails until someone edits this file and
// says why. A check nobody can silently step around.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const SRC = "src";
const failures = [];
const fail = (msg) => failures.push(msg);

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (/\.(ts|tsx)$/.test(p)) out.push(p);
  }
  return out;
}

const FILES = walk(SRC).map((p) => [relative(SRC, p).replaceAll("\\", "/"), readFileSync(p, "utf8")]);

// Comments are not code. A rule that flags a comment ABOUT a risk teaches
// people to ignore the rule.
const stripComments = (s) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

// ---------------------------------------------------------------------------
// 1. Anything that puts a string into the DOM as markup.
// ---------------------------------------------------------------------------
// file -> why this one is allowed to do it.
const HTML_SINKS = {
  "components/tiles/BrowserTile.tsx":
    "Renders page.html from browse.py. That server SANITIZES with an allowlist " +
    "(no script/iframe/on*/javascript:) and ESCAPES the non-HTML branch — the " +
    "branch that was the XSS. Never render anything here that has not been " +
    "through _clean() or html.escape().",
};

for (const [file, raw] of FILES) {
  const src = stripComments(raw);
  if (!/dangerouslySetInnerHTML/.test(src)) continue;
  if (!(file in HTML_SINKS)) {
    fail(
      `NEW HTML SINK: ${file} uses dangerouslySetInnerHTML.\n` +
      `    Every string reaching it must be sanitized SERVER-side. If that is ` +
      `true here, add it to HTML_SINKS with the reason.`,
    );
  }
}
for (const f of Object.keys(HTML_SINKS)) {
  if (!FILES.some(([p]) => p === f)) fail(`HTML_SINKS lists ${f}, which no longer exists — remove it.`);
  if (HTML_SINKS[f].length < 60) fail(`HTML_SINKS[${f}]: write an actual reason.`);
}

// ---------------------------------------------------------------------------
// 2. react-markdown must transform URLs explicitly.
// ---------------------------------------------------------------------------
// The landmine: react-markdown's default urlTransform STRIPS unknown schemes.
// The app's `tile:` links became href="" — a real navigation, an SPA remount,
// and a wiped session. Any renderer must decide this on purpose.
for (const [file, raw] of FILES) {
  const src = stripComments(raw);
  if (!/<ReactMarkdown/.test(src)) continue;
  if (!/urlTransform\s*=/.test(src)) {
    fail(
      `${file} renders <ReactMarkdown> without urlTransform.\n` +
      `    The default strips custom schemes, so a tile: link becomes href="" — ` +
      `a real navigation that remounts the SPA and wipes the session.`,
    );
  }
}

// ---------------------------------------------------------------------------
// 3. The browser talks to our backend, and to nothing else.
// ---------------------------------------------------------------------------
// A direct third-party call hands the user's IP to a stranger from their own
// browser, cannot be cached or rate-limited by us, and dies under any CSP worth
// having. Upstream redundancy belongs server-side: one request from one address,
// not one per user.
for (const [file, raw] of FILES) {
  const src = stripComments(raw);
  for (const m of src.matchAll(/fetch\(\s*[`"']((?:https?:)?\/\/[^`"'\s]+)/g)) {
    fail(`${file} fetches a third party directly: ${m[1].slice(0, 60)}\n    Route it through the backend.`);
  }
  // A hardcoded ROUTABLE host is how Mark 3 shipped a Tailscale IP that worked
  // for exactly one person, over plain http, which has no secure context.
  //
  // Loopback is deliberately allowed: api.ts falls back to 127.0.0.1:8000 for
  // local development, which is correct and is not shipped anywhere. Flagging it
  // would make this rule cry wolf on the one legitimate case.
  for (const m of src.matchAll(/["'`]https?:\/\/(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})[^"'`]*/g)) {
    const host = m[1];
    if (host === "127.0.0.1" || host === "0.0.0.0") continue;
    fail(`${file} hardcodes a routable host: ${m[0].slice(0, 50)}`);
  }
}

// ---------------------------------------------------------------------------
// 4. Only the API client knows the backend's address.
// ---------------------------------------------------------------------------
for (const [file, raw] of FILES) {
  if (file === "api.ts") continue;
  if (/\bVITE_API_BASE_URL\b/.test(stripComments(raw))) {
    fail(`${file} reads VITE_API_BASE_URL. Resolution lives in api.ts only.`);
  }
}

// ---------------------------------------------------------------------------
// 5. Nothing secret-shaped is committed or stored.
// ---------------------------------------------------------------------------
const SECRET_SHAPES = [
  [/\bsk-[A-Za-z0-9\-_]{20,}/, "an API key"],
  [/\bsbp_[A-Za-z0-9]{20,}/, "a Supabase key"],
  [/eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\./, "a JWT"],
];
for (const [file, raw] of FILES) {
  for (const [re, what] of SECRET_SHAPES) {
    if (re.test(raw)) fail(`${file} appears to contain ${what}.`);
  }
}

// A provider key must never reach the browser: the bundle is public, and a key
// in it is a key in every user's devtools. Session tokens are different — they
// are per-device credentials and belong in storage.
for (const [file, raw] of FILES) {
  const src = stripComments(raw);
  for (const m of src.matchAll(/localStorage\.setItem\(\s*([^,]+)/g)) {
    const key = m[1];
    if (/api[_-]?key|openrouter|secret|provider[_-]?key/i.test(key)) {
      fail(`${file} stores something key-shaped in localStorage: ${key.trim().slice(0, 50)}`);
    }
  }
}

// ---------------------------------------------------------------------------
// 6. Nothing may reach back into the desktop shell.
// ---------------------------------------------------------------------------
// Tauri and Capacitor are gone. An import that survives is dead weight at best
// and, if it is static, breaks the browser bundle outright — which is exactly
// how WindowControls.tsx would have.
for (const [file, raw] of FILES) {
  const src = stripComments(raw);
  if (/@tauri-apps|@capacitor/.test(src)) {
    fail(`${file} imports a desktop-shell package. Those are removed; the web build has no shell.`);
  }
}

// ---------------------------------------------------------------------------

const checks = [
  "HTML sinks pinned",
  "react-markdown urlTransform",
  "no direct third-party fetch",
  "no hardcoded hosts",
  "backend URL only in api.ts",
  "no secrets in source or storage",
  "no desktop-shell imports",
];
console.log(`frontend audit — ${FILES.length} source files\n`);
for (const c of checks) console.log(`  · ${c}`);

if (failures.length) {
  console.error(`\n${"=".repeat(64)}\nFAILED (${failures.length}):\n`);
  for (const f of failures) console.error("  " + f + "\n");
  process.exit(1);
}
console.log(`\n${"=".repeat(64)}\nclean.`);
