import { lazy, Suspense, useEffect, useState } from "react";
import { ShootList } from "./components/ShootList";
import { Toast } from "./components/Toast";
import { Workspace } from "./components/Workspace";

const CreatorPage = lazy(() => import("./creator/CreatorPage"));

function parseHash(): { shootId: string | null; creator: { shootId: string; labId: string | null } | null } {
  const m = location.hash.match(/^#\/s\/([^/?#]+)/);
  const c = location.hash.match(/^#\/creator\/([^/?#]+)(?:\/([^/?#]+))?/);
  return {
    shootId: m ? decodeURIComponent(m[1]!) : null,
    creator: c ? { shootId: decodeURIComponent(c[1]!), labId: c[2] ? decodeURIComponent(c[2]) : null } : null,
  };
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
  return (
    <>
      {route.creator ? (
        <Suspense fallback={null}>
          <CreatorPage shootId={route.creator.shootId} labId={route.creator.labId} />
        </Suspense>
      ) : route.shootId ? (
        <Workspace shootId={route.shootId} />
      ) : (
        <ShootList />
      )}
      <Toast />
    </>
  );
}
