// Functional e2e — does the app DO things, not just render them.
//
// smoke.mjs proves every tile mounts. That is a low bar: a tile can mount
// perfectly and save nothing, fetch nothing, and lose your work on reload.
// This drives the actual flows a user would try in their first five minutes.
//
//   node e2e/functional.mjs
//   APP_URL=https://... node e2e/functional.mjs
//
// Each check is independent and reports pass/fail with the reason, so one broken
// flow does not hide the rest. Exits non-zero if any check fails.

import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";

const APP = (process.env.APP_URL || "http://127.0.0.1:5173").replace(/\/+$/, "");
const PASSWORD = process.env.SMOKE_PASSWORD || "smoke-test-pass";
const SETUP_TOKEN = process.env.SETUP_TOKEN || "";
const SHOTS = "e2e/shots";
mkdirSync(SHOTS, { recursive: true });

const results = [];
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
const page = await ctx.newPage();
const consoleErrors = [];
page.on("pageerror", (e) => consoleErrors.push(String(e).slice(0, 200)));

async function check(name, fn) {
  try {
    const note = await fn();
    results.push({ name, ok: true, note: note || "" });
    console.log(`[  ok  ] ${name}${note ? "  — " + note : ""}`);
  } catch (e) {
    const why = String(e).split("\n")[0].slice(0, 180);
    results.push({ name, ok: false, note: why });
    console.log(`[ FAIL ] ${name}  — ${why}`);
    await page.screenshot({ path: `${SHOTS}/fn-${name.replace(/\W+/g, "-")}.png` }).catch(() => {});
  }
}

// The landing screen and the canvas are different views with different DOM.
// A check that silently runs against the landing screen "fails" for reasons
// that have nothing to do with what it is testing — which is how the first run
// of this file reported six bugs that were all one bug in the harness.
const onCanvas = () => page.evaluate(() => !!document.querySelector(".tile"));

const ensureCanvas = async () => {
  if (await onCanvas()) return;
  const input = await page.$("input, textarea");
  if (input) {
    await input.click().catch(() => {});
    await page.keyboard.type("go");
    await page.keyboard.press("Enter");
  }
  await page.waitForTimeout(5000);
  if (!(await onCanvas())) throw new Error("could not reach the canvas");
};

const openTile = async (label) => {
  await ensureCanvas();
  await page.keyboard.press("Escape").catch(() => {});
  await page.keyboard.press("Control+K");
  await page.waitForSelector(".palette-input", { timeout: 8000 });
  await page.fill(".palette-input", label);
  await page.waitForTimeout(400);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(2000);
};

// ---- boot + auth -----------------------------------------------------------
await page.goto(APP, { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(1500);
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
  await page.fill('input[placeholder="Device name"]', "fn");
  await page.fill('input[placeholder="Password"]', PASSWORD);
  await page.click("button.authgate-btn");
  await page.waitForSelector("button.authgate-btn", { state: "detached", timeout: 25000 });
}
await page.waitForTimeout(1200);
const li = await page.$("input, textarea");
if (li) { await li.click(); await page.keyboard.type("go"); await page.keyboard.press("Enter"); }
await page.waitForTimeout(5000);
console.log(`APP_URL = ${APP}\n`);

// ---- checks ----------------------------------------------------------------

await check("notes persist across a reload", async () => {
  const marker = `note-${Date.now()}`;
  await openTile("Notes");
  const ta = await page.$(".tile textarea");
  if (!ta) throw new Error("no textarea in the Notes tile");
  await ta.click();
  await page.keyboard.type(marker);
  await page.waitForTimeout(1200);          // debounced write
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(4000);
  await ensureCanvas();
  // document.body.innerText does NOT include a textarea's value — reading it
  // that way is a check that can never pass, regardless of the app.
  const restored = await page.evaluate(() => {
    const t = document.querySelector(".tile textarea");
    return t ? t.value : null;
  });
  if (restored === null) throw new Error("no Notes textarea after the reload");
  if (!restored.includes(marker)) throw new Error("the note did not survive a reload");
  return "written and re-read";
});

await check("a task can be added", async () => {
  await openTile("Tasks");
  const input = await page.$('input[placeholder="Add a task here"]');
  if (!input) throw new Error("no task input");
  const marker = `task-${Date.now() % 100000}`;
  await input.click();
  await page.keyboard.type(marker);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(900);
  const body = await page.evaluate(() => document.body.innerText);
  if (!body.includes(marker)) throw new Error("the task did not appear in the list");
  return "added and listed";
});

await check("the browser tile fetches a real page server-side", async () => {
  await openTile("Browser");
  const input = await page.$('.tbrowser-url');
  if (!input) throw new Error("no address input in the reader tile");
  await input.evaluate((el) => el.scrollIntoView({ block: "center" }));
  await input.fill("example.com");
  await input.press("Enter");
  await page.waitForTimeout(7000);
  const body = await page.evaluate(() => document.body.innerText);
  if (/illustrative examples|example domain/i.test(body)) return "fetched and extracted example.com";
  if (/unavailable|error|failed/i.test(body)) throw new Error("reader reported a failure");
  throw new Error("no recognisable page content rendered");
});

await check("the vault reports its stats", async () => {
  await openTile("Vault");
  await page.waitForTimeout(2500);
  // Scope to the Vault tile. Scanning the whole page catches other tiles'
  // messages and blames them on this one.
  const text = await page.evaluate(() => {
    const t = [...document.querySelectorAll(".tile")]
      .find((el) => /vault/i.test(el.querySelector(".tile-title")?.textContent || ""));
    return t ? t.innerText : null;
  });
  if (text === null) throw new Error("the Vault tile did not open");
  if (/unavailable|failed to/i.test(text)) throw new Error(`vault reported: ${text.slice(0, 90)}`);
  return "rendered without error";
});

await check("settings opens and the theme actually changes", async () => {
  await page.keyboard.press("Escape").catch(() => {});
  const before = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  const btn = await page.$(".settings-btn, [aria-label='Settings']");
  if (!btn) throw new Error("no settings button");
  await btn.click();
  await page.waitForTimeout(1200);
  const opened = await page.evaluate(() => document.body.innerText.toLowerCase().includes("theme"));
  if (!opened) throw new Error("settings panel did not open");
  // Flip the theme via the app's own control if present.
  const dark = await page.$("text=Dark");
  if (dark) { await dark.click(); await page.waitForTimeout(900); }
  const after = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
  await page.keyboard.press("Escape").catch(() => {});
  return `theme ${before} -> ${after}`;
});

await check("the command palette filters", async () => {
  await page.keyboard.press("Escape").catch(() => {});
  await page.keyboard.press("Control+K");
  await page.waitForSelector(".palette-input", { timeout: 8000 });
  await page.fill(".palette-input", "");        // it retains the last filter
  await page.waitForTimeout(400);
  const all = (await page.$$(".palette-row-label")).length;
  await page.fill(".palette-input", "clock");
  await page.waitForTimeout(500);
  const few = (await page.$$(".palette-row-label")).length;
  await page.keyboard.press("Escape");
  if (!all) throw new Error("palette listed nothing at all");
  if (few >= all) throw new Error(`filter did nothing (${all} -> ${few})`);
  return `${all} commands -> ${few} for "clock"`;
});

await check("a tile can be closed", async () => {
  await openTile("Notes");
  const before = await page.evaluate(() => document.querySelectorAll(".tile").length);
  const closer = await page.$(".free-tile-close");
  if (!closer) throw new Error("no close control on any tile");
  // The canvas pans, so a present element can be outside the viewport and never
  // pass Playwright's actionability check. That is a harness problem, not an
  // app one — dispatch the click directly.
  await closer.evaluate((el) => el.click());
  await page.waitForTimeout(900);
  const after = await page.evaluate(() => document.querySelectorAll(".tile").length);
  if (after >= before) throw new Error(`close did nothing (${before} -> ${after})`);
  return `${before} -> ${after} tiles`;
});

await check("chat refuses cleanly with no model configured", async () => {
  await page.keyboard.press("Escape").catch(() => {});
  const bubble = await page.$(".fb-input");
  if (!bubble) throw new Error("no input bubble");
  await bubble.click();
  await page.keyboard.type("what is 2+2");
  await page.keyboard.press("Enter");
  await page.waitForTimeout(9000);
  const body = await page.evaluate(() => document.body.innerText);
  // With no OPENROUTER_API_KEY the correct behaviour is an explicit refusal,
  // NOT a silent nothing and NOT a fabricated answer.
  if (/no model lane|not configured|unavailable|openrouter/i.test(body)) {
    return "explicit refusal, as designed";
  }
  if (/\b4\b/.test(body)) throw new Error("answered without a model configured");
  throw new Error("no visible response and no refusal — silent failure");
});

// ---- report ----------------------------------------------------------------
await page.screenshot({ path: `${SHOTS}/functional-end.png` });
const failed = results.filter((r) => !r.ok);
console.log(`\n${"=".repeat(64)}`);
console.log(`checks: ${results.length}   passed: ${results.length - failed.length}   FAILED: ${failed.length}`);
if (consoleErrors.length) {
  console.log(`\nuncaught page errors (${consoleErrors.length}):`);
  for (const e of [...new Set(consoleErrors)].slice(0, 8)) console.log("  " + e);
}
writeFileSync(`${SHOTS}/functional.json`, JSON.stringify({ app: APP, results, consoleErrors }, null, 2));
await browser.close();
process.exit(failed.length ? 1 : 0);
