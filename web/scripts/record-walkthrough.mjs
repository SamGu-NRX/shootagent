// Records a real end-to-end showcase run as a silent 1920x1080 video for the Loom and the fallback demo.
//
//   node scripts/record-walkthrough.mjs record [baseUrl] [--mock]   -> raw.mp4 + events.json in %TEMP%/sa-rec
//   node scripts/record-walkthrough.mjs cut <out.mp4>               -> trims idle waits, prints mapped timestamps
//
// Needs `shootagent serve` (it serves web/dist) and Microsoft Edge: Playwright's own Chromium can't decode
// the reel's H.264. Frames come from the DevTools screencast and go to ffmpeg at a fixed 30 fps against the
// wall clock, so the raw video runs in real time. Headless screencasts don't draw the pointer, so the page
// gets a cursor overlay that follows real mouse events.
//
// The cut step only removes time inside waits for the agent where the trace added nothing for more than
// IDLE_S seconds; it keeps the first and last moments of each gap, so the clock visibly jumps instead of
// the trace being sped up.

import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

const DIR = join(tmpdir(), "sa-rec");
const RAW = join(DIR, "raw.mp4");
const EVENTS = join(DIR, "events.json");
const FPS = 30;
const IDLE_S = 8;
const KEEP_HEAD_S = 2.5;
const KEEP_TAIL_S = 1.5;


// ---- recording ----------------------------------------------------------------------------------------

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
    ring.id = "__rec_ring";
    Object.assign(ring.style, { position: "fixed", left: "0", top: "0", width: "34px", height: "34px", margin: "-17px 0 0 -17px",
      borderRadius: "50%", border: "2px solid rgba(255,255,255,.9)", boxShadow: "0 0 0 1px rgba(0,0,0,.4)",
      zIndex: "2147483646", pointerEvents: "none", opacity: "0", transition: "opacity 260ms ease-out, transform 260ms ease-out" });
    document.documentElement.append(ring, c);
    addEventListener("mousemove", (e) => { c.style.transform = "translate(" + (e.clientX - 2) + "px," + (e.clientY - 2) + "px)"; }, true);
    addEventListener("mousedown", (e) => {
      ring.style.transition = "none"; ring.style.opacity = "1";
      ring.style.transform = "translate(" + e.clientX + "px," + e.clientY + "px) scale(.6)";
      requestAnimationFrame(() => { ring.style.transition = "opacity 320ms ease-out, transform 320ms ease-out";
        ring.style.opacity = "0"; ring.style.transform = "translate(" + e.clientX + "px," + e.clientY + "px) scale(1.4)"; });
    }, true);
  };
  if (document.readyState === "loading") addEventListener("DOMContentLoaded", make); else make();
})();`;

async function record(base, mock) {
  mkdirSync(DIR, { recursive: true });
  const browser = await chromium.launch({ channel: "msedge" });
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  await context.addInitScript(CURSOR_SCRIPT);
  const page = await context.newPage();
  page.on("pageerror", (e) => log("PAGEERROR", String(e)));
  page.on("console", (m) => m.type() === "error" && log("CONSOLE", m.text()));

  // ---- frame pump: newest screencast frame, written FPS times a second of wall clock --------------------
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
  await page.goto(`${base}/?api=${mock ? "mock&speed=1" : "live"}#/`);
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 88, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });

  const now = () => (t0 ? (Date.now() - t0) / 1000 : 0);
  const events = { marks: [], waits: [] };
  const mark = (name, note = "") => {
    events.marks.push({ t: now(), name, note });
    log(`[${now().toFixed(1)}s] ${name}${note ? ` (${note})` : ""}`);
  };

  // ---- human-ish input --------------------------------------------------------------------------------
  let px = 960, py = 540;
  const pause = (ms) => page.waitForTimeout(ms);
  async function moveTo(x, y) {
    const d = Math.hypot(x - px, y - py);
    const steps = Math.max(8, Math.min(55, Math.round(d / 14)));
    for (let i = 1; i <= steps; i++) {
      const k = i / steps;
      const e = 1 - Math.pow(1 - k, 3); // ease-out
      await page.mouse.move(px + (x - px) * e, py + (y - py) * e);
      await pause(12);
    }
    px = x; py = y;
  }
  async function center(locator) {
    await locator.scrollIntoViewIfNeeded();
    const b = await locator.boundingBox();
    if (!b) throw new Error("no box for locator");
    return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  }
  async function click(locator, { hold = 90, after = 450 } = {}) {
    const c = await center(locator);
    await moveTo(c.x, c.y);
    await pause(180);
    await page.mouse.down();
    await pause(hold);
    await page.mouse.up();
    await pause(after);
  }
  async function type(locator, text) {
    await click(locator, { after: 200 });
    await page.keyboard.type(text, { delay: 55 });
    await pause(500);
  }
  async function drag(fromX, fromY, toX, toY, steps = 40) {
    await moveTo(fromX, fromY);
    await pause(200);
    await page.mouse.down();
    for (let i = 1; i <= steps; i++) {
      const k = i / steps;
      const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2; // ease-in-out
      await page.mouse.move(fromX + (toX - fromX) * e, fromY + (toY - fromY) * e);
      await pause(22);
    }
    await page.mouse.up();
    px = toX; py = toY;
    await pause(300);
  }
  const imgBox = () => page.locator(".layer:not([hidden]) .imgbox").last().boundingBox();
  async function zoomAt(fx, fy, notches) {
    const r = await imgBox();
    const x = r.x + r.width * fx, y = r.y + r.height * fy;
    await moveTo(x, y);
    await pause(300);
    for (let i = 0; i < notches; i++) {
      await page.mouse.wheel(0, -180);
      await pause(140);
    }
    await pause(500);
    return { x, y };
  }
  async function slider(toX) {
    const h = await page.locator(".split-handle").boundingBox();
    await drag(h.x + h.width / 2, h.y + h.height / 2, toX, h.y + h.height / 2, 45);
  }

  /** Waits for `done()` while logging when the trace changes, for the idle cut and the key-moment hooks. */
  async function waitAgent(label, done, hooks = [], maxMs = 15 * 60_000) {
    const w = { label, start: now(), end: 0, changes: [] };
    let sig = "";
    const fired = new Set();
    const start = Date.now();
    while (Date.now() - start < maxMs) {
      // counts only: anything that ticks on its own (the live clocks) would hide idle time
      const s = await page.evaluate(() => {
        const q = (sel) => document.querySelectorAll(sel).length;
        const tools = document.querySelectorAll(".tape-stats dd")[1]?.textContent ?? "";
        const chat = /(\d+) tool call/.exec(document.querySelector(".exchange:last-child .msg-status")?.textContent ?? "")?.[1] ?? "";
        return [q(".timeline > li"), q(".tr-tool[data-status=ok], .tr-tool[data-status=failed]"), q(".result"),
          q(".reel-steps li[data-status=ok]"), q(".verdict-list li"), tools, chat].join("|");
      });
      if (s !== sig) {
        sig = s;
        w.changes.push(now());
      }
      for (const h of hooks) {
        if (fired.has(h.name)) continue;
        if (await page.locator(h.selector).count()) {
          fired.add(h.name);
          mark(h.name, h.note ?? "");
          if (h.hover) {
            const loc = page.locator(h.selector).first();
            const b = await loc.boundingBox().catch(() => null);
            if (b && b.y > 100 && b.y < 1000) await moveTo(b.x + Math.min(160, b.width / 2), b.y + 14);
          }
          if (h.hold) await pause(h.hold);
        }
      }
      if (await done()) break;
      await pause(500);
    }
    w.end = now();
    events.waits.push(w);
    mark(`${label} finished`);
  }

  const save = () => writeFileSync(EVENTS, JSON.stringify(events, null, 1));
  try {
    if (mock) await mockFlow();
    else await showcaseFlow();
  } catch (e) {
    mark("driver error", String(e).slice(0, 300));
    log(String(e?.stack ?? e));
  } finally {
    save();
    await pause(1500);
    clearInterval(pump);
    await cdp.send("Page.stopScreencast").catch(() => {});
    ff.stdin.end();
    await ffDone;
    await browser.close();
    log(`raw video ${RAW}, ${(written / FPS).toFixed(1)} s; events ${EVENTS}`);
  }

  // ---- flows ------------------------------------------------------------------------------------------
  async function shootJson(id) {
    return page.evaluate(async (sid) => (await fetch(`/api/shoots/${sid}`)).json(), id);
  }
  function stripItem(i) {
    return page.locator(".strip-item").nth(i);
  }
  function runEnded(n) {
    return async () => (await page.locator(".tr-end").count()) >= n;
  }
  async function latestExchangeDone() {
    return (await page.locator(".exchange").count()) > 0 && (await page.locator(".exchange").last().locator(".msg-status").count()) === 0;
  }

  async function showcaseFlow() {
    await page.locator(".start-card[data-primary]").waitFor();
    await moveTo(700, 400);
    mark("start screen");
    await pause(3000);
    await click(page.locator(".start-card[data-primary]"), { after: 0 });
    mark("create showcase shoot");
    await page.waitForURL(/#\/s\//, { timeout: 300_000 });
    const shootId = decodeURIComponent(page.url().split("#/s/")[1]);
    events.shootId = shootId;
    save();
    await page.locator(".strip-item").first().waitFor();
    await pause(1500);
    mark("workspace", shootId);
    await moveTo(110, 500);
    await pause(1200);
    await click(page.getByRole("button", { name: "Run QC", exact: true }).first(), { after: 300 });
    mark("Run QC pressed");
    await moveTo(1690, 600);
    await waitAgent("QC run", runEnded(1), [
      { name: "plan", selector: ".plan" },
      { name: "first candidates", selector: ".cand-chips" },
      { name: "regroup (split)", selector: ".tr-tool:has(.tr-tool-name:text-is('regroup brackets'))" },
      { name: "first failed check", selector: ".tr-check[data-passed=false]", hover: true, hold: 2500 },
      { name: "retry", selector: ".tr-retry", hover: true, hold: 2000 },
      { name: "first passed check", selector: ".tr-check[data-passed=true]", hover: true, hold: 1500 },
      { name: "verdicts", selector: ".tr-verdicts" },
    ]);
    await pause(4000);
    mark("QC summary");
    // verdict block: expand the dismissals so the reasons show
    const more = page.locator(".tr-verdicts .link-quiet");
    if (await more.count()) {
      await page.locator(".tr-verdicts").first().evaluate((el) => el.scrollIntoView({ block: "center" }));
      await pause(800);
      await click(more.first(), { after: 3500 });
      mark("dismissal reasons");
    }

    // ---- stop sign ----
    let shoot = await shootJson(shootId);
    const signIdx = shoot.views.findIndex((v) => v.meta?.case_id === "defect_train_0016" && !v.derived_from);
    if (signIdx >= 0) {
      await click(stripItem(signIdx), { after: 1500 });
      mark("stop-sign photo");
      const v = shoot.views[signIdx];
      const f = v.findings.find((x) => x.status === "fixed" && x.region) ?? v.findings.find((x) => x.region && x.status !== "dismissed");
      const r = f?.region ?? [0.2, 0.37, 0.24, 0.47];
      const pt = await zoomAt((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, 4);
      const h = await page.locator(".split-handle").boundingBox();
      await slider(pt.x + 160);
      await pause(600);
      await slider(pt.x - 140);
      await pause(600);
      await slider(pt.x);
      mark("slider across the stop sign", f ? `finding ${f.status}` : "no finding recorded");
      await pause(3000);
      await moveTo(h.x, h.y); // leave the photo
      await page.keyboard.press("0");
      await pause(800);
    } else mark("stop-sign photo missing");

    // ---- chat across the shoot ----
    await click(page.getByRole("tab", { name: "Chat" }));
    await type(page.locator("#chat-input"), "remove the cars and trash bins");
    await page.keyboard.press("Enter");
    mark("chat: remove the cars and trash bins");
    await pause(1200);
    await waitAgent("chat 1", latestExchangeDone, [
      { name: "chat 1 first edit check", selector: ".exchange .msg-status" },
    ]);
    await pause(1500);
    const ex1 = page.locator(".exchange").last();
    await ex1.evaluate((el) => el.scrollIntoView({ block: "start" }));
    await pause(1500);
    mark("chat 1 results");
    const skipped = ex1.locator(".skipped summary");
    if (await skipped.count()) {
      await click(skipped.first(), { after: 2500 });
      mark("not-needed reasons");
    }
    const applied = ex1.locator(".result[data-outcome=applied]");
    if (await applied.count()) {
      await click(applied.first(), { after: 1500 });
      const r = await imgBox();
      await slider(r.x + r.width * 0.25);
      await pause(700);
      await slider(r.x + r.width * 0.75);
      await pause(700);
      await slider(r.x + r.width * 0.5);
      mark("car removal before/after");
      await pause(2500);
    } else mark("chat 1 applied nothing");

    // ---- TV with a click mask ----
    shoot = await shootJson(shootId);
    const tvIdx = shoot.views.findIndex((v) => v.meta?.case_id === "request-0e142faf3b55390045" && !v.derived_from);
    if (tvIdx >= 0) {
      await click(stripItem(tvIdx), { after: 1500 });
      mark("bedroom TV photo");
      const r = await imgBox();
      await moveTo(r.x + r.width * 0.27, r.y + r.height * 0.39);
      await pause(400);
      await page.keyboard.down("Shift");
      await page.mouse.down(); await pause(90); await page.mouse.up();
      await pause(700);
      await moveTo(r.x + r.width * 0.22, r.y + r.height * 0.34);
      await page.mouse.down(); await pause(90); await page.mouse.up();
      await page.keyboard.up("Shift");
      mark("shift-click mask");
      await page.locator(".mask-card", { hasText: "of frame" }).waitFor({ timeout: 180_000 });
      await pause(2000);
      await click(page.getByRole("button", { name: "Use in chat" }));
      await type(page.locator("#chat-input"), "put the reference image on this screen");
      await page.keyboard.press("Enter");
      mark("chat: put the reference image on this screen");
      await pause(1200);
      await waitAgent("chat 2", latestExchangeDone);
      await pause(1500);
      await page.locator(".exchange").last().evaluate((el) => el.scrollIntoView({ block: "start" }));
      mark("chat 2 results");
      await pause(3000);
      await click(page.getByRole("tab", { name: "Trace" }));
      const failed = page.locator(".tr-check[data-passed=false]");
      if (await failed.count()) {
        await failed.first().evaluate((el) => el.scrollIntoView({ block: "center" }));
        await pause(600);
        const b = await failed.first().boundingBox();
        await moveTo(b.x + 120, b.y + 20);
        mark("TV: failed check then retry");
        await pause(3500);
      }
      const pt = await zoomAt(0.275, 0.39, 2);
      await slider(pt.x - 120);
      await pause(500);
      await slider(pt.x + 60);
      mark("TV before/after");
      await pause(3000);
      await page.keyboard.press("0");
    } else mark("TV photo missing");

    // ---- approve ----
    shoot = await shootJson(shootId);
    const parents = new Set(shoot.views.map((v) => v.derived_from).filter(Boolean));
    const brightness = await page.evaluate(() =>
      [...document.querySelectorAll(".strip-item .strip-thumb img")].map((img) => {
        try {
          const c = document.createElement("canvas");
          c.width = 24; c.height = 16;
          const g = c.getContext("2d");
          g.drawImage(img, 0, 0, 24, 16);
          const d = g.getImageData(0, 0, 24, 16).data;
          let s = 0;
          for (let i = 0; i < d.length; i += 4) s += d[i] + d[i + 1] + d[i + 2];
          return s / (d.length / 4) / 3;
        } catch { return 255; }
      }),
    );
    const toApprove = shoot.views
      .map((v, i) => ({ v, i }))
      .filter(({ v, i }) => !parents.has(v.id) && (brightness[i] ?? 255) > 45);
    mark("approve photos", `${toApprove.length} of ${shoot.views.length}`);
    for (const { i } of toApprove) {
      await click(stripItem(i), { after: 300 });
      const btn = page.locator(".versions .btn[aria-pressed]");
      if ((await btn.getAttribute("aria-pressed")) !== "true") await click(btn, { after: 350 });
    }

    // ---- reel ----
    await click(page.getByRole("tab", { name: "Reel" }));
    await click(page.getByRole("button", { name: /Build reel|Rebuild reel/ }));
    mark("build reel");
    await waitAgent("reel run", async () =>
      (await page.locator('.reel-frame[data-state="ready"]').count()) > 0 ||
      (await page.getByText(/stopped before a video|didn't load/).count()) > 0, [], 10 * 60_000);
    if (await page.locator('.reel-frame[data-state="ready"]').count()) {
      await page.evaluate(() => { const v = document.querySelector(".reel-frame video"); if (v) { v.currentTime = 0; v.play(); } });
      mark("reel playing");
      const c = await center(page.locator(".reel-frame"));
      await moveTo(c.x + 220, c.y + 260);
      const dur = await page.evaluate(() => document.querySelector(".reel-frame video")?.duration ?? 24);
      await pause(Math.min(40, dur + 1) * 1000);
      mark("reel ended");
    } else mark("reel failed");
    await pause(1500);
  }

  async function mockFlow() {
    await page.locator(".start-card").first().waitFor();
    mark("start screen");
    await pause(1500);
    await click(page.getByRole("button", { name: /QC tour/ }), { after: 0 });
    await page.waitForURL(/#\/s\//);
    await page.locator(".strip-item").first().waitFor();
    await pause(1000);
    await click(page.getByRole("button", { name: "Run QC", exact: true }).first());
    mark("Run QC pressed");
    await moveTo(1690, 600);
    await waitAgent("QC run", runEnded(1), [
      { name: "first failed check", selector: ".tr-check[data-passed=false]", hover: true, hold: 2000 },
      { name: "retry", selector: ".tr-retry", hover: true, hold: 1500 },
    ]);
    await click(stripItem(0), { after: 800 });
    const r = await imgBox();
    await slider(r.x + r.width * 0.3);
    await slider(r.x + r.width * 0.7);
    mark("slider");
    await click(page.getByRole("tab", { name: "Chat" }));
    await type(page.locator("#chat-input"), "Make the kitchen brighter in every photo");
    await page.keyboard.press("Enter");
    await waitAgent("chat 1", latestExchangeDone);
    mark("chat results");
    await pause(2000);
  }
}

// ---- cutting ------------------------------------------------------------------------------------------

async function cut(out) {
  if (!out) throw new Error("cut needs an output path");
  const events = JSON.parse(readFileSync(EVENTS, "utf8"));
  const cuts = [];
  for (const w of events.waits) {
    const pts = [w.start, ...w.changes.filter((t) => t > w.start && t < w.end), w.end];
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1], b = pts[i];
      if (b - a > IDLE_S) cuts.push([a + KEEP_HEAD_S, b - KEEP_TAIL_S, w.label]);
    }
  }
  const removed = cuts.reduce((s, [a, b]) => s + (b - a), 0);
  const mapT = (t) => t - cuts.filter(([a]) => a < t).reduce((s, [a, b]) => s + (Math.min(t, b) - a), 0);
  const expr = cuts.map(([a, b]) => `between(t\\,${a.toFixed(3)}\\,${b.toFixed(3)})`).join("+");
  const vf = cuts.length ? `select='not(${expr})',setpts=N/FRAME_RATE/TB` : "null";
  const args = ["-y", "-loglevel", "error", "-i", RAW, "-vf", vf, "-r", String(FPS), "-c:v", "libx264", "-preset", "slow",
    "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", out];
  await new Promise((res, rej) => {
    const p = spawn("ffmpeg", args, { stdio: "inherit" });
    p.on("close", (c) => (c === 0 ? res() : rej(new Error(`ffmpeg exited ${c}`))));
  });
  const fmt = (t) => {
    const s = Math.round(t);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };
  console.log(`shoot ${events.shootId ?? "?"}; cut ${cuts.length} idle stretches, ${removed.toFixed(0)} s removed`);
  for (const [a, b, label] of cuts) console.log(`  cut ${label}: raw ${a.toFixed(1)}-${b.toFixed(1)} (${(b - a).toFixed(0)} s) at ${fmt(mapT(a))}`);
  for (const m of events.marks) console.log(`${fmt(mapT(m.t))}  ${m.name}${m.note ? ` (${m.note})` : ""}`);
  writeFileSync(join(DIR, "timeline.json"), JSON.stringify({ cuts, marks: events.marks.map((m) => ({ ...m, final: mapT(m.t) })) }, null, 1));
}

function log(...a) {
  console.log(new Date().toISOString().slice(11, 19), ...a);
}

const cmd = process.argv[2];
if (cmd === "record") await record(process.argv[3] ?? "http://127.0.0.1:8000", process.argv.includes("--mock"));
else if (cmd === "cut") await cut(process.argv[3]);
else {
  console.error("usage: record [baseUrl] [--mock] | cut <out.mp4>");
  process.exit(2);
}
