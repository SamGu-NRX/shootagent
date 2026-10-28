// Loom screenshots (web/screenshots/live-03..07) from a showcase shoot that already has a QC run, the two
// chat requests ("remove the cars and trash bins", then a TV mask with "put the reference image on this
// screen") and a reel. It only reads runs; the click-to-mask shot asks the server for one SAM mask (local,
// no API cost). Needs `npm run dev` and `shootagent serve`, and Microsoft Edge installed, because
// Playwright's own Chromium cannot decode the reel's H.264.
// Usage: node scripts/live-screenshots.mjs <shootId> [slider|chat|mask|reel]
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
const out = (n) => fileURLToPath(new URL(`../screenshots/${n}.jpg`, import.meta.url));
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const shootId = process.argv[2];
const only = process.argv[3];
const b = await chromium.launch({ channel: "msedge" });
const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
p.on("pageerror", (e) => log("PAGEERROR", String(e)));
p.on("console", (m) => m.type() === "error" && log("CONSOLE", m.text()));
const shot = (n) => p.screenshot({ path: out(n), type: "jpeg", quality: 88 });

async function open() {
  await p.goto("about:blank");
  await p.goto(`http://localhost:5173/?api=live#/s/${shootId}`);
  await p.locator(".strip-item").first().waitFor();
  await p.waitForTimeout(2500);
}
async function view(label) {
  const item = p.locator(".strip-item").filter({ has: p.locator(".strip-label", { hasText: new RegExp(`^\\d+${label.replace(/[()]/g, "\\$&")}$`) }) });
  await item.click();
  await p.waitForTimeout(1200);
}
const imgBox = () => p.locator(".layer:not([hidden]) .imgbox").last().boundingBox();
async function zoomAt(fx, fy, steps) {
  const r = await imgBox();
  const x = r.x + r.width * fx, y = r.y + r.height * fy;
  await p.mouse.move(x, y);
  for (let i = 0; i < steps; i++) { await p.mouse.wheel(0, -240); await p.waitForTimeout(60); }
  await p.waitForTimeout(500);
  return { x, y };
}
async function slideTo(x) {
  const hb = await p.locator(".split-handle").boundingBox();
  await p.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
  await p.mouse.down();
  await p.mouse.move(x, hb.y + hb.height / 2, { steps: 15 });
  await p.mouse.up();
  await p.waitForTimeout(500);
}
async function pickRun(match) {
  await p.getByRole("tab", { name: "Trace" }).click();
  const sel = p.locator(".run-pick select");
  const opts = await sel.locator("option").allTextContents();
  const i = opts.findIndex((o) => match.test(o));
  if (i >= 0) await sel.selectOption({ index: i });
  await p.waitForTimeout(800);
}
async function scrollTraceTo(locator) {
  await locator.first().evaluate((el) => el.scrollIntoView({ block: "center" }));
  await p.waitForTimeout(500);
}

if (!only || only === "slider") {
  await open();
  await view("Exterior 2");
  await p.locator(".ver", { hasText: "v03.2" }).click();
  await p.waitForTimeout(1000);
  const pt = await zoomAt(0.221, 0.42, 3);
  await slideTo(pt.x);
  await pickRun(/QC pass/);
  await scrollTraceTo(p.locator(".tr-check[data-passed=true]"));
  await shot("live-03-fixed-finding-slider");
  log("slider labels", (await p.locator(".stage-label").allTextContents()).join(" | "));
}

if (!only || only === "chat") {
  await open();
  await view("Exterior 3");
  const r = await imgBox();
  await slideTo(r.x + r.width * 0.5);
  await p.getByRole("tab", { name: "Chat" }).click();
  await p.waitForTimeout(600);
  await p.locator(".skipped summary").first().click();
  await p.waitForTimeout(400);
  await p.locator(".exchange").first().evaluate((el) => el.scrollIntoView({ block: "start" }));
  await p.waitForTimeout(400);
  await shot("live-04-chat-results");
}

if (!only || only === "mask") {
  await open();
  await view("Bedroom 2");
  await p.locator(".ver", { hasText: "v08.0" }).click();
  await p.waitForTimeout(800);
  await p.getByRole("radio", { name: "After" }).click();
  await p.waitForTimeout(600);
  const r = await imgBox();
  await p.keyboard.down("Shift");
  await p.mouse.click(r.x + r.width * 0.27, r.y + r.height * 0.39);
  await p.mouse.click(r.x + r.width * 0.22, r.y + r.height * 0.34);
  await p.keyboard.up("Shift");
  await p.locator(".mask-card", { hasText: "of frame" }).waitFor({ timeout: 120000 });
  await p.waitForTimeout(800);
  await p.getByRole("button", { name: "Use in chat" }).click();
  await p.locator("#chat-input").fill("put the reference image on this screen");
  await p.waitForTimeout(600);
  await shot("live-05-click-to-mask");
  // the result of the same request: split through the TV, the retry in the trace
  await p.getByRole("button", { name: "Remove mask" }).click();
  await p.locator(".tog", { hasText: "Mask" }).click();
  await p.locator("#chat-input").fill("");
  await p.locator(".ver", { hasText: "v08.2" }).click();
  await p.getByRole("radio", { name: "Split" }).click();
  await p.waitForTimeout(800);
  const pt = await zoomAt(0.275, 0.39, 2);
  await slideTo(pt.x);
  await pickRun(/reference image/);
  await scrollTraceTo(p.locator(".tr-retry"));
  await shot("live-06-tv-result-retry");
}

if (!only || only === "reel") {
  await open();
  await view("Exterior 3");
  await p.getByRole("tab", { name: "Reel" }).click();
  await p.locator('.reel-frame[data-state="ready"]').waitFor({ timeout: 60000 });
  await p.evaluate(() => { const v = document.querySelector(".reel-frame video"); v.currentTime = 4.4; v.pause(); });
  await p.waitForTimeout(800);
  await shot("live-07-reel-playing");
  log("video", JSON.stringify(await p.evaluate(() => { const v = document.querySelector(".reel-frame video"); return { t: v.currentTime, paused: v.paused, w: v.videoWidth }; })));
}
await b.close();
