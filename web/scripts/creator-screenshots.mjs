// Drives the creator dashboard and saves screenshots to web/screenshots/creator-*.jpg: the recorded lab in demo
// mode (a live replay for the mid-run shots, then the finished lab) at 1600x960 and 1440x900, and, when the API
// answers, a live lab. Needs a server for the built app, e.g.
//   SHOOTAGENT_API=http://127.0.0.1:8011 npx vite preview --port 5179 --strictPort
// Usage: node scripts/creator-screenshots.mjs [baseUrl] [liveLabPath]
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://localhost:5179";
const live = process.argv[3] ?? "64170ca0a0/20261028-215529-df468d";
const out = new URL("../screenshots/", import.meta.url);
await mkdir(fileURLToPath(out), { recursive: true });

const browser = await chromium.launch();
const errors = [];

async function open(width, height) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  page.on("console", (m) => m.type() === "error" && errors.push(`${width}: ${m.text()}`));
  page.on("pageerror", (e) => errors.push(`${width}: ${e}`));
  const shot = (name) => page.screenshot({ path: fileURLToPath(new URL(`creator-${name}.jpg`, out)), type: "jpeg", quality: 84 });
  const toSection = async (id) => {
    await page.evaluate((s) => document.getElementById(s)?.scrollIntoView({ block: "start" }), id);
    await page.waitForTimeout(500);
  };
  // puts an element just under the sticky stage bar
  const toElement = async (selector, gap = 18) => {
    await page.evaluate(
      ([sel, g]) => {
        const el = document.querySelector(sel);
        const bar = document.querySelector(".cl-stages")?.getBoundingClientRect().height ?? 0;
        if (el) window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - bar - g);
      },
      [selector, gap],
    );
    await page.waitForTimeout(500);
  };
  return { page, shot, toSection, toElement };
}

// ---- 1600 x 960: the recorded lab, replayed live, then finished --------------------------------------
{
  const { page, shot, toSection, toElement } = await open(1600, 960);
  await page.goto(`${base}/?api=mock&speed=2#/creator/64170ca0a0`);
  await page.locator('.cl-pill[data-status="done"]').waitFor();
  await page.waitForTimeout(600);
  await shot("top");

  await page.getByRole("button", { name: "Run creative lab" }).first().click();
  await page.locator('.cl-pill[data-status="running"]').waitFor();
  await page.locator('.cl-step[data-kind="critique"]').first().waitFor({ timeout: 60_000 });
  await page.waitForTimeout(1500);
  await toSection("cl-room");
  await shot("room-midrun");

  await page.locator('.cl-pill[data-status="done"]').waitFor({ timeout: 90_000 });
  await page.waitForTimeout(800);
  await toSection("cl-room");
  await shot("room");

  await page.locator(".cl-fin-title").first().click();
  await page.locator(".cl-drawer[open]").waitFor();
  await page.waitForTimeout(600);
  await shot("drawer");
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);

  await toSection("cl-reels");
  await page.getByRole("button", { name: "Play all in sync" }).click();
  await page.waitForTimeout(2600);
  await shot("reels");
  await page.getByRole("button", { name: "Pause all" }).click();

  await toSection("cl-scorecard");
  await shot("scorecard");
  await page.getByRole("button", { name: /^Full scorecard/ }).click();
  await page.waitForTimeout(500);
  await toElement(".cl-full");
  await shot("scorecard-full");

  await toSection("cl-launch");
  await page.getByRole("button", { name: "Simulate launch" }).click();
  await page.waitForTimeout(9000);
  await shot("sim-midrun");
  await page.locator('.cl-banner[data-state="won"]').waitFor({ timeout: 30_000 });
  await page.waitForTimeout(900);
  await shot("sim-done");
  await page.close();
}

// ---- 1440 x 900: the finished lab ------------------------------------------------------------------------
{
  const { page, shot, toSection } = await open(1440, 900);
  await page.goto(`${base}/?api=mock#/creator/64170ca0a0`);
  await page.locator('.cl-pill[data-status="done"]').waitFor();
  await page.waitForTimeout(600);
  await shot("1440-top");
  for (const s of ["room", "reels", "scorecard", "launch"]) {
    await toSection(`cl-${s}`);
    if (s === "launch") {
      await page.getByRole("button", { name: "Skip to the end" }).click();
      await page.waitForTimeout(600);
    }
    await shot(`1440-${s}`);
  }
  await page.close();
}

// ---- a live lab through the API, when one answers ----------------------------------------------------------
{
  const { page, shot, toSection } = await open(1600, 960);
  try {
    await page.goto(`${base}/?api=live#/creator/${live}`);
    await page.locator(".cl-pill").waitFor({ timeout: 8000 });
    await page.waitForTimeout(800);
    await toSection("cl-room");
    await shot("live-room");
    await toSection("cl-scorecard");
    await shot("live-scorecard");
  } catch {
    console.log("no live API: skipped the live lab");
  }
  await page.close();
}

await browser.close();
console.log(errors.length ? `console errors:\n${errors.join("\n")}` : "no console errors");
