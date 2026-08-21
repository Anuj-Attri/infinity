// The canvas must be movable with fingers, not just a mouse.
//
// Mark 4 lost Mark 3's touch driver, so `wheel` and `mousedown` were the only
// pan/zoom paths — both mouse-only. On a phone or tablet the field was frozen at
// zoom 1.0 and anything outside the opening viewport was unreachable, including
// the Browser tile whose default position is 1120px down. Tiles still dragged,
// so the app looked alive while the canvas around it could not be moved.
//
// This drives real touch through CDP and reads the transform off `.dash-zoom`,
// which is where pan/zoom actually land. It also guards the two things the fix
// could plausibly break: a mouse drag must still pan EXACTLY once (a mouse fires
// pointer events too, so mishandling doubles every desktop drag), and dragging a
// tile must not also pan the field underneath it.
//
//   APP_URL=http://127.0.0.1:8000 ACCESS_TOKEN=... node e2e/touch.mjs
import { chromium } from "playwright";

const APP = (process.env.APP_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
const TOKEN = process.env.ACCESS_TOKEN || "";
const PASSWORD = process.env.SMOKE_PASSWORD || "smoke-test-pass";
const SETUP = process.env.SETUP_TOKEN || "";

let failures = 0;
const ok = (label, cond, extra = "") => {
  if (!cond) failures++;
  console.log(`  ${cond ? "  ok  " : " FAIL "} ${label}${extra ? "  — " + extra : ""}`);
};

/** Read the live pan/zoom straight off the element that carries them. */
async function transform(page) {
  return page.evaluate(() => {
    const el = document.querySelector(".dash-zoom");
    if (!el) return null;
    const t = el.style.transform || "";
    const pan = /translate\(([-\d.]+)px,\s*([-\d.]+)px\)/.exec(t);
    const zoom = /scale\(([\d.]+)\)/.exec(t);
    return {
      x: pan ? parseFloat(pan[1]) : 0,
      y: pan ? parseFloat(pan[2]) : 0,
      zoom: zoom ? parseFloat(zoom[1]) : 1,
    };
  });
}

/** A point on the canvas that is well clear of every tile.
 *
 * Not cosmetic. On a tablet viewport the tiles cover most of the field, and a
 * gesture starting on one is CORRECTLY ignored by the pan hook — so a fixed
 * coordinate made this test report "touch is broken" when the hook was fine.
 * elementFromPoint was not enough either: it disagreed with real hit-testing at
 * tile edges, so this uses the tiles' own rectangles plus a margin.
 */
async function emptyPoint(page) {
  const found = await page.evaluate(() => {
    const pad = 24;
    const rects = [...document.querySelectorAll(
      ".free-tile, .fixed-bubble, .top-toolbar, .settings-panel, .palette, .canvas-draw-capture")]
      .map((el) => el.getBoundingClientRect())
      .filter((r) => r.width > 0 && r.height > 0);
    const canvas = document.querySelector(".dash-canvas")?.getBoundingClientRect();
    if (!canvas) return null;
    const clear = (x, y) => rects.every(
      (r) => x < r.left - pad || x > r.right + pad || y < r.top - pad || y > r.bottom + pad);
    // Search inward from the canvas edges — the middle is where tiles live.
    for (let y = canvas.bottom - 30; y > canvas.top + 30; y -= 10) {
      for (let x = canvas.right - 30; x > canvas.left + 30; x -= 10) {
        if (clear(x, y)) return { x: Math.round(x), y: Math.round(y) };
      }
    }
    return null;
  });
  if (!found) throw new Error("no clear canvas point found — cannot test canvas gestures");
  return found;
}

async function signIn(page) {
  await page.goto(APP, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(1500);

  // Same sequence smoke.mjs uses; the first-run screen asks for different
  // fields than the login screen and both can appear.
  if (await page.$('input[placeholder="Master password"]')) {
    if (await page.$('input[placeholder="Setup token"]')) {
      await page.fill('input[placeholder="Setup token"]', SETUP);
    }
    await page.fill('input[placeholder="Master password"]', PASSWORD);
    await page.fill('input[placeholder="Confirm password"]', PASSWORD);
    await page.click("button.authgate-btn");
    await page.waitForSelector('input[placeholder="Device name"]', { timeout: 20000 }).catch(() => {});
  }
  if (await page.$('input[placeholder="Device name"]')) {
    await page.fill('input[placeholder="Device name"]', "touch");
    await page.fill('input[placeholder="Password"]', PASSWORD);
    await page.click("button.authgate-btn");
    await page.waitForSelector("button.authgate-btn", { state: "detached", timeout: 25000 }).catch(() => {});
  }
  await page.waitForTimeout(1200);

  // Landing -> canvas.
  const landingInput = await page.$(".landing input, .landing textarea, input, textarea");
  if (landingInput) {
    await landingInput.click().catch(() => {});
    await page.keyboard.type("touch");
    await page.keyboard.press("Enter");
  }
  await page.waitForSelector(".dash-zoom", { timeout: 40000 });
  await page.waitForTimeout(3000);
}

const browser = await chromium.launch();

// ---------------------------------------------------------------- touch ----
{
  // A large touch surface, not a phone preset. What is under test is the touch
  // GESTURE path, and on a small viewport the tiles and fixed bubbles cover the
  // entire canvas — there is no empty field left to grab, so the test would be
  // measuring layout density rather than the hook.
  const ctx = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    hasTouch: true,
    ...(TOKEN ? { extraHTTPHeaders: { "X-Access-Token": TOKEN } } : {}),
  });
  const page = await ctx.newPage();
  await signIn(page);

  const before = await transform(page);
  ok("the canvas transform is readable", !!before, JSON.stringify(before));

  // --- one finger pans ---
  const cdp = await ctx.newCDPSession(page);
  const drag = async (from, to, steps = 8) => {
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart", touchPoints: [{ x: from.x, y: from.y, id: 1 }],
    });
    for (let i = 1; i <= steps; i++) {
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{
          x: from.x + ((to.x - from.x) * i) / steps,
          y: from.y + ((to.y - from.y) * i) / steps,
          id: 1,
        }],
      });
      await page.waitForTimeout(16);
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  };

  // Must start on genuinely empty canvas: a gesture on a tile belongs to the
  // tile, and the hook is right to ignore it.
  const from = await emptyPoint(page);
  console.log(`  ....  panning from empty canvas at (${from.x}, ${from.y})`);
  await drag(from, { x: from.x - 200, y: from.y - 120 });
  await page.waitForTimeout(400);
  const panned = await transform(page);
  const dx = panned.x - before.x, dy = panned.y - before.y;
  ok("one finger pans the canvas", Math.abs(dx) > 50 && Math.abs(dy) > 30,
     `moved by (${dx.toFixed(0)}, ${dy.toFixed(0)})`);

  // --- two fingers pinch-zoom ---
  const z0 = (await transform(page)).zoom;
  // Both fingers must land on clear canvas AND inside the viewport. The first
  // attempt anchored on the raw empty point near the bottom-right corner, so
  // the second finger was dispatched off-screen and the pinch never started.
  const raw = await emptyPoint(page);
  const vp = page.viewportSize();
  const anchor = {
    x: Math.min(Math.max(raw.x, 200), vp.width - 200),
    y: Math.min(Math.max(raw.y, 120), vp.height - 120),
  };
  console.log(`  ....  pinching around (${anchor.x}, ${anchor.y})`);
  const pinch = async (spread) => {
    const cx = anchor.x, cy = anchor.y;
    await cdp.send("Input.dispatchTouchEvent", {
      type: "touchStart",
      touchPoints: [{ x: cx - 60, y: cy, id: 1 }, { x: cx + 60, y: cy, id: 2 }],
    });
    for (let i = 1; i <= 10; i++) {
      const d = 60 + ((spread - 60) * i) / 10;
      await cdp.send("Input.dispatchTouchEvent", {
        type: "touchMove",
        touchPoints: [{ x: cx - d, y: cy, id: 1 }, { x: cx + d, y: cy, id: 2 }],
      });
      await page.waitForTimeout(16);
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await page.waitForTimeout(400);
  };

  await pinch(160);   // fingers apart -> zoom in
  const zIn = (await transform(page)).zoom;
  ok("pinch out zooms in", zIn > z0 + 0.05, `${z0.toFixed(2)} -> ${zIn.toFixed(2)}`);

  await pinch(20);    // fingers together -> zoom out
  const zOut = (await transform(page)).zoom;
  ok("pinch in zooms out", zOut < zIn - 0.05, `${zIn.toFixed(2)} -> ${zOut.toFixed(2)}`);

  ok("zoom stays within the same bounds the wheel uses",
     zIn <= 2.5 + 1e-6 && zOut >= 0.5 - 1e-6, `in=${zIn.toFixed(2)} out=${zOut.toFixed(2)}`);

  // --- a finger on a TILE must not pan the field ---
  const tile = await page.$(".free-tile");
  if (tile) {
    const box = await tile.boundingBox();
    const t0 = await transform(page);
    // Grab the tile's header area, which is its drag handle.
    await drag({ x: box.x + box.width / 2, y: box.y + 8 },
               { x: box.x + box.width / 2 + 60, y: box.y + 68 });
    await page.waitForTimeout(400);
    const t1 = await transform(page);
    ok("dragging a tile does not also pan the canvas",
       Math.abs(t1.x - t0.x) < 3 && Math.abs(t1.y - t0.y) < 3,
       `canvas moved (${(t1.x - t0.x).toFixed(1)}, ${(t1.y - t0.y).toFixed(1)})`);
  } else {
    console.log("  ....  no .free-tile on canvas; skipped the tile-drag guard");
  }

  await ctx.close();
}

// ---------------------------------------------------------------- mouse ----
// The regression risk: a mouse fires pointer events AS WELL AS mouse events, so
// if the touch hook handled it too, every desktop drag would pan twice.
{
  const ctx = await browser.newContext({
    viewport: { width: 1400, height: 900 },
    ...(TOKEN ? { extraHTTPHeaders: { "X-Access-Token": TOKEN } } : {}),
  });
  const page = await ctx.newPage();
  await signIn(page);

  const before = await transform(page);
  await page.mouse.move(700, 700);
  await page.mouse.down();
  for (let i = 1; i <= 10; i++) { await page.mouse.move(700 + i * 10, 700 + i * 5); await page.waitForTimeout(12); }
  await page.mouse.up();
  await page.waitForTimeout(400);
  const after = await transform(page);

  const dx = after.x - before.x, dy = after.y - before.y;
  ok("mouse drag still pans", Math.abs(dx) > 40, `dx=${dx.toFixed(0)}`);
  // Moved +100/+50. Doubling would show ~200/~100.
  ok("mouse drag pans exactly ONCE, not twice",
     Math.abs(dx) < 150 && Math.abs(dy) < 90, `moved (${dx.toFixed(0)}, ${dy.toFixed(0)}) — expected about (100, 50)`);

  await ctx.close();
}

await browser.close();
console.log(failures ? `\n${failures} FAILED` : "\ncanvas touch: all checks passed");
process.exit(failures ? 1 : 0);
