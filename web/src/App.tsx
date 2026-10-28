import { useEffect, useState } from "react";
import { ShootList } from "./components/ShootList";
import { Toast } from "./components/Toast";
import { Workspace } from "./components/Workspace";

function parseHash(): { shootId: string | null } {
  const m = location.hash.match(/^#\/s\/([^/?#]+)/);
  return { shootId: m ? decodeURIComponent(m[1]!) : null };
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
      {route.shootId ? <Workspace shootId={route.shootId} /> : <ShootList />}
      <Toast />
    </>
  );
}
