// Drives the creator dashboard against the demo replay and saves screenshots to web/screenshots/creator-*.jpg.
// Needs a dev server: npx vite --port 5179 --strictPort. Usage: node scripts/creator-screenshots.mjs [baseUrl]
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://localhost:5179";
const out = new URL("../screenshots/", import.meta.url);
await mkdir(fileURLToPath(out), { recursive: true });
const shot = (page, name) => page.screenshot({ path: fileURLToPath(new URL(`creator-${name}.jpg`, out)), type: "jpeg", quality: 84 });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 960 }, deviceScaleFactor: 1 });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

const toSection = async (id) => {
  await page.evaluate((s) => document.getElementById(s)?.scrollIntoView({ block: "start" }), id);
  await page.waitForTimeout(500);
};
// puts an element just under the sticky header and stepper
const toElement = async (selector) => {
  await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    const sticky = document.querySelector(".cl-sticky")?.getBoundingClientRect().height ?? 0;
    if (el) window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - sticky - 14);
  }, selector);
  await page.waitForTimeout(500);
};

// the recorded lab, then a fresh run replayed live (speed 2: about 38 s)
await page.goto(`${base}/?api=mock&speed=2#/creator/64170ca0a0`);
await page.locator(".cl-status", { hasText: "Done in" }).waitFor();
await page.getByRole("button", { name: "Run creative lab" }).first().click();
await page.locator(".live-pill.cl-status").waitFor();

// the room mid-run: critics at work
await page.locator('.cl-step[data-status="running"]', { hasText: "Critique" }).waitFor({ timeout: 60_000 });
await page.waitForTimeout(3200);
await toSection("cl-room");
await shot(page, "room-midrun");

// the reels while they render
await page.locator('.cl-step[data-status="running"]', { hasText: "Render" }).waitFor({ timeout: 60_000 });
await page.waitForTimeout(1200);
await toSection("cl-reels");
await shot(page, "reels-rendering");

// done
await page.locator(".cl-status", { hasText: "Done in" }).waitFor({ timeout: 90_000 });
await page.waitForTimeout(800);
await toSection("cl-room");
await page.locator(".cl-feed-scroll").evaluate((el) => el.scrollTo({ top: el.scrollHeight }));
await page.waitForTimeout(400);
await shot(page, "room-done");

// a concept in the drawer
await page.locator(".cl-card[data-finalist] .cl-card-title button").first().click();
await page.locator(".cl-drawer[open]").waitFor();
await page.waitForTimeout(600);
await shot(page, "drawer");
await page.keyboard.press("Escape");
await page.waitForTimeout(300);

// the reels, playing in sync
await toSection("cl-reels");
await page.getByRole("button", { name: "Play all in sync" }).click();
await page.waitForTimeout(2600);
await shot(page, "reels");
await page.getByRole("button", { name: "Pause all" }).click();

// the scorecard, with a map point hovered
await toSection("cl-scorecard");
await shot(page, "scorecard");
await toElement(".cl-dims");
await page.locator('.cl-map-pt[data-kind="finalist"]').first().hover();
await page.waitForTimeout(400);
await shot(page, "scorecard-map");
await toElement(".cl-h2h");
await page.locator(".cl-vcell").nth(5).click();
await page.waitForTimeout(300);
await shot(page, "scorecard-audience");

// the launch: mid-run, then the winner
await toSection("cl-launch");
await page.getByRole("button", { name: "Simulate launch" }).click();
await page.waitForTimeout(9000);
await shot(page, "sim-midrun");
await page.locator('.cl-banner[data-state="won"]').waitFor({ timeout: 30_000 });
await page.waitForTimeout(900);
await shot(page, "sim-done");

// narrowest supported width
await page.setViewportSize({ width: 1280, height: 900 });
await toSection("cl-room");
await shot(page, "room-1280");

await browser.close();
console.log(errors.length ? `console errors:\n${errors.join("\n")}` : "no console errors");
