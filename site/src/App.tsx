import { Compliance } from "./components/Compliance";
import { Film } from "./components/Film";
import { Flow } from "./components/Flow";
import { Hero } from "./components/Hero";
import { DemoButton, Nav } from "./components/Nav";
import { Results } from "./components/Results";
import { GITHUB_URL, LICENSE_URL } from "./content";
import { useInputMode } from "./lib/motion";

export function App() {
  useInputMode();
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Nav />
      <main id="main" tabIndex={-1}>
        <Hero />
        <Film />
        <Flow />
        <Compliance />
        <Results />
        <section className="wrap closing" aria-labelledby="closing-title">
          <h2 id="closing-title">See it on a real shoot.</h2>
          <p className="lede">
            The demo runs ShootAgent on AutoHDR’s hackathon shoots, with every step the agent takes in the trace beside
            the photos.
          </p>
          <div className="closing-actions">
            <DemoButton />
            <a className="button button-quiet" href={GITHUB_URL}>
              Read the code on GitHub
            </a>
          </div>
        </section>
      </main>
      <footer className="footer">
        <div className="wrap footer-inner">
          <p>
            Built by Sam Gu and Bryan Wang for the AITX &amp; MLOps World Community Hackathon, Austin Tech Week, October
            2026.
          </p>
          <p>
            <a href={LICENSE_URL}>MIT licensed</a>. Disclosures follow AB 723 (California Business and Professions Code
            §10140.8). Not legal advice.
          </p>
        </div>
      </footer>
    </>
  );
}
