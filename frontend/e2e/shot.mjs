// Capture a single tile, by label. Used to eyeball a change without opening a
// browser by hand:  node e2e/shot.mjs Console
import { chromium } from "playwright";
const LABEL = process.argv[2] || "Console";
const APP = (process.env.APP_URL || "http://127.0.0.1:5173").replace(/\/+$/, "");
const PASSWORD = process.env.SMOKE_PASSWORD || "smoke-test-pass";
const DOOR = process.env.ACCESS_TOKEN || "";
const b = await chromium.launch();
const _ctx = await b.newContext({
  viewport: { width: 1500, height: 1000 },
  ...(DOOR ? { extraHTTPHeaders: { "X-Access-Token": DOOR } } : {}),
});
const p = await _ctx.newPage();
await p.goto(APP, { waitUntil: "networkidle" });
await p.waitForTimeout(1200);
if (await p.$('input[placeholder="Device name"]')) {
  await p.fill('input[placeholder="Device name"]', "shot");
  await p.fill('input[placeholder="Password"]', PASSWORD);
  await p.click("button.authgate-btn");
  await p.waitForSelector("button.authgate-btn", { state: "detached", timeout: 20000 });
}
await p.waitForTimeout(1200);
const i = await p.$("input, textarea");
if (i) { await i.click(); await p.keyboard.type("x"); await p.keyboard.press("Enter"); }
await p.waitForTimeout(4500);
await p.keyboard.press("Control+K");
await p.waitForSelector(".palette-input");
await p.fill(".palette-input", LABEL);
await p.waitForTimeout(400);
await p.keyboard.press("Enter");
await p.waitForTimeout(3200);
const tiles = await p.$$(".tile");
await tiles[tiles.length - 1].screenshot({ path: `e2e/shots/tile-${LABEL.toLowerCase()}.png` });
console.log(`captured e2e/shots/tile-${LABEL.toLowerCase()}.png`);
await b.close();
