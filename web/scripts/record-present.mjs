// Records the presenter (src/creator/present) to an MP4 for whoever cuts the demo video, plus a PNG still at
// the middle and end of each scene. It plays the recorded lab in demo mode (?api=mock), so it needs no API,
// only a server for the web app: the dev server (npx vite) or a build served with
//   npx vite preview --port 5179 --strictPort            (in web/)
// Usage, from the repo root:
//   node web/scripts/record-present.mjs [baseUrl] [--scenes hook,pitch] [--out data/present/present.mp4] [--fps 30] [--no-stills]
// baseUrl defaults to http://127.0.0.1:5179. Scene ids and durations are read from src/creator/present/scenes.ts.
//
// Each scene is recorded on its own: seek to it at 0 ms, play, stop at its duration. Chrome's screencast sends a
// JPEG whenever the page repaints; the frames keep their timestamps, and ffmpeg turns them into constant-rate
// H.264, holding a frame for as long as it was on screen.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, mkdtemp, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const MOCK = join(ROOT, "web/public/mock/creative");
const W = 1920;
const H = 1080;

// ---- arguments ----------------------------------------------------------------------------------------

const args = process.argv.slice(2);
const VALUED = new Set(["--scenes", "--out", "--fps"]);
const opts = { base: "http://127.0.0.1:5179", scenes: null, out: null, fps: 30, stills: true };
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (VALUED.has(a)) {
    const v = args[++i];
    if (v == null) fail(`${a} needs a value`);
    if (a === "--scenes") opts.scenes = v.split(",").map((s) => s.trim()).filter(Boolean);
    else if (a === "--out") opts.out = resolve(process.cwd(), v);
    else opts.fps = Number(v);
  } else if (a === "--no-stills") opts.stills = false;
  else if (a.startsWith("--")) fail(`unknown option ${a}`);
  else opts.base = a.replace(/\/$/, "");
}
if (!(opts.fps > 0 && opts.fps <= 120)) fail("--fps must be between 1 and 120");

function fail(msg) {
  console.error(`record-present: ${msg}`);
  process.exit(1);
}

const FFMPEG = ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"].find((p) => existsSync(p)) ?? "ffmpeg";
const FFPROBE = FFMPEG.replace(/ffmpeg$/, "ffprobe");

// ---- the scene list -------------------------------------------------------------------------------------

async function sceneList() {
  const src = await readFile(join(ROOT, "web/src/creator/present/scenes.ts"), "utf8");
  const list = [];
  // one object literal per scene: `id: "hook", ... duration: 7000,`
  for (const m of src.matchAll(/\bid:\s*"([^"]+)"[^{}]*?\bduration:\s*([\d_]+)/g)) list.push({ id: m[1], duration: Number(m[2].replace(/_/g, "")) });
  if (!list.length) fail("found no scenes in web/src/creator/present/scenes.ts");
  return list.map((s, index) => ({ ...s, index }));
}

const all = await sceneList();
const wanted = opts.scenes ? opts.scenes.map((id) => all.find((s) => s.id === id) ?? fail(`no scene "${id}"; scenes: ${all.map((s) => s.id).join(", ")}`)) : all;
const total = wanted.reduce((a, s) => a + s.duration, 0);
const out = opts.out ?? join(ROOT, "data/present", opts.scenes ? `present-${wanted.map((s) => s.id).join("+")}.mp4` : "present.mp4");
const stillsDir = join(ROOT, "data/present/stills");
await mkdir(dirname(out), { recursive: true });
if (opts.stills) await mkdir(stillsDir, { recursive: true });

// ---- the page -----------------------------------------------------------------------------------------------

const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required", "--hide-scrollbars"] });
const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));

const url = `${opts.base}/?api=mock&present=1&clean=1&autoplay=0&scene=${wanted[0].id}#/creator/64170ca0a0`;
try {
  await page.goto(url, { waitUntil: "load" });
  await page.locator(".pz-stage").waitFor({ timeout: 30_000 });
  await page.waitForFunction(() => !!window.__present, null, { timeout: 30_000 });
} catch (e) {
  await browser.close();
  fail(`the presenter did not open at ${url}: ${String(e).split("\n")[0]}`);
}
const pageTotal = await page.evaluate(() => window.__present.state().total);
if (pageTotal !== all.length) console.warn(`warning: scenes.ts lists ${all.length} scenes but the page has ${pageTotal}; is the server serving this checkout?`);

// warm the cache with the demo's media, then mount each wanted scene once so images decode before the take
const media = [];
for (const [dir, ext] of [["reels", /\.(mp4|jpg)$/], ["views-hd", /\.jpg$/], ["views", /\.jpg$/]]) {
  const files = existsSync(join(MOCK, dir)) ? await readdir(join(MOCK, dir)) : [];
  for (const f of files) if (ext.test(f)) media.push(`/mock/creative/${dir}/${f}`);
}
await page.evaluate((urls) => Promise.all(urls.map((u) => fetch(u).then((r) => r.arrayBuffer()).catch(() => null))), media);
await page.evaluate(() => document.fonts.ready);
for (const s of wanted) {
  await page.evaluate((i) => window.__present.seek(i, 0), s.index);
  await page.waitForTimeout(400);
}
await page.evaluate((i) => window.__present.seek(i, 0), wanted[0].index);
await page.waitForTimeout(1500);

/** Once scene `s` is playing, waits until its clock reaches `ms` (or the presenter has moved on), then pauses. */
async function playUntil(s, ms) {
  // state() reflects the last render, so first wait for the play to land
  await page.waitForFunction(([i]) => {
    const st = window.__present.state();
    return st.playing && st.scene === i;
  }, [s.index], { polling: 16, timeout: 10_000 });
  await page.waitForFunction(
    ([i, until]) => {
      const st = window.__present.state();
      return st.scene !== i || st.t >= until || !st.playing;
    },
    [s.index, ms],
    { polling: 16, timeout: s.duration + 15_000 },
  );
  await page.evaluate(() => window.__present.pause());
}

// ---- the take: a screencast, one scene at a time --------------------------------------------------------------

const work = await mkdtemp(join(tmpdir(), "present-"));
const cdp = await page.context().newCDPSession(page);
const frames = [];
const writes = [];
cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
  cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  const file = join(work, `f${String(frames.length).padStart(6, "0")}.jpg`);
  frames.push({ file, ts: metadata.timestamp ?? Date.now() / 1000 });
  writes.push(writeFile(file, Buffer.from(data, "base64")));
});
await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: W, maxHeight: H, everyNthFrame: 1 });
await page.waitForTimeout(300);

const segments = [];
for (const s of wanted) {
  const start = Date.now() / 1000;
  await page.evaluate((i) => {
    window.__present.seek(i, 0);
    window.__present.play();
  }, s.index);
  await playUntil(s, s.duration - 30);
  segments.push({ scene: s, start, end: start + s.duration / 1000 });
  if (!(await page.locator(".pz-stage").count())) console.warn(`warning: the presenter was gone by the end of ${s.id} (a render error?)`);
  await page.waitForTimeout(120);
}
await cdp.send("Page.stopScreencast");
await Promise.all(writes);

// each segment keeps the frames shown inside it; a frame lasts until the next one, the last until the cut
const lines = ["ffconcat version 1.0"];
const perScene = [];
let last = null;
for (const seg of segments) {
  const mine = frames.filter((f) => f.ts >= seg.start && f.ts < seg.end);
  perScene.push(`${seg.scene.id} ${mine.length} frames (${(mine.length / (seg.scene.duration / 1000)).toFixed(1)} fps)`);
  if (!mine.length) {
    console.warn(`warning: no frames for scene ${seg.scene.id}`);
    continue;
  }
  mine.forEach((f, i) => {
    const from = i === 0 ? seg.start : f.ts;
    const to = i + 1 < mine.length ? mine[i + 1].ts : seg.end;
    lines.push(`file '${basename(f.file)}'`, `duration ${Math.max(0.001, to - from).toFixed(4)}`);
  });
  last = mine[mine.length - 1];
}
if (!last) fail("the screencast sent no frames");
lines.push(`file '${basename(last.file)}'`); // the concat demuxer applies the last duration only to a repeated entry
await writeFile(join(work, "frames.txt"), `${lines.join("\n")}\n`);

const ff = spawnSync(
  FFMPEG,
  [
    "-y", "-hide_banner", "-loglevel", "error",
    "-f", "concat", "-safe", "0", "-i", join(work, "frames.txt"),
    // screencast JPEGs are full range; editors and players expect limited-range yuv420p
    "-vf", `fps=${opts.fps},scale=${W}:${H}:flags=lanczos:in_range=pc:out_range=tv,format=yuv420p`,
    "-t", (total / 1000).toFixed(3),
    "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-color_range", "tv", "-movflags", "+faststart",
    out,
  ],
  { stdio: ["ignore", "inherit", "inherit"] },
);
if (ff.status !== 0) fail(`ffmpeg failed (exit ${ff.status}); frames kept in ${work}`);
const captured = segments.reduce((n, seg) => n + frames.filter((f) => f.ts >= seg.start && f.ts < seg.end).length, 0);
await rm(work, { recursive: true, force: true });

// ---- stills: play each scene again and pause at its middle and just before its end --------------------------

const stills = [];
if (opts.stills) {
  for (const s of wanted) {
    await page.evaluate((i) => {
      window.__present.seek(i, 0);
      window.__present.play();
    }, s.index);
    for (const [name, at] of [["mid", s.duration / 2], ["end", s.duration - 250]]) {
      await playUntil(s, at);
      await page.waitForTimeout(250);
      const file = join(stillsDir, `${s.id}-${name}.png`);
      await page.screenshot({ path: file });
      stills.push(file);
      if (name === "mid") await page.evaluate(() => window.__present.play());
    }
  }
}
await browser.close();

// ---- report ---------------------------------------------------------------------------------------------------

const probe = spawnSync(FFPROBE, ["-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=width,height,nb_read_frames,r_frame_rate:format=duration", "-of", "json", out], { encoding: "utf8" });
let info = "";
try {
  const j = JSON.parse(probe.stdout);
  const st = j.streams[0];
  info = `${st.width}x${st.height}, ${Number(j.format.duration).toFixed(2)} s, ${st.nb_read_frames} frames at ${st.r_frame_rate}`;
} catch {
  info = "(ffprobe unavailable)";
}
const rel = (p) => (p.startsWith(ROOT) ? p.slice(ROOT.length) : p);
console.log(`video   ${rel(out)}  ${info}`);
console.log(`capture ${captured} frames over ${(total / 1000).toFixed(1)} s = ${(captured / (total / 1000)).toFixed(1)} fps (${perScene.join("; ")})`);
if (stills.length) console.log(`stills  ${stills.length} in ${rel(stillsDir)}/`);
console.log(errors.length ? `page errors:\n${errors.join("\n")}` : "no page errors");
