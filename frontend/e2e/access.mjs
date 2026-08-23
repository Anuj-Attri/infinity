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

// ---- a stranger sees nothing, and a person sees the door --------------------
// No header, no cookie, no link. Every API path must be indistinguishable from a
// URL with nothing behind it, so the surface cannot be enumerated.
const strangerCtx = await browser.newContext();
const stranger = await strangerCtx.newPage();
for (const path of ["/auth/status", "/admin/diagnostics", "/vault/stats"]) {
  const r = await stranger.goto(APP + path, { waitUntil: "domcontentloaded", timeout: 45000 });
  ok(`a stranger gets 404 at ${path}`, r?.status() === 404, `status=${r?.status()}`);
}

// "/" is the exception, and deliberately so: it is where a PERSON lands, and
// serving them a raw JSON 404 made the deployment unopenable by its own owner.
{
  const r = await stranger.goto(APP + "/", { waitUntil: "networkidle", timeout: 45000 });
  ok("the root lands on the door, not a JSON 404", r?.status() === 200 && new URL(stranger.url()).pathname === "/access",
     `status=${r?.status()} path=${new URL(stranger.url()).pathname}`);
  const body = await stranger.content();
  ok("the door has somewhere to type a key", body.includes('id="k"'));
  ok("the door does not leak the key", !body.includes(TOKEN));
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
// It used to say the literal words "Not Found", which is what a dead end looks
// like. A wrong key now lands on the door with a message you can act on — the
// key is still refused, but the person is not stranded.
ok("a wrong key is refused with something actionable",
   /not accepted|try again/i.test(badBody), JSON.stringify(badBody.slice(-60)));
ok("a wrong key still leaves you outside", !badBody.includes("Ready when you are"));

await browser.close();
console.log(failures ? `\n${failures} FAILED` : "\naccess door: all checks passed");
process.exit(failures ? 1 : 0);
