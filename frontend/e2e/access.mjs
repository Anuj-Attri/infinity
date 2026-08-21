// The waitlist door, driven the way a PERSON uses it.
//
// Every other test opens the app with an X-Access-Token header. That is the CI
// and curl path. The path a human gets sent is `/access#<token>` — a different
// code path (a fragment, a page that POSTs it, a cookie) that was live and
// completely untested until it was checked by hand against production.
//
// The fragment matters: `#...` is never sent to the server, so the long-lived
// door key does not land in Railway or Cloudflare access logs. `?k=` does. If
// anyone ever "simplifies" this into a query parameter, these assertions are
// what should stop them.
//
//   APP_URL=http://127.0.0.1:8000 ACCESS_TOKEN=... node e2e/access.mjs
import { chromium } from "playwright";

const APP = (process.env.APP_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
const TOKEN = process.env.ACCESS_TOKEN;
if (!TOKEN) {
  console.error("ACCESS_TOKEN is required — this test is about the door.");
  process.exit(2);
}
const isHttps = APP.startsWith("https://");

let failures = 0;
const ok = (label, cond, extra = "") => {
  if (!cond) failures++;
  console.log(`  ${cond ? "  ok  " : " FAIL "} ${label}${extra ? "  — " + extra : ""}`);
};

const browser = await chromium.launch();

// ---- a stranger sees nothing ------------------------------------------------
// No header, no cookie, no link. This must be indistinguishable from a URL with
// nothing behind it.
const strangerCtx = await browser.newContext();
const stranger = await strangerCtx.newPage();
for (const path of ["/", "/auth/status", "/admin/diagnostics"]) {
  const r = await stranger.goto(APP + path, { waitUntil: "domcontentloaded", timeout: 45000 });
  ok(`a stranger gets 404 at ${path}`, r?.status() === 404, `status=${r?.status()}`);
}

// ---- the waitlist link ------------------------------------------------------
const ctx = await browser.newContext();
const page = await ctx.newPage();
await page.goto(`${APP}/access#${TOKEN}`, { waitUntil: "networkidle", timeout: 45000 });
await page.waitForTimeout(2500);

const url = page.url();
ok("the token is gone from the address bar", !url.includes(TOKEN));
ok("it lands in the app", new URL(url).pathname === "/", `path=${new URL(url).pathname}`);
ok("the app actually rendered", (await page.content()).includes('id="root"'));

const door = (await ctx.cookies()).find((c) => c.name === "infinity_access");
ok("a door cookie was set", !!door);
if (door) {
  // An XSS that could read this would hand over the door key for every visitor.
  ok("the cookie is HttpOnly", door.httpOnly === true);
  ok("the cookie is SameSite=Lax", String(door.sameSite).toLowerCase() === "lax");
  // Secure is correct to OMIT on plain http (a Secure cookie would simply never
  // be sent, breaking local dev). It is mandatory once TLS is in front.
  if (isHttps) ok("the cookie is Secure on HTTPS", door.secure === true);
  else ok("the cookie is not Secure on plain http (correct)", door.secure === false);
}

const again = await page.goto(APP + "/", { waitUntil: "domcontentloaded", timeout: 45000 });
ok("the cookie alone keeps the door open", again?.status() === 200, `status=${again?.status()}`);

// ---- a wrong key opens nothing ---------------------------------------------
const badCtx = await browser.newContext();
const bad = await badCtx.newPage();
await bad.goto(`${APP}/access#definitely-not-the-key`, { waitUntil: "networkidle", timeout: 45000 });
await bad.waitForTimeout(2000);
ok("a wrong key sets no cookie",
   !(await badCtx.cookies()).find((c) => c.name === "infinity_access"));
const badBody = (await bad.evaluate(() => document.body.innerText)).trim();
ok("a wrong key says Not Found", /not found/i.test(badBody), JSON.stringify(badBody.slice(0, 40)));

await browser.close();
console.log(failures ? `\n${failures} FAILED` : "\naccess door: all checks passed");
process.exit(failures ? 1 : 0);
