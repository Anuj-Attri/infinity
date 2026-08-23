// The editor must work with the rest of the internet switched off.
//
// `@monaco-editor/react` fetches the entire editor engine from
// `cdn.jsdelivr.net` unless `loader.config()` has been called, and it never had
// been. The shipped bundle carried the URL. That meant:
//
//   * the editor did not work offline, behind a strict CSP, or anywhere
//     jsdelivr is blocked — and it fails by HANGING on a blank pane, with no
//     timeout and no error, which is indistinguishable from a broken app;
//   * a third party could serve arbitrary script into a product sold on the
//     promise that your files and your company's documents are safe in it.
//
// Reasoning about it is not enough. The loader's default config object is still
// in the bundle as a dead constant, so grepping dist/ for the hostname says
// nothing about whether a request is actually made. The only honest test is to
// forbid the network and see whether the editor still works.
//
// This test therefore ABORTS every request that leaves the app's own origin,
// records it, and then requires BOTH:
//   * Monaco actually mounted and is editable — otherwise "no external
//     requests" passes trivially against an editor that never started; and
//   * nothing was blocked.
//
// Run:  APP_URL=http://127.0.0.1:8000 SETUP_TOKEN=... node e2e/editor-offline.mjs
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const APP = (process.env.APP_URL || "http://127.0.0.1:5173").replace(/\/+$/, "");
const PASSWORD = process.env.SMOKE_PASSWORD || "smoke-test-pass";
const SETUP_TOKEN = process.env.SETUP_TOKEN || "";
const DOOR = process.env.ACCESS_TOKEN || "";
const ORIGIN = new URL(APP).origin;
const SHOTS = "e2e/shots";
mkdirSync(SHOTS, { recursive: true });

let failures = 0;
const ok = (label, cond, extra = "") => {
  if (!cond) failures++;
  console.log(`  ${cond ? "  ok  " : " FAIL "} ${label}${extra ? "  — " + extra : ""}`);
};

const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1500, height: 950 },
  ...(DOOR ? { extraHTTPHeaders: { "X-Access-Token": DOOR } } : {}),
});

// The internet, switched off. Anything not on our own origin is aborted and
// recorded. data: and blob: are same-document and never hit the network.
const blocked = [];
await ctx.route("**/*", (route) => {
  const url = route.request().url();
  if (url.startsWith("data:") || url.startsWith("blob:") || url.startsWith(ORIGIN)) {
    return route.continue();
  }
  blocked.push(url);
  return route.abort();
});

const page = await ctx.newPage();
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e).slice(0, 200)));

await page.goto(APP, { waitUntil: "networkidle", timeout: 45000 });
await page.waitForTimeout(1500);

// First run claims the instance; afterwards this is a sign-in. Same sequence as
// functional.mjs — the selectors are specific because the two forms share a
// submit button and a generic input[type=password] matches three fields.
if (await page.$('input[placeholder="Master password"]')) {
  if (await page.$('input[placeholder="Setup token"]') && SETUP_TOKEN) {
    await page.fill('input[placeholder="Setup token"]', SETUP_TOKEN);
  }
  await page.fill('input[placeholder="Master password"]', PASSWORD);
  await page.fill('input[placeholder="Confirm password"]', PASSWORD);
  await page.click("button.authgate-btn");
  await page.waitForSelector('input[placeholder="Device name"]', { timeout: 20000 });
}
if (await page.$('input[placeholder="Device name"]')) {
  await page.fill('input[placeholder="Device name"]', "offline");
  await page.fill('input[placeholder="Password"]', PASSWORD);
  await page.click("button.authgate-btn");
  await page.waitForSelector("button.authgate-btn", { state: "detached", timeout: 25000 });
}
await page.waitForTimeout(1200);

// Get onto the canvas.
if (!(await page.$(".tile"))) {
  const input = await page.$("input, textarea");
  if (input) {
    await input.click().catch(() => {});
    await page.keyboard.type("go");
    await page.keyboard.press("Enter");
  }
  await page.waitForTimeout(5000);
}
ok("the canvas is up", !!(await page.$(".tile")));

// ---- open the Code tile ----------------------------------------------------
// Via the command palette, which is how a person does it.
await page.keyboard.press("Escape").catch(() => {});
await page.keyboard.press("Control+K");
await page.waitForSelector(".palette-input", { timeout: 10000 });
await page.fill(".palette-input", "code");
await page.waitForTimeout(600);
await page.keyboard.press("Enter");

// The tile is lazily loaded and the chunk is several MB, so this is a real wait.
await page.waitForSelector(".tcode-body", { timeout: 30000 });
ok("the Code tile opened", true);

// ---- it must have something to open ---------------------------------------
const NAME = `offline-${Date.now().toString().slice(-6)}.ts`;
await page.waitForSelector(".tcode-new", { timeout: 15000 });
await page.click(".tcode-new");
await page.waitForSelector(".tcode-new-input", { timeout: 8000 });
await page.fill(".tcode-new-input", NAME);
await page.keyboard.press("Enter");

// ---- Monaco itself ---------------------------------------------------------
// `.monaco-editor` only exists once the engine has loaded and mounted. If the
// bundle still went to a CDN, the request above was aborted and this never
// appears — which is exactly the assertion we want.
let mounted = false;
try {
  await page.waitForSelector(".monaco-editor", { timeout: 40000 });
  mounted = true;
} catch { /* reported below */ }
ok("Monaco mounted with no network access", mounted,
   mounted ? "" : "the editor never appeared — it is still fetching from a CDN");

if (mounted) {
  // Rendering is not working. Type into it and read the value back, so a
  // half-initialised editor cannot pass.
  await page.click(".monaco-editor .view-lines").catch(() => {});
  await page.keyboard.type("export const x = 1;");
  await page.waitForTimeout(800);
  const raw = await page.$eval(".monaco-editor .view-lines", (el) => el.innerText).catch(() => "");
  // Monaco renders spaces as U+00A0, so a naive includes() on a string with
  // ordinary spaces fails against text that is visibly identical. Normalise
  // every kind of space to one before comparing.
  const text = raw.replace(/\s+/g, " ").trim();
  ok("it accepts typing", text.includes("export const x = 1;"), JSON.stringify(text.slice(0, 48)));

  // Ctrl+S must reach the backend and clear the dirty state. Asserted on the
  // save button's disabled-ness rather than by scraping header text: the
  // button is disabled exactly when there is nothing unsaved, so this is the
  // app's own answer to "did it save", and it cannot match another tile.
  await page.keyboard.press("Control+KeyS").catch(() => {});
  await page.keyboard.press("Control+S").catch(() => {});
  let clean = false;
  for (let i = 0; i < 20; i++) {
    clean = await page.$eval(".tcode-save", (el) => el.disabled).catch(() => false);
    if (clean) break;
    await page.waitForTimeout(400);
  }
  const codeHead = await page
    .$eval(".tcode-body", (el) => el.closest(".tile")?.querySelector(".tile-head")?.innerText || "")
    .catch(() => "");
  ok("Ctrl+S saves and clears the unsaved marker", clean, JSON.stringify(codeHead.slice(0, 70)));
  ok("saving reported no error", !/error/i.test(codeHead), JSON.stringify(codeHead.slice(0, 70)));
}

// ---- and nothing left the origin ------------------------------------------
// Checked LAST: on its own it would pass against an editor that never started,
// which is why the mount assertion above is not optional.
const external = [...new Set(blocked)];
ok("no request left the app's origin", external.length === 0,
   external.length ? external.slice(0, 4).join(", ") : "");

ok("no uncaught page errors", pageErrors.length === 0,
   pageErrors.slice(0, 2).join(" | "));

if (failures) {
  await page.screenshot({ path: `${SHOTS}/editor-offline.png` }).catch(() => {});
}
await browser.close();
console.log(failures ? `\n${failures} FAILED` : "\neditor works with no network: all checks passed");
process.exit(failures ? 1 : 0);
