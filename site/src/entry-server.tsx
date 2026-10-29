import { renderToString } from "react-dom/server";
import { App } from "./App";

/** The page's HTML at build time (scripts/prerender.mjs), so it reads and links before any script runs. */
export const render = () => renderToString(<App />);
