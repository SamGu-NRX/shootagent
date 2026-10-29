// Records the listing-launch flow on the demo house as a 1920x1080 video for narration: the QC trace and a
// split view in the workspace, the launch storyboard and film, the QR code, the buyer page at phone size
// (a question and a tour booking), the realtor's lead, and the disclosure page.
//
//   node scripts/record-listing-walkthrough.mjs record <baseUrl> <shootId>      -> raw.mp4 + events.json in %TEMP%/sa-rec-listing
//   node scripts/record-listing-walkthrough.mjs finish <filmDir> <out.mp4>       -> H.264 + AAC, faststart
//
// Needs `shootagent serve` (it serves web/dist) with no DEMO_PASSCODE, and Microsoft Edge (Playwright's own
// Chromium can't decode the film's H.264). Frames come from the DevTools screencast at a fixed 30 fps against
// the wall clock, so the video runs in real time; the page draws its own pointer, since headless capture has
// none. The screencast has no sound, so `finish` lays the film's own audio under the seconds where the film
// plays (English, then Spanish from the shot the switch landed on) and leaves the rest silent for the narrator.
// <filmDir> is the shoot's film folder, holding en/film.mp4 and es/film.mp4.

import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

const DIR = join(tmpdir(), "sa-rec-listing");
const RAW = join(DIR, "raw.mp4");
const EVENTS = join(DIR, "events.json");
const FPS = 30;
const PHONE = { w: 390, h: 844, scale: 1.18 };

// The pointer. In the phone frame the page has two documents; each draws its own pointer, and the outer one
// hides while the mouse is over the phone.
const CURSOR_SCRIPT = `
(() => {
  const make = () => {
    if (document.getElementById("__rec_cursor")) return;
    const c = document.createElement("div");
    c.id = "__rec_cursor";
    c.innerHTML = '<svg width="22" height="26" viewBox="0 0 22 26"><path d="M2 2 L2 21 L7.2 16.4 L10.6 24 L14 22.5 L10.7 15 L18 15 Z" fill="#fff" stroke="#111" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, { position: "fixed", left: "0", top: "0", zIndex: "2147483647", pointerEvents: "none",
      transform: "translate(-100px,-100px)", filter: "drop-shadow(0 1px 2px rgba(0,0,0,.5))" });
    const ring = document.createElement("div");
    Object.assign(ring.style, { position: "fixed", left: "0", top: "0", width: "34px", height: "34px", margin: "-17px 0 0 -17px",
      borderRadius: "50%", border: "2px solid rgba(255,255,255,.9)", boxShadow: "0 0 0 1px rgba(0,0,0,.4)",
      zIndex: "2147483646", pointerEvents: "none", opacity: "0" });
    document.documentElement.append(ring, c);
    addEventListener("mousemove", (e) => {
      c.style.display = e.target && e.target.tagName === "IFRAME" ? "none" : "";
      c.style.transform = "translate(" + (e.clientX - 2) + "px," + (e.clientY - 2) + "px)";
    }, true);
    addEventListener("mouseout", (e) => { if (!e.relatedTarget) c.style.display = "none"; }, true);
    addEventListener("mousedown", (e) => {
      ring.style.transition = "none"; ring.style.opacity = "1";
      ring.style.transform = "translate(" + e.clientX + "px," + e.clientY + "px) scale(.6)";
      requestAnimationFrame(() => { ring.style.transition = "opacity 320ms ease-out, transform 320ms ease-out";
        ring.style.opacity = "0"; ring.style.transform = "translate(" + e.clientX + "px," + e.clientY + "px) scale(1.4)"; });
    }, true);
  };
  if (document.readyState === "loading") addEventListener("DOMContentLoaded", make); else make();
})();`;

const log = (...a) => console.log(...a);

async function record(base, shootId) {
  mkdirSync(DIR, { recursive: true });
  const browser = await chromium.launch({ channel: "msedge" });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await context.addInitScript(CURSOR_SCRIPT);
  const page = await context.newPage();
  page.on("pageerror", (e) => log("PAGEERROR", String(e)));
  page.on("console", (m) => m.type() === "error" && log("CONSOLE", m.text()));

  const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "mjpeg", "-i", "-",
    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "14", "-pix_fmt", "yuv420p", RAW], { stdio: ["pipe", "inherit", "inherit"] });
  const ffDone = new Promise((r) => ff.on("close", r));
  let last = null;
  let t0 = 0;
  let written = 0;
  const cdp = await context.newCDPSession(page);
  cdp.on("Page.screencastFrame", ({ data, sessionId }) => {
    last = Buffer.from(data, "base64");
    if (!t0) t0 = Date.now();
    cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  const pump = setInterval(() => {
    if (!last) return;
    const due = Math.floor(((Date.now() - t0) * FPS) / 1000);
    while (written < due) {
      ff.stdin.write(last);
      written++;
    }
  }, 10);

  const now = () => (t0 ? (Date.now() - t0) / 1000 : 0);
  const events = { shootId, marks: [], film: [] };
  const mark = (name, extra = {}) => {
    events.marks.push({ t: now(), name, ...extra });
    log(`[${now().toFixed(1)}s] ${name}`);
  };

  // ---- input --------------------------------------------------------------------------------------------
  let px = 960, py = 540;
  const pause = (ms) => page.waitForTimeout(ms);
  async function moveTo(x, y) {
    const d = Math.hypot(x - px, y - py);
    const steps = Math.max(8, Math.min(55, Math.round(d / 14)));
    for (let i = 1; i <= steps; i++) {
      const e = 1 - Math.pow(1 - i / steps, 3);
      await page.mouse.move(px + (x - px) * e, py + (y - py) * e);
      await pause(12);
    }
    px = x; py = y;
  }
  async function press(x, y, after = 450) {
    await moveTo(x, y);
    await pause(180);
    await page.mouse.down();
    await pause(90);
    await page.mouse.up();
    await pause(after);
  }
  async function centerOf(locator) {
    await locator.scrollIntoViewIfNeeded();
    const b = await locator.boundingBox();
    if (!b) throw new Error("no box for locator");
    return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  }
  const click = async (locator, after) => { const c = await centerOf(locator); await press(c.x, c.y, after); };
  async function wheel(x, y, total, step = 60, gap = 40) {
    await moveTo(x, y);
    for (let done = 0; Math.abs(done) < Math.abs(total); done += step * Math.sign(total)) {
      await page.mouse.wheel(0, step * Math.sign(total));
      await pause(gap);
    }
  }

  // phone frame: an iframe scaled up for legibility; clicks are placed from the element's box inside the frame
  const phone = () => page.frameLocator("#phone");
  async function phoneCenter(selector) {
    const loc = phone().locator(selector).first();
    await loc.waitFor();
    await loc.evaluate((el) => el.scrollIntoView({ block: "center", behavior: "instant" }));
    await pause(250);
    const r = await loc.evaluate((el) => { const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; });
    const f = await page.locator("#phone").boundingBox();
    return { x: f.x + (r.x + r.w / 2) * PHONE.scale, y: f.y + (r.y + r.h / 2) * PHONE.scale };
  }
  const phoneClick = async (selector, after) => { const c = await phoneCenter(selector); await press(c.x, c.y, after); };
  async function phoneType(selector, text) {
    await phoneClick(selector, 200);
    await page.keyboard.type(text, { delay: 60 });
    await pause(500);
  }
  async function showPhone(hash) {
    await page.setContent(`<!doctype html><html><body style="margin:0;height:100vh;display:grid;place-items:center;background:#131313">
      <div style="width:${PHONE.w * PHONE.scale + 24}px;height:${PHONE.h * PHONE.scale + 24}px;border-radius:${44 * PHONE.scale}px;background:#050505;
        box-shadow:0 0 0 1px #2b2b2b,0 40px 80px rgba(0,0,0,.6);display:grid;place-items:center">
        <div style="width:${PHONE.w * PHONE.scale}px;height:${PHONE.h * PHONE.scale}px;border-radius:${34 * PHONE.scale}px;overflow:hidden">
          <iframe id="phone" src="${base}/?api=live${hash}" style="width:${PHONE.w}px;height:${PHONE.h}px;border:0;transform:scale(${PHONE.scale});transform-origin:0 0"></iframe>
        </div></div></body></html>`);
  }
  const filmTime = () => page.evaluate(() => document.querySelector("video.film-video")?.currentTime ?? null);

  try {
    // ---- 1. workspace: the QC trace and a split view ------------------------------------------------------
    await page.goto(`${base}/?api=live#/s/${shootId}`);
    await cdp.send("Page.startScreencast", { format: "jpeg", quality: 90, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
    await page.locator(".strip-item").first().waitFor();
    await pause(1200);
    mark("workspace");
    await moveTo(900, 520);
    await pause(3500);
    const runPick = page.locator(".run-pick select");
    const qcRun = await runPick.locator("option").evaluateAll((os) => os.find((o) => o.textContent.startsWith("QC pass"))?.value);
    await click(runPick, 300);
    await runPick.selectOption(qcRun);
    await pause(900);
    mark("QC trace");
    await page.locator(".trace-scroll").evaluate((el) => { el.scrollTop = 0; });
    await pause(2500);
    await wheel(1690, 640, 2200, 40, 70);
    await pause(1500);
    await wheel(1690, 640, 20000, 120, 30);
    await pause(4500);
    await click(page.locator(".strip-item").nth(3), 400);
    mark("primary bathroom original");
    await moveTo(800, 560);
    await pause(5500);
    await click(page.locator(".strip-item").nth(4), 400);
    mark("primary bathroom part with the tub");
    await moveTo(820, 640);
    await pause(5500);

    // ---- 2. launch listing --------------------------------------------------------------------------------
    await click(page.getByRole("link", { name: /Launch listing/ }), 300);
    await page.locator(".sb-row").first().waitFor();
    await pause(1500);
    mark("launch listing");
    await moveTo(700, 420);
    await pause(4000);
    mark("storyboard rejections");
    const rows = page.locator(".sb-row");
    for (const i of [1, 2, 3]) {
      // distance from the top of the storyboard pane to this shot's row
      const dy = await rows.nth(i).evaluate((el) => el.getBoundingClientRect().top - el.closest(".launch-story").getBoundingClientRect().top - 8);
      await wheel(700, 520, Math.max(0, dy), 30, 45);
      await pause(i === 1 ? 6500 : 5500);
    }
    await page.locator(".launch-story").evaluate((el) => el.scrollTo({ top: 0, behavior: "smooth" }));
    await pause(1500);
    const video = page.locator("video.film-video");
    await click(video, 200);
    await page.waitForFunction(() => !document.querySelector("video.film-video")?.paused);
    events.film.push({ lang: "en", t: now(), at: await filmTime() });
    mark("film plays in English");
    await moveTo(185, 900);
    await pause(13500);
    await click(page.getByRole("radio", { name: "Español" }), 0);
    await pause(700);
    events.film.push({ lang: "es", t: now(), at: await filmTime() });
    mark("film switches to Spanish");
    await pause(5500);
    await page.evaluate(() => document.querySelector("video.film-video")?.pause());
    events.film.push({ lang: null, t: now(), at: await filmTime() });
    mark("film paused");
    await pause(800);

    // ---- 3. QR code and the buyer page --------------------------------------------------------------------
    await click(page.getByRole("button", { name: /Show QR code|Publish listing/ }), 300);
    const submit = page.locator("dialog.publish button[type=submit]");
    if (await submit.isVisible().catch(() => false)) await click(submit, 600);
    await page.locator(".qr").waitFor();
    mark("QR code");
    await moveTo(900, 470);
    await pause(5000);
    const href = await page.locator(".publish-live a.btn").getAttribute("href");
    const slug = href.split("#/l/")[1];
    events.slug = slug;
    await page.keyboard.press("Escape");
    await pause(500);

    await showPhone(`#/l/${slug}`);
    await phone().locator(".address").waitFor();
    mark("buyer page");
    await moveTo(1250, 540);
    await pause(6000);
    await phoneClick(".room-chip:nth-child(4)", 3500);
    mark("ask");
    await phoneType("#ask-input", "Is there a bathtub in the primary bath?");
    await phoneClick(".ask-send", 300);
    await phone().locator(".turn-a").first().waitFor({ timeout: 60_000 });
    await phone().locator(".turn-a").first().evaluate((el) => el.scrollIntoView({ block: "center", behavior: "smooth" }));
    mark("answer");
    await pause(7000);
    await phoneClick(".book-cta", 600);
    mark("book a tour");
    await phoneType(".sheet input >> nth=0", "Dana Whitfield");
    await phoneType(".sheet input >> nth=1", "dana@example.com");
    await phoneClick(".when-chip >> nth=0", 900);
    await phoneClick(".sheet button[type=submit]", 300);
    await phone().locator(".sheet-done").waitFor();
    mark("tour requested");
    await pause(5000);

    // ---- 4. the realtor's lead and the disclosure ---------------------------------------------------------
    const inbox = await (await page.request.get(`${base}/api/leads`)).json();
    const lead = inbox.leads.find((l) => l.name === "Dana Whitfield") ?? inbox.leads[0];
    await showPhone(`#/r/${shootId}/${lead.id}`);
    await phone().locator(".lead[data-focused]").waitFor();
    mark("realtor lead");
    await moveTo(1300, 540);
    await pause(4000);
    const leadBox = await phoneCenter(".lead[data-focused] .lead-qs");
    await moveTo(leadBox.x, leadBox.y);
    await pause(6000);

    // a different query string: the phone frame replaced the app's document in place, so a hash-only change of
    // the old URL would not load the app again
    await page.goto(`${base}/?api=live&view=disclosure#/l/${slug}/disclosure`);
    await page.locator(".pair").first().waitFor();
    await pause(1200);
    mark("disclosure");
    await moveTo(960, 600);
    await pause(3500);
    await wheel(960, 600, 1500, 30, 40);
    await pause(3000);
    await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" }));
    await pause(2500);
    mark("disclosure film section");
    await pause(4500);
    mark("end");
  } catch (e) {
    mark("driver error", { error: String(e).slice(0, 300) });
    log(String(e?.stack ?? e));
  } finally {
    writeFileSync(EVENTS, JSON.stringify(events, null, 1));
    await pause(800);
    clearInterval(pump);
    await cdp.send("Page.stopScreencast").catch(() => {});
    ff.stdin.end();
    await ffDone;
    await browser.close();
    log(`raw video ${RAW}, ${(written / FPS).toFixed(1)} s; events ${EVENTS}`);
  }
}

// ---- finish: film audio under the film segment, then the final encode -----------------------------------

function run(cmd, args) {
  return new Promise((resolve, reject) => {
    const p = spawn(cmd, args, { stdio: ["ignore", "inherit", "inherit"] });
    p.on("close", (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} exited ${code}`))));
  });
}

async function finish(filmDir, out) {
  const events = JSON.parse(readFileSync(EVENTS, "utf8"));
  const [en, es, stop] = events.film;
  if (!en || !es || !stop) throw new Error("events.json has no complete film segment; record again");
  const ms = (s) => Math.round(s * 1000);
  const graph = [
    `[1:a]atrim=start=${en.at.toFixed(3)}:duration=${(es.t - en.t).toFixed(3)},asetpts=PTS-STARTPTS,afade=t=out:st=${(es.t - en.t - 0.3).toFixed(3)}:d=0.3,adelay=${ms(en.t)}|${ms(en.t)}[a0]`,
    `[2:a]atrim=start=${es.at.toFixed(3)}:duration=${(stop.t - es.t).toFixed(3)},asetpts=PTS-STARTPTS,afade=t=out:st=${(stop.t - es.t - 0.4).toFixed(3)}:d=0.4,adelay=${ms(es.t)}|${ms(es.t)}[a1]`,
    `[a0][a1]amix=inputs=2:normalize=0,apad[a]`,
  ].join(";");
  await run("ffmpeg", ["-y", "-loglevel", "error", "-i", RAW, "-i", join(filmDir, "en", "film.mp4"), "-i", join(filmDir, "es", "film.mp4"),
    // the screencast's JPEG frames are full range; players expect TV-range yuv420p
    "-filter_complex", `${graph};[0:v]scale=in_range=pc:out_range=tv,format=yuv420p[v]`, "-map", "[v]", "-map", "[a]", "-shortest",
    "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p", "-color_range", "tv", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
    "-movflags", "+faststart", out]);
  log(`wrote ${out}`);
  for (const m of events.marks) log(`${Math.floor(m.t / 60)}:${String(Math.floor(m.t % 60)).padStart(2, "0")}  ${m.name}`);
}

const [cmd, a, b] = process.argv.slice(2);
if (cmd === "record") await record(a ?? "http://127.0.0.1:8000", b ?? "c46bbca496");
else if (cmd === "finish") await finish(a, b);
else throw new Error("usage: record <baseUrl> <shootId> | finish <filmDir> <out.mp4>");
