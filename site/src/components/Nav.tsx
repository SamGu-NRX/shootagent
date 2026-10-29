import { useEffect, useState } from "react";
import { DEMO_URL, GITHUB_URL } from "../content";
import { Mark } from "./Brand";
import { ArrowUpRight } from "./Icons";

export function Nav() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <header className="nav" data-scrolled={scrolled || undefined}>
      <nav className="wrap nav-inner" aria-label="Main">
        <a className="brand" href="#top" aria-label="ShootAgent, top of the page">
          <Mark />
          ShootAgent
        </a>
        <div className="nav-links">
          <a href="#how">How it works</a>
          <a href="#run">Watch a run</a>
          <a href="#compliance">Compliance</a>
          <a href="#results">Results</a>
          <a href={GITHUB_URL}>GitHub</a>
          <a className="button button-primary nav-cta" href={DEMO_URL}>
            Open the live demo
          </a>
        </div>
      </nav>
    </header>
  );
}

export function DemoButton({ label = "Open the live demo" }: { label?: string }) {
  return (
    <a className="button button-primary" href={DEMO_URL}>
      {label} <ArrowUpRight />
    </a>
  );
}
