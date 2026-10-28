import { useEffect, useState } from "react";
import { DisclosurePage } from "./components/DisclosurePage";
import { ReviewPage } from "./components/ReviewPage";
import { ShootList } from "./components/ShootList";
import { Toast } from "./components/Toast";
import { Workspace } from "./components/Workspace";

type Route =
  | { kind: "list" }
  | { kind: "shoot"; shootId: string }
  | { kind: "public"; token: string; view: string | null }
  | { kind: "review"; token: string };

function parseHash(): Route {
  const h = location.hash;
  const d = h.match(/^#\/d\/([^/?#]+)(?:\/([^/?#]+))?/);
  if (d) return { kind: "public", token: decodeURIComponent(d[1]!), view: d[2] ? decodeURIComponent(d[2]) : null };
  const r = h.match(/^#\/r\/([^/?#]+)/);
  if (r) return { kind: "review", token: decodeURIComponent(r[1]!) };
  const m = h.match(/^#\/s\/([^/?#]+)/);
  return m ? { kind: "shoot", shootId: decodeURIComponent(m[1]!) } : { kind: "list" };
}

export const openShootRoute = (id: string) => {
  location.hash = `#/s/${encodeURIComponent(id)}`;
};
export const openListRoute = () => {
  location.hash = "#/";
};

export function App() {
  const [route, setRoute] = useState(parseHash);
  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  // The disclosure page and the review link are for people outside the studio: their own titles, light theme.
  useEffect(() => {
    document.title = route.kind === "public" ? "Photo disclosures" : route.kind === "review" ? "Photo review" : "ShootAgent";
  }, [route.kind]);
  return (
    <>
      {route.kind === "public" ? (
        <DisclosurePage token={route.token} focus={route.view} />
      ) : route.kind === "review" ? (
        <ReviewPage token={route.token} />
      ) : route.kind === "shoot" ? (
        <Workspace shootId={route.shootId} />
      ) : (
        <ShootList />
      )}
      <Toast />
    </>
  );
}
