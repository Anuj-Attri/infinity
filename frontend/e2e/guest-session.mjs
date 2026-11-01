// Guest sessions in a real browser: run against a backend started with
// GUEST_SESSIONS=1 that also serves the built frontend.
//
//   BASE=http://127.0.0.1:8791 node e2e/guest-session.mjs
//
// What it proves, which no unit test can: two tabs are two accounts, a reload
// keeps its session, nothing lands in persistent browser storage, and closing
// a tab really does delete its data on the server. The last step waits out the
// server's grace period and sweep interval, so it takes over a minute.

import { chromium } from "playwright";

const BASE = (process.env.BASE || "http://127.0.0.1:8791").replace(/\/+$/, "");
const TOKEN_KEY = "infinity.auth.token.v1";
const fails = [];
const check = (ok, what) => { console.log(`${ok ? "PASS" : "FAIL"}  ${what}`); if (!ok) fails.push(what); };

async function api(token, method, path, body) {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return { status: r.status, json: r.headers.get("content-type")?.includes("json") ? await r.json() : null };
}

async function openTab(ctx) {
  const page = await ctx.newPage();
  await page.goto(BASE);
  await page.waitForSelector(".guest-notice", { timeout: 20000 });
  const token = await page.evaluate((k) => sessionStorage.getItem(k), TOKEN_KEY);
  return { page, token };
}

// The real localStorage, past the guest shim that redirects it. A blank iframe
// shares the origin but not the shim, so its localStorage is the real one.
const realLocalStorageAppKeys = (page) => page.evaluate(() => {
  const f = document.createElement("iframe");
  document.body.appendChild(f);
  const real = f.contentWindow.localStorage;
  const keys = Object.keys(real).filter((k) => k.startsWith("infinity.") || k.startsWith("supernova."));
  f.remove();
  return keys;
});

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext();
  const a = await openTab(ctx);
  check(!!a.token, "tab A got a guest session without signing in");
  check(await a.page.evaluate(() => window.__INFINITY_GUEST__ === true),
        "the storage shim loaded before the app");
  check(/deleted when you close this tab/.test(await a.page.textContent(".guest-notice")),
        "the guest notice is visible");

  check((await api(a.token, "PUT", "/profile", { display_name: "Alice" })).status === 200,
        "tab A can write");
  check((await api(a.token, "POST", "/fs/write",
        { path: "alice.py", content: "print(1)\n", expect_version: "" })).status === 200,
        "tab A can save a file");

  const b = await openTab(ctx);
  check(b.token && b.token !== a.token, "tab B is a different session");
  check((await api(b.token, "GET", "/profile")).json?.display_name === "",
        "tab B cannot see tab A's name");
  check(!JSON.stringify((await api(b.token, "GET", "/fs/tree")).json).includes("alice"),
        "tab B cannot see tab A's files");
  check((await api(b.token, "GET", "/admin/diagnostics")).status === 403,
        "a guest is refused operator routes");

  await a.page.reload();
  await a.page.waitForSelector(".guest-notice");
  const afterReload = await a.page.evaluate((k) => sessionStorage.getItem(k), TOKEN_KEY);
  check(afterReload === a.token, "a reload keeps the same session");
  check((await api(a.token, "GET", "/profile")).json?.display_name === "Alice",
        "a reload keeps the session's data");

  check((await realLocalStorageAppKeys(b.page)).length === 0,
        "nothing was written to persistent browser storage");

  await a.page.close({ runBeforeUnload: true });
  // Do NOT poll with tab A's token while waiting. Any request carrying it
  // cancels the pending close, because that is exactly what a reload looks
  // like. Wait out the 45 s grace period and one 30 s sweep, then ask once.
  console.log("      waiting 90 s for the server to delete tab A (grace period + sweep)...");
  await new Promise((r) => setTimeout(r, 90_000));
  check((await api(a.token, "GET", "/profile")).status === 401,
        "closing tab A deleted its session on the server");
  check((await api(b.token, "GET", "/profile")).status === 200,
        "tab B is unaffected by tab A closing");
} finally {
  await browser.close();
}

if (fails.length) { console.error(`\n${fails.length} failed`); process.exit(1); }
console.log("\nall guest-session checks passed");
