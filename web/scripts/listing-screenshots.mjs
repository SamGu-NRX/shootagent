// Screenshots of the listing surfaces at desktop width and at 390 px, saved to web/screenshots/listing/.
// Needs the UI (npm run dev) and a server with a published-able shoot that has a film.
// Usage: node scripts/listing-screenshots.mjs <baseUrl> <shootId> [--mock]
// With --mock, pass any shoot id: the script creates the "QC tour" demo shoot and navigates by hash, because
// demo data lives in the page and a reload would drop it.
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const [base = "http://localhost:5173", shootId, flag] = process.argv.slice(2);
const mock = flag === "--mock";
if (!shootId) throw new Error("usage: node scripts/listing-screenshots.mjs <baseUrl> <shootId> [--mock]");
const out = new URL(`../screenshots/listing/${mock ? "demo-data" : "live"}/`, import.meta.url);
await mkdir(fileURLToPath(out), { recursive: true });
const q = mock ? "?api=mock" : "?api=live";

const browser = await chromium.launch();
const errors = [];
const sizes = { desk: { width: 1440, height: 900 }, phone: { width: 390, height: 844 } };

async function page(size) {
  const ctx = await browser.newContext({
    viewport: sizes[size],
    deviceScaleFactor: size === "phone" ? 2 : 1,
    isMobile: size === "phone",
    hasTouch: size === "phone",
  });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(`${size}: ${e}`));
  p.on("console", (m) => m.type() === "error" && errors.push(`${size}: ${m.text()}`));
  return p;
}
async function snap(p, name, full = false) {
  if (full) {
    // lazy images load only near the viewport: walk the page first so a full-page shot has them
    const h = await p.evaluate(() => document.documentElement.scrollHeight);
    for (let y = 0; y < h; y += 600) {
      await p.evaluate((top) => window.scrollTo(0, top), y);
      await p.waitForTimeout(120);
    }
    await p.waitForLoadState("networkidle").catch(() => {});
    await p.evaluate(() => window.scrollTo(0, 0));
    await p.waitForTimeout(300);
  }
  await p.screenshot({ path: fileURLToPath(new URL(`${name}.jpg`, out)), fullPage: full, type: "jpeg", quality: 80 });
}

async function go(p, hash) {
  if (!mock) return p.goto(`${base}/${q}${hash}`);
  await p.evaluate((h) => (location.hash = h), hash);
}

for (const size of ["desk", "phone"]) {
  const p = await page(size);
  let id = shootId;
  if (mock) {
    await p.goto(`${base}/${q}#/`);
    await p.getByRole("button", { name: /QC tour/ }).click();
    await p.waitForURL(/#\/s\//);
    id = decodeURIComponent(p.url().split("#/s/")[1]);
  }

  // 1. launch view
  await go(p, `#/s/${id}/launch`);
  await p.locator(".sb-row").first().waitFor({ timeout: 20000 });
  await p.waitForTimeout(1200);
  await snap(p, `1-launch-${size}`, size === "phone");
  if (size === "desk") {
    await p.locator(".sb-row").nth(1).locator(".sb-pick").click();
    await p.waitForTimeout(800);
    await snap(p, `1-launch-shot2-${size}`);
  }

  // publish
  await p.getByRole("button", { name: /Publish listing|Show QR code/ }).first().click();
  await p.locator("dialog.publish .modal-head").waitFor();
  await p.waitForTimeout(300);
  const publishBtn = p.locator("dialog.publish button[type=submit]");
  if (await publishBtn.isVisible()) {
    await snap(p, `1-publish-details-${size}`);
    await publishBtn.click();
  }
  await p.locator(".qr").waitFor();
  await p.waitForTimeout(700);
  await snap(p, `1-publish-qr-${size}`);
  const href = await p.locator(".publish-live a.btn").getAttribute("href");
  await p.keyboard.press("Escape");
  const slug = href.split("#/l/")[1];

  // 2. buyer page
  await go(p, `#/l/${slug}`);
  await p.locator(".address").waitFor();
  await p.waitForTimeout(1500);
  await snap(p, `2-buyer-${size}`);
  await snap(p, `2-buyer-full-${size}`, true);
  await p.locator(".room-chip").nth(2).click();
  await p.waitForTimeout(600);
  await p.locator("#ask-input").fill("Is there a bathtub in the primary bathroom?");
  await p.locator(".ask-send").click();
  await p.locator(".turn-a").first().waitFor({ timeout: 60000 });
  await p.locator("#ask-input").fill("What are the HOA fees?");
  await p.locator(".ask-send").click();
  await p.locator(".turn-a").nth(1).waitFor({ timeout: 60000 });
  await p.locator(".ask").scrollIntoViewIfNeeded();
  await p.waitForTimeout(400);
  await snap(p, `2-buyer-ask-${size}`);
  await p.getByRole("button", { name: "ES", exact: true }).first().click();
  await p.waitForTimeout(1200);
  await p.evaluate(() => window.scrollTo(0, 0));
  await snap(p, `2-buyer-es-${size}`);
  await p.getByRole("button", { name: "EN", exact: true }).first().click();
  await p.locator(size === "desk" ? ".book-inline" : ".book-cta").click();
  await p.locator(".sheet input").first().waitFor();
  await p.waitForTimeout(450);
  await p.locator(".sheet input").nth(0).fill(size === "desk" ? "Dana Whitfield" : "José Díaz");
  await p.locator(".sheet input").nth(1).fill(size === "desk" ? "dana@example.com" : "+1 512 555 0142");
  await p.locator(".when-chip").first().click();
  await snap(p, `2-book-${size}`);
  await p.locator(".sheet button[type=submit]").click();
  await p.locator(".sheet-done").waitFor();
  await p.waitForTimeout(600);
  await snap(p, `2-book-sent-${size}`);

  // 4. disclosure
  await go(p, `#/l/${slug}/disclosure`);
  await p.locator(".pair").first().waitFor();
  await p.waitForTimeout(1000);
  await snap(p, `4-disclosure-${size}`);
  await snap(p, `4-disclosure-full-${size}`, true);

  // 3. realtor view
  await go(p, `#/r`);
  await p.locator(".lead").first().waitFor();
  await p.waitForTimeout(500);
  await snap(p, `3-realtor-${size}`, size === "phone");
  await p.close();
}

await browser.close();
if (errors.length) {
  console.log("console errors:\n" + errors.join("\n"));
  process.exitCode = 1;
}
