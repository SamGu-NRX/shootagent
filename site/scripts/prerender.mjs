// After `vite build` and `vite build --ssr src/entry-server.tsx --outDir dist-ssr`: render the page once and put
// the HTML into dist/index.html, where main.tsx hydrates it.
import { readFileSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const { render } = await import(pathToFileURL(`${root}dist-ssr/entry-server.js`).href);
const file = `${root}dist/index.html`;
const html = readFileSync(file, "utf8");
const empty = '<div id="root"></div>';
if (!html.includes(empty)) throw new Error(`no ${empty} in dist/index.html`);
// React adds an image preload for every eager <img> it renders; index.html already preloads the two the first
// screen needs, so drop the rest instead of letting them compete with it.
const body = render().replace(/<link rel="preload" as="image"[^>]*\/>/g, "");
writeFileSync(file, html.replace(empty, `<div id="root">${body}</div>`));
rmSync(`${root}dist-ssr`, { recursive: true, force: true });
console.log(`prerendered ${(body.length / 1024).toFixed(0)} KB of HTML into dist/index.html`);
