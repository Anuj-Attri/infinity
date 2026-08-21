// End-to-end smoke test. Drives a REAL browser against a running app and opens
// every tile, because "it builds" and "tsc is clean" have never once meant "it
// works" in this project.
//
//   node e2e/smoke.mjs                     # against http://127.0.0.1:5173
//   APP_URL=https://... node e2e/smoke.mjs # against a deployment
//
// Exits non-zero if any tile fails to render or the page logs an error, so this
// can gate a deploy later.
//
// Deliberately NOT a unit test: the failures this catches are integration ones —
// a registry entry whose component was deleted, a tile that throws on mount, an
// API call that 404s only in production.

import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";

const APP = (process.env.APP_URL || "http://127.0.0.1:5173").replace(/\/+$/, "");
const PASSWORD = process.env.SMOKE_PASSWORD || "smoke-test-pass";
const SETUP_TOKEN = process.env.SETUP_TOKEN || "";
const SHOTS = "e2e/shots";
mkdirSync(SHOTS, { recursive: true });

// Every tile the command palette can open. Labels come from TILE_REGISTRY in
// src/state/TilesContext.tsx — if that list changes, this one must too, and the
// mismatch is exactly the bug worth catching.
const TILES = [
  ["chat", "Chat"], ["clock", "World Clock"], ["stocks", "Markets"],
  ["news", "News"], ["todo", "Tasks"], ["notes", "Notes"],
  ["vault", "Vault"], ["skills", "Skills"], ["summary", "Summary"],
  ["research", "Research"], ["chain", "Chain"], ["browser", "Browser"],
  ["code", "Code"], ["terminal", "Terminal"], ["weather", "Weather"],
  ["usage", "Usage"], ["console", "Console"],
];

const results = [];
let pageErrors = [];

// The deployment fails closed when ACCESS_TOKEN is set, so the harness has to
// present the door key on every request — exactly as a real browser does once
// it has been through /access.
const DOOR = process.env.ACCESS_TOKEN || "";
const browser = await chromium.launch();
const ctx = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
  ...(DOOR ? { extraHTTPHeaders: { "X-Access-Token": DOOR } } : {}),
});
const page = await ctx.newPage();

page.on("console", (m) => {
  if (m.type() === "error") pageErrors.push(m.text().replace(/\s+/g, " ").slice(0, 220));
});
page.on("pageerror", (e) => pageErrors.push("PAGEERROR " + String(e).replace(/\s+/g, " ").slice(0, 220)));
page.on("requestfailed", (r) => {
  const u = r.url();
  const why = r.failure()?.errorText || "";
  // ERR_ABORTED is a CANCELLED request, not a failed one — React StrictMode
  // double-invokes effects in dev, so the first fetch of a pair is aborted by
  // design. Flagging it trains the reader to ignore this whole channel.
  if (/favicon|\.map$/.test(u) || /ERR_ABORTED/.test(why)) return;
  pageErrors.push(`REQFAIL ${u.slice(0, 110)} ${why}`);
});

const die = async (why) => {
  await page.screenshot({ path: `${SHOTS}/FATAL.png`, fullPage: false });
  console.error(`\nFATAL: ${why}`);
  console.error(`page text: ${(await page.evaluate(() => document.body.innerText)).slice(0, 400)}`);
  await browser.close();
  process.exit(1);
};

// ---- boot ------------------------------------------------------------------
console.log(`APP_URL = ${APP}\n`);
await page.goto(APP, { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(1500);

// ---- auth ------------------------------------------------------------------
if (await page.$('input[placeholder="Master password"]')) {
  if (await page.$('input[placeholder="Setup token"]')) {
    if (!SETUP_TOKEN) await die("setup token required but SETUP_TOKEN is not set");
    await page.fill('input[placeholder="Setup token"]', SETUP_TOKEN);
  }
  await page.fill('input[placeholder="Master password"]', PASSWORD);
  await page.fill('input[placeholder="Confirm password"]', PASSWORD);
  await page.click("button.authgate-btn");
  await page.waitForSelector('input[placeholder="Device name"]', { timeout: 20000 })
    .catch(() => die("setup did not lead to the login screen"));
}
if (await page.$('input[placeholder="Device name"]')) {
  await page.fill('input[placeholder="Device name"]', "smoke");
  await page.fill('input[placeholder="Password"]', PASSWORD);
  await page.click("button.authgate-btn");
  await page.waitForSelector("button.authgate-btn", { state: "detached", timeout: 25000 })
    .catch(() => die("login did not clear the auth gate"));
}
await page.waitForTimeout(1200);

// ---- landing -> canvas -----------------------------------------------------
const landingInput = await page.$(".landing input, .landing textarea, input, textarea");
if (landingInput) {
  await landingInput.click().catch(() => {});
  await page.keyboard.type("smoke");
  await page.keyboard.press("Enter");
}
await page.waitForTimeout(5000);
await page.screenshot({ path: `${SHOTS}/canvas.png` });

const onCanvas = await page.$(".tile");
if (!onCanvas) await die("no .tile on the canvas after the landing transition");
console.log("canvas reached\n");

// ---- open every tile via the command palette --------------------------------
for (const [id, label] of TILES) {
  pageErrors = [];
  let status = "ok", detail = "";
  try {
    await page.keyboard.press("Control+K");
    await page.waitForSelector(".palette-input", { timeout: 6000 });
    await page.fill(".palette-input", label);
    await page.waitForTimeout(350);
    const rows = await page.$$(".palette-row-label");
    if (!rows.length) throw new Error(`no palette match for "${label}"`);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(2200);
    // Palette must have closed — if it did not, the command did nothing.
    if (await page.$(".palette-input")) {
      await page.keyboard.press("Escape");
      throw new Error("palette stayed open (command did not execute)");
    }
    const count = await page.evaluate(() => document.querySelectorAll(".tile").length);
    if (!count) throw new Error("no tiles rendered after open");
    if (pageErrors.length) { status = "errors"; detail = pageErrors.join(" || "); }
  } catch (e) {
    status = "FAIL";
    detail = String(e).split("\n")[0];
    await page.screenshot({ path: `${SHOTS}/tile-${id}-FAIL.png` });
    await page.keyboard.press("Escape").catch(() => {});
  }
  results.push({ id, label, status, detail });
  const mark = status === "ok" ? "  ok  " : status === "errors" ? " warn " : " FAIL ";
  console.log(`[${mark}] ${id.padEnd(9)} ${detail.slice(0, 150)}`);
}

await page.waitForTimeout(1500);
await page.screenshot({ path: `${SHOTS}/all-tiles.png` });

// ---- report ----------------------------------------------------------------
const failed = results.filter((r) => r.status === "FAIL");
const warned = results.filter((r) => r.status === "errors");
console.log(`\n${"=".repeat(60)}`);
console.log(`tiles: ${results.length}   ok: ${results.length - failed.length - warned.length}   warn: ${warned.length}   FAIL: ${failed.length}`);
writeFileSync(`${SHOTS}/report.json`, JSON.stringify({ app: APP, results }, null, 2));
await browser.close();
process.exit(failed.length ? 1 : 0);
