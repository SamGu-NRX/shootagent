// Drives the UI against the in-browser demo data and saves screenshots to web/screenshots/.
// Needs `npm run dev` on :5173. Usage: node scripts/screenshots.mjs [baseUrl]
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const base = process.argv[2] ?? "http://localhost:5173";
const out = new URL("../screenshots/", import.meta.url);
await mkdir(fileURLToPath(out), { recursive: true });
const shot = (page, name) => page.screenshot({ path: fileURLToPath(new URL(`${name}.jpg`, out)), type: "jpeg", quality: 82 });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 960 }, deviceScaleFactor: 1 });
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));

await page.goto(`${base}/?api=mock&speed=2#/`);
await page.getByRole("heading", { name: "Shoots" }).waitFor();
await page.waitForTimeout(400);
await shot(page, "01-shoots");

await page.getByRole("button", { name: /QC tour/ }).click();
await page.getByRole("button", { name: "Run QC", exact: true }).first().waitFor();
await page.waitForTimeout(800);
await shot(page, "02-workspace-fresh");

await page.getByRole("button", { name: "Run QC", exact: true }).first().click();
await page.getByText("Retry · attempt 2").first().waitFor({ timeout: 30000 });
await page.waitForTimeout(2600);
await shot(page, "03-trace-live-retry");

await page.getByText("Run finished").waitFor({ timeout: 60000 });
await page.waitForTimeout(600);
await page.locator(".strip-item").first().click();
await page.waitForTimeout(600);
await shot(page, "04-trace-done-living-room");

// failed first attempt vs passing fix, side by side
await page.locator(".ver", { hasText: "v01.1" }).click();
await page.waitForTimeout(500);
await shot(page, "05-failed-attempt-preview");

await page.locator(".strip-item").nth(1).click();
await page.getByRole("tab", { name: "Chat" }).click();
await page.getByRole("button", { name: "Make the kitchen brighter in every photo" }).click();
await page.getByText(/Brightened/).waitFor({ timeout: 30000 });
await page.waitForTimeout(800);
await shot(page, "06-chat-results");

// click-to-mask
await page.locator(".strip-item").nth(4).click();
await page.waitForTimeout(500);
const stage = page.locator(".stage");
const box = await stage.boundingBox();
await page.keyboard.down("Shift");
await page.mouse.click(box.x + box.width * 0.2, box.y + box.height * 0.18);
await page.keyboard.up("Shift");
await page.getByText(/of frame/).waitFor();
await page.waitForTimeout(700);
await shot(page, "07-click-to-mask");

// zoomed pixel inspection
await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
for (let i = 0; i < 6; i++) await page.mouse.wheel(0, -240);
await page.waitForTimeout(400);
await shot(page, "08-zoom");

await page.setViewportSize({ width: 820, height: 1180 });
await page.keyboard.press("0");
await page.getByRole("tab", { name: "Trace" }).click();
await page.waitForTimeout(600);
await shot(page, "09-narrow");

await page.setViewportSize({ width: 1600, height: 960 });
await page.goto(`${base}/?api=live#/`);
await page.waitForTimeout(1500);
await shot(page, "10-live-unreachable");

await browser.close();
console.log(errors.length ? `console errors:\n${errors.join("\n")}` : "no console errors");
