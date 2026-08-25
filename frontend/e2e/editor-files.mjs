// The workspace file operations, driven the way a person uses them.
//
// Create, autosave, rename, delete, and the lost-update check. All five are
// destructive or near-destructive, and none of them can be proved by a unit
// test: what matters is that the TILE ends up in the right state — tabs
// following a rename, tabs closing on a delete, autosave not firing over a
// conflict — and that is a browser question.
//
//   APP_URL=http://127.0.0.1:8000 SETUP_TOKEN=... node e2e/editor-files.mjs
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const APP = (process.env.APP_URL || "http://127.0.0.1:5173").replace(/\/+$/, "");
const PASSWORD = process.env.SMOKE_PASSWORD || "smoke-test-pass";
const SETUP_TOKEN = process.env.SETUP_TOKEN || "";
const DOOR = process.env.ACCESS_TOKEN || "";
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
const page = await ctx.newPage();
// Playwright auto-dismisses dialogs, which would silently ANSWER NO to every
// confirm and make the destructive tests pass without deleting anything.
// Accept them explicitly, and count them, so "we were asked" is itself asserted.
let confirms = [];
page.on("dialog", async (d) => { confirms.push(d.message()); await d.accept(); });

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
  await page.fill('input[placeholder="Device name"]', "files");
  await page.fill('input[placeholder="Password"]', PASSWORD);
  await page.click("button.authgate-btn");
  await page.waitForSelector("button.authgate-btn", { state: "detached", timeout: 25000 });
}
await page.waitForTimeout(1200);
if (!(await page.$(".tile"))) {
  const input = await page.$("input, textarea");
  if (input) { await input.click().catch(() => {}); await page.keyboard.type("go"); await page.keyboard.press("Enter"); }
  await page.waitForTimeout(5000);
}

await page.keyboard.press("Escape").catch(() => {});
await page.keyboard.press("Control+K");
await page.waitForSelector(".palette-input", { timeout: 10000 });
await page.fill(".palette-input", "code");
await page.waitForTimeout(600);
await page.keyboard.press("Enter");
await page.waitForSelector(".tcode-new", { timeout: 30000 });
ok("the Code tile opened", true);

const rowNames = () => page.$$eval(".code-tree-name", (els) => els.map((e) => e.textContent.trim()));
const tabNames = () => page.$$eval(".tcode-tab-name", (els) => els.map((e) => e.textContent.trim()));

// ---- create, inside a folder ----------------------------------------------
const STAMP = Date.now().toString().slice(-6);
const DIR = `e2e${STAMP}`;
await page.click(".tcode-new");
await page.waitForSelector(".tcode-new-input", { timeout: 8000 });
await page.fill(".tcode-new-input", `${DIR}/first.ts`);
await page.keyboard.press("Enter");
await page.waitForSelector(".monaco-editor", { timeout: 40000 });
await page.waitForTimeout(1200);

ok("the new file opened in a tab", (await tabNames()).includes("first.ts"));
// The reveal: a file created inside a folder must be VISIBLE in the tree, not
// hidden inside a collapsed one.
ok("its folder was expanded so it can be seen", (await rowNames()).includes("first.ts"),
   (await rowNames()).join(", ").slice(0, 90));

// ---- autosave --------------------------------------------------------------
await page.click(".monaco-editor .view-lines");
await page.keyboard.type("export const one = 1;");
// No Ctrl+S. Autosave has to do it.
let saved = false;
for (let i = 0; i < 25; i++) {
  await page.waitForTimeout(400);
  const disabled = await page.$eval(".tcode-save", (el) => el.disabled).catch(() => false);
  if (disabled) { saved = true; break; }
}
ok("autosave wrote the file without Ctrl+S", saved);

// It has to have written the real bytes, not just cleared a flag: reload the
// tile's view of the file by closing and reopening the tab.
await page.click(".tcode-tab-x");
await page.waitForTimeout(400);
await page.click(`.code-tree-filerow:has-text("first.ts")`);
await page.waitForTimeout(1500);
const reread = await page.$eval(".monaco-editor .view-lines", (el) => el.innerText).catch(() => "");
ok("and the content really landed on the server",
   reread.replace(/\s+/g, " ").includes("export const one = 1;"),
   JSON.stringify(reread.slice(0, 50)));

// ---- rename ----------------------------------------------------------------
confirms = [];
await page.click(`.code-tree-filerow:has-text("first.ts")`, { button: "right" });
await page.waitForSelector(".tcode-menu", { timeout: 8000 });
await page.click('.tcode-menu-item:text-is("Rename…")');
await page.waitForSelector(".tcode-new-input", { timeout: 8000 });
await page.fill(".tcode-new-input", `${DIR}/second.ts`);
await page.keyboard.press("Enter");
await page.waitForTimeout(1800);

ok("the file was renamed in the tree", (await rowNames()).includes("second.ts"));
ok("the old name is gone", !(await rowNames()).includes("first.ts"));
// The tab must FOLLOW. If it does not, the next save writes to a path that no
// longer exists — or worse, recreates the old file.
ok("the open tab followed the rename", (await tabNames()).includes("second.ts"),
   (await tabNames()).join(", "));

// And saving still works against the new path.
await page.click(".monaco-editor .view-lines");
await page.keyboard.type(" // moved");
await page.waitForTimeout(2500);
const afterRenameErr = await page.$$eval(".tcode-openerr, .tcode-new-err", (els) => els.map((e) => e.textContent).join(" "));
ok("saving after a rename does not error", !/error|failed|not found/i.test(afterRenameErr),
   afterRenameErr.slice(0, 70));

// ---- delete ----------------------------------------------------------------
confirms = [];
await page.click(`.code-tree-filerow:has-text("second.ts")`, { button: "right" });
await page.waitForSelector(".tcode-menu", { timeout: 8000 });
await page.click('.tcode-menu-item:text-is("Delete…")');
await page.waitForTimeout(2000);

ok("deleting asks first", confirms.length === 1, JSON.stringify(confirms[0]?.slice(0, 60) || ""));
ok("the file left the tree", !(await rowNames()).includes("second.ts"));
// A tab left open on a deleted file is the worst outcome: the next autosave
// would recreate what the user just deleted.
ok("its tab closed too", !(await tabNames()).includes("second.ts"), (await tabNames()).join(", "));

// ---- deleting a folder says what is in it ----------------------------------
confirms = [];
await page.click(`.code-tree-dirrow:has-text("${DIR}")`, { button: "right" });
await page.waitForSelector(".tcode-menu", { timeout: 8000 });
await page.click('.tcode-menu-item:text-is("Delete…")');
await page.waitForTimeout(2000);
ok("deleting a folder asks first", confirms.length === 1, JSON.stringify(confirms[0]?.slice(0, 70) || ""));
ok("the folder is gone", !(await rowNames()).includes(DIR));

// ---- the lost-update check -------------------------------------------------
// The scenario: the file changes on the server while you have it open. Two Code
// tiles is enough to cause it (`code` is a multi-instance kind), and so is a
// second device. Before the version check, whoever saved first simply lost
// their work with nothing said.
//
// Simulated by writing to /fs/write from the page with the app's own session
// token, which is exactly what "another device saved" looks like to this tab.
// That couples this test to the storage key, deliberately: the alternative is
// driving a second tile, which made the test about tile spawning rather than
// about the thing being tested.
confirms = [];
const SHARED = `shared${STAMP}.ts`;
await page.click(".tcode-new");
await page.waitForSelector(".tcode-new-input", { timeout: 8000 });
await page.fill(".tcode-new-input", SHARED);
await page.keyboard.press("Enter");
await page.waitForSelector(".monaco-editor", { timeout: 40000 });
await page.waitForTimeout(1500);

const outOfBand = await page.evaluate(async (path) => {
  const tok = localStorage.getItem("infinity.auth.token.v1");
  const r = await fetch("/fs/write", {
    method: "POST",
    headers: { "content-type": "application/json", Authorization: `Bearer ${tok}` },
    body: JSON.stringify({ path, content: "// written by somebody else\n" }),
  });
  return r.status;
}, SHARED);
ok("something else saved the file underneath us", outOfBand === 200, `HTTP ${outOfBand}`);

// Now type. Autosave will try to write, carrying the version from before that
// out-of-band save, and must be refused.
await page.click(".monaco-editor .view-lines");
await page.keyboard.type("// my local edit");
await page.waitForTimeout(4000);

const banner = await page.$(".tcode-conflict");
ok("the tile was told the file changed underneath it", !!banner);
if (banner) {
  const text = (await banner.innerText()).replace(/\s+/g, " ");
  ok("and it offers both ways out, naming who loses",
     /discard mine/i.test(text) && /overwrite theirs/i.test(text), JSON.stringify(text.slice(0, 90)));
}

// The other side of the guarantee: the other save is still on disk. Autosave
// must NOT have quietly won, and must not keep retrying either.
const onDisk = await page.evaluate(async (path) => {
  const tok = localStorage.getItem("infinity.auth.token.v1");
  const r = await fetch(`/fs/read?path=${encodeURIComponent(path)}`,
                        { headers: { Authorization: `Bearer ${tok}` } });
  return r.ok ? (await r.json()).content : `HTTP ${r.status}`;
}, SHARED);
ok("the other save was not overwritten", onDisk.includes("written by somebody else"),
   JSON.stringify(onDisk.slice(0, 60)));

// Choosing "overwrite theirs" must then actually work — a conflict the user
// cannot resolve is a dead end.
if (banner) {
  await page.click(".tcode-conflict-danger");
  await page.waitForTimeout(2500);
  ok("choosing to overwrite clears the conflict", !(await page.$(".tcode-conflict")));
  const after = await page.evaluate(async (path) => {
    const tok = localStorage.getItem("infinity.auth.token.v1");
    const r = await fetch(`/fs/read?path=${encodeURIComponent(path)}`,
                          { headers: { Authorization: `Bearer ${tok}` } });
    return r.ok ? (await r.json()).content : `HTTP ${r.status}`;
  }, SHARED);
  ok("and our version is the one on disk now", after.includes("my local edit"),
     JSON.stringify(after.slice(0, 60)));
}

// ---- "+ New file" must never overwrite ------------------------------------
// The exact keystrokes: click +, type the name of a file you already have,
// press Enter. That used to return 200 and write the empty string — the file
// was zeroed, and the tab opened with original:"" so it was not even marked
// dirty. No trash, no git, no undo.
confirms = [];
const KEEP = `keep${STAMP}.ts`;
await page.click(".tcode-new");
await page.waitForSelector(".tcode-new-input", { timeout: 8000 });
await page.fill(".tcode-new-input", KEEP);
await page.keyboard.press("Enter");
await page.waitForSelector(".monaco-editor", { timeout: 40000 });
await page.waitForTimeout(1200);
await page.click(".monaco-editor .view-lines");
await page.keyboard.type("// years of work");
await page.waitForTimeout(2600);           // let autosave land it

await page.click(".tcode-new");
await page.waitForSelector(".tcode-new-input", { timeout: 8000 });
await page.fill(".tcode-new-input", KEEP);       // the SAME name
await page.keyboard.press("Enter");
await page.waitForTimeout(1800);

const createErr = await page.$$eval(".tcode-new-err", (els) => els.map((e) => e.textContent).join(" "));
ok("creating over an existing file is refused, and says why",
   /already exists/i.test(createErr), JSON.stringify(createErr.slice(0, 70)));

const survived = await page.evaluate(async (path) => {
  const tok = localStorage.getItem("infinity.auth.token.v1");
  const r = await fetch(`/fs/read?path=${encodeURIComponent(path)}`,
                        { headers: { Authorization: `Bearer ${tok}` } });
  return r.ok ? (await r.json()).content : `HTTP ${r.status}`;
}, KEEP);
ok("and the existing file was not emptied", survived.includes("years of work"),
   JSON.stringify(survived.slice(0, 50)));

// ---- closing the tile must not eat the last edit ---------------------------
// Autosave fires after a pause, so there is always a window where typed text is
// not yet on the server. Closing a TILE unmounts the component and drops its
// state — and beforeunload does not help, because that is the browser tab, not
// the tile. The fix flushes pending saves on unmount; this is the proof.
confirms = [];
const LAST = `flush${STAMP}.ts`;
await page.click(".tcode-new");
await page.waitForSelector(".tcode-new-input", { timeout: 8000 });
await page.fill(".tcode-new-input", LAST);
await page.keyboard.press("Enter");
await page.waitForSelector(".monaco-editor", { timeout: 40000 });
await page.waitForTimeout(1500);

await page.click(".monaco-editor .view-lines");
await page.keyboard.type("// typed then closed immediately");
// Deliberately INSIDE the autosave debounce — this is the window being tested.
await page.waitForTimeout(200);
const closeBtn = await page.$(".free-tile-close");
ok("the tile has a close control", !!closeBtn);
if (closeBtn) {
  await closeBtn.click();
  await page.waitForTimeout(2500);
  const onDiskAfterClose = await page.evaluate(async (path) => {
    const tok = localStorage.getItem("infinity.auth.token.v1");
    const r = await fetch(`/fs/read?path=${encodeURIComponent(path)}`,
                          { headers: { Authorization: `Bearer ${tok}` } });
    return r.ok ? (await r.json()).content : `HTTP ${r.status}`;
  }, LAST);
  ok("the edit was flushed when the tile closed",
     onDiskAfterClose.includes("typed then closed immediately"),
     JSON.stringify(onDiskAfterClose.slice(0, 60)));
}

if (failures) await page.screenshot({ path: `${SHOTS}/editor-files.png` }).catch(() => {});
await browser.close();
console.log(failures ? `\n${failures} FAILED` : "\nworkspace file operations: all checks passed");
process.exit(failures ? 1 : 0);
