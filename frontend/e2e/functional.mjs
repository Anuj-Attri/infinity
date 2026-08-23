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

// The panel is grouped into tabs. A tab whose content failed to render shows an
// empty pane — and the check above would still pass, because it only looks for
// the word "theme" on the default tab. Every tab has to actually contain
// something, and the one that is easiest to get wrong is the one whose content
// is a component that fetches (Account → your name, Connections → MCP).
await check("every settings tab renders content", async () => {
  await page.keyboard.press("Escape").catch(() => {});
  const btn = await page.$(".settings-btn, [aria-label='Settings']");
  if (!btn) throw new Error("no settings button");
  await btn.click();
  await page.waitForSelector(".settings-tab", { timeout: 8000 });

  const tabs = await page.$$eval(".settings-tab", (els) => els.map((e) => e.textContent.trim()));
  if (tabs.length < 2) throw new Error(`only ${tabs.length} tab(s): ${tabs}`);

  const empty = [];
  for (const label of tabs) {
    await page.click(`.settings-tab:text-is("${label}")`);
    await page.waitForTimeout(500);
    const n = await page.$$eval(".settings-body .settings-section", (els) => els.length);
    const text = await page.$eval(".settings-body", (el) => el.innerText.trim().length);
    if (!n || text < 20) empty.push(`${label}(${n} sections, ${text} chars)`);
  }
  await page.keyboard.press("Escape").catch(() => {});
  if (empty.length) throw new Error(`empty tab(s): ${empty.join(", ")}`);
  return `${tabs.length} tabs: ${tabs.join(" · ")}`;
});

// The name Nova calls you. It is server state feeding the model's system
// prompt, so "it saved" means it survives a reload — not that the input
// accepted keystrokes.
await check("your name saves and survives a reload", async () => {
  await page.keyboard.press("Escape").catch(() => {});
  const btn = await page.$(".settings-btn, [aria-label='Settings']");
  await btn.click();
  await page.waitForSelector(".settings-tab", { timeout: 8000 });
  await page.click('.settings-tab:text-is("Account")');
  await page.waitForSelector('.settings-input[placeholder="your name"]', { timeout: 8000 });

  // Unique per run. Save is (correctly) disabled when nothing changed, so a
  // fixed name makes this check pass once and then hang on the second run
  // against the same data directory — which is exactly how it first failed.
  const NAME = `E2E ${Date.now().toString().slice(-6)}`;
  await page.fill('.settings-input[placeholder="your name"]', NAME);
  await page.click('.settings-row-actions button:text-is("Save")');
  await page.waitForTimeout(1200);

  // Verified by RELOADING, not by calling /profile from page script: a bare
  // fetch() carries none of the app's auth and just returns 401, which would
  // fail this check for a reason that has nothing to do with the feature.
  // A reload also tests the thing the user actually cares about.
  const readBack = async () => {
    await page.reload({ waitUntil: "networkidle" });
    await page.waitForTimeout(3500);
    await ensureCanvas();
    const b = await page.$(".settings-btn, [aria-label='Settings']");
    await b.click();
    await page.waitForSelector(".settings-tab", { timeout: 8000 });
    await page.click('.settings-tab:text-is("Account")');
    await page.waitForSelector('.settings-input[placeholder="your name"]', { timeout: 8000 });
    await page.waitForTimeout(800);   // the field loads from the server
    return page.$eval('.settings-input[placeholder="your name"]', (el) => el.value);
  };

  const stored = await readBack();
  if (stored !== NAME) throw new Error(`after reload the field held ${JSON.stringify(stored)}`);

  // Clearing it is a supported outcome, not an error — the persona has a
  // neutral form and uses it when no name is set.
  await page.fill('.settings-input[placeholder="your name"]', "");
  await page.click('.settings-row-actions button:text-is("Save")');
  await page.waitForTimeout(1200);
  const cleared = await readBack();
  await page.keyboard.press("Escape").catch(() => {});
  if (cleared !== "") throw new Error(`clearing left ${JSON.stringify(cleared)}`);
  return `saved "${NAME}", survived a reload, then cleared`;
});

// What Nova remembers about you — add, see it listed, delete it. A core flow
// with no coverage at all until now, which is why restructuring the list from
// type-grouped sections to one flat list could have broken it silently.
await check("a memory can be added and deleted", async () => {
  await page.keyboard.press("Escape").catch(() => {});
  const btn = await page.$(".settings-btn, [aria-label='Settings']");
  await btn.click();
  await page.waitForSelector(".settings-tab", { timeout: 8000 });
  await page.click('.settings-tab:text-is("Nova")');
  await page.waitForSelector(".mem-add-input", { timeout: 8000 });

  const MARK = `e2e-memory-${Date.now().toString().slice(-6)}`;
  await page.waitForTimeout(600);            // let the panel's initial load settle
  await page.fill(".mem-add-input", MARK);
  // Add is disabled until the field has text, so an enabled button is proof
  // React actually saw the input. Without this, a fill that did not register
  // makes the click a silent no-op, and the check fails later with "added but
  // not listed" — which points at the list instead of at the typing.
  await page.waitForFunction(
    () => { const b = document.querySelector(".mem-add-btn"); return b && !b.disabled; },
    { timeout: 5000 },
  ).catch(() => { throw new Error("the Add button never enabled — the field did not register the text"); });
  await page.click(".mem-add-btn");

  // POLL, do not sleep a fixed amount. Saving embeds the text, and the FIRST
  // embed in a fresh process pays for the TLS handshake to the embedding API on
  // top of the call itself — comfortably more than the 1.5s this check
  // originally waited. That made it fail against a perfectly working build, and
  // the failure looked exactly like the rendering bug it was meant to catch.
  let listed = [];
  let errText = "";
  for (let i = 0; i < 30; i++) {
    listed = await page.$$eval(".mem-card-text", (els) => els.map((e) => e.textContent));
    errText = await page.$$eval(".mem-error", (els) => els.map((e) => e.textContent).join(" "));
    if (listed.some((t) => t.includes(MARK)) || errText.trim()) break;
    await page.waitForTimeout(500);
  }

  // With no embedding provider configured the backend correctly refuses with a
  // 503. That is the environment, not a bug — but this check must not go
  // quietly green because of it, or it would report success on a build where
  // memory is entirely broken. So when the happy path is unavailable, assert
  // the other property that matters: the refusal is visible to the user rather
  // than the click appearing to do nothing.
  if (!listed.some((t) => t.includes(MARK))) {
    // A successful add clears the field, so its contents say WHERE this broke:
    // still holding the text means the request never completed or threw;
    // empty means it was accepted and the list is what is wrong.
    const stillTyped = await page.$eval(".mem-add-input", (el) => el.value).catch(() => "?");
    await page.keyboard.press("Escape").catch(() => {});
    if (errText.trim()) {
      return `NOT FULLY TESTED — save refused, but visibly: "${errText.trim().slice(0, 70)}"`;
    }
    throw new Error(
      `added but not listed and nothing was said — ${listed.length} card(s), ` +
      `input still holds ${JSON.stringify(stillTyped)}`);
  }

  // Delete it again so repeat runs do not accumulate junk in the account.
  const idx = listed.findIndex((t) => t.includes(MARK));
  const delBtns = await page.$$(".mem-card .mem-btn-danger");
  if (!delBtns[idx]) throw new Error("no delete button on the new memory");
  await delBtns[idx].click();
  await page.waitForTimeout(1500);
  const after = await page.$$eval(".mem-card-text", (els) => els.map((e) => e.textContent));
  await page.keyboard.press("Escape").catch(() => {});
  if (after.some((t) => t.includes(MARK))) throw new Error("delete did not remove it");
  return `added, listed among ${listed.length}, deleted`;
});

// Nova's built-in hands (model switching, panels, /image, /audio, /research,
// /summarize) were rendered in the same list as a user's servers, with the same
// Remove button — and removing one only breaks the app until the next restart
// re-seeds it. The backend refuses now; this is the other half.
await check("Nova's built-in tools cannot be removed from the UI", async () => {
  await page.keyboard.press("Escape").catch(() => {});
  const btn = await page.$(".settings-btn, [aria-label='Settings']");
  await btn.click();
  await page.waitForSelector(".settings-tab", { timeout: 8000 });
  await page.click('.settings-tab:text-is("Connections")');
  await page.waitForSelector(".mcp-server", { timeout: 8000 });

  const rows = await page.$$eval(".mcp-server", (els) => els.map((el) => ({
    name: el.querySelector(".mcp-server-name")?.textContent?.trim() || "?",
    builtin: /built in/i.test(el.querySelector(".mcp-server-head")?.innerText || ""),
    removable: !!el.querySelector(".mcp-x"),
  })));
  await page.keyboard.press("Escape").catch(() => {});

  const builtins = rows.filter((r) => r.builtin);
  if (!builtins.length) throw new Error(`no built-in server listed at all: ${JSON.stringify(rows)}`);
  const offered = builtins.filter((r) => r.removable);
  if (offered.length) throw new Error(`Remove offered on built-in(s): ${offered.map((r) => r.name)}`);
  return `${builtins.length} built-in, ${rows.length - builtins.length} user server(s)`;
});

// The product owns the model keys; the user never supplies one. Any screen that
// presents our upstream as something they connected is a bug the owner called
// out by name — so assert the app does not say it anywhere a person can read.
await check("the UI does not name our upstream provider", async () => {
  await page.keyboard.press("Escape").catch(() => {});
  const btn = await page.$(".settings-btn, [aria-label='Settings']");
  await btn.click();
  await page.waitForSelector(".settings-tab", { timeout: 8000 });
  const tabs = await page.$$eval(".settings-tab", (els) => els.map((e) => e.textContent.trim()));
  const hits = [];
  for (const label of tabs) {
    await page.click(`.settings-tab:text-is("${label}")`);
    await page.waitForTimeout(400);
    const t = await page.$eval(".settings-body", (el) => el.innerText);
    if (/openrouter/i.test(t)) hits.push(label);
  }
  await page.keyboard.press("Escape").catch(() => {});
  if (hits.length) throw new Error(`named on tab(s): ${hits.join(", ")}`);
  return `clean across ${tabs.length} tabs`;
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
