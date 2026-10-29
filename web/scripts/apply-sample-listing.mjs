// Writes the sample listing details (src/listing/sample-listing.json) to a shoot through the API.
// Usage: node scripts/apply-sample-listing.mjs <apiBase> <shootId>   e.g. http://127.0.0.1:8000 c46bbca496
// Sends DEMO_PASSCODE from the environment when the server needs one.
import { readFile } from "node:fs/promises";

const [base, shootId] = process.argv.slice(2);
if (!base || !shootId) throw new Error("usage: node scripts/apply-sample-listing.mjs <apiBase> <shootId>");
const sample = JSON.parse(await readFile(new URL("../src/listing/sample-listing.json", import.meta.url), "utf8"));
const headers = { "content-type": "application/json" };
if (process.env.DEMO_PASSCODE) headers["x-demo-passcode"] = process.env.DEMO_PASSCODE;
const res = await fetch(`${base.replace(/\/$/, "")}/api/shoots/${encodeURIComponent(shootId)}/listing`, {
  method: "PATCH",
  headers,
  body: JSON.stringify(sample),
});
if (!res.ok) throw new Error(`The server answered ${res.status}: ${await res.text()}`);
console.log(`Sample listing written to ${shootId}: ${sample.address}`);
