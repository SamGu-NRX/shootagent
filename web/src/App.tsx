import { useEffect, useState } from "react";
import { BuyerPage } from "./components/listing/BuyerPage";
import { DisclosurePage } from "./components/listing/DisclosurePage";
import { LaunchView } from "./components/listing/LaunchView";
import { RealtorView } from "./components/listing/RealtorView";
import { ShootList } from "./components/ShootList";
import { Toast } from "./components/Toast";
import { Workspace } from "./components/Workspace";
import { parseHash } from "./routes";

export const openShootRoute = (id: string) => {
  location.hash = `#/s/${encodeURIComponent(id)}`;
};
export const openListRoute = () => {
  location.hash = "#/";
};

export function App() {
  const [route, setRoute] = useState(() => parseHash(location.hash));
  useEffect(() => {
    const on = () => {
      setRoute(parseHash(location.hash));
      window.scrollTo(0, 0);
      // a screen reader hears nothing when a hash route swaps the view; move focus to the new view's heading
      requestAnimationFrame(() =>
        requestAnimationFrame(() => document.querySelector<HTMLElement>("h1[tabindex='-1']")?.focus({ preventScroll: true })),
      );
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  // The public pages are light and scroll as a document; the workspace surfaces are dark, fixed-height apps.
  const day = route.kind === "buyer" || route.kind === "disclosure" || route.kind === "disclosure-preview";
  useEffect(() => {
    document.documentElement.dataset.surface = day ? "day" : route.kind === "realtor" ? "phone" : "app";
    // the browser's top bar follows the page: daylight paper for buyers, the darkroom for everything else
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", day ? "#f1f2ef" : "#161616");
    document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", day ? "light" : "dark");
    const titles: Record<string, string> = {
      launch: "Launch listing · ShootAgent",
      buyer: "Listing",
      disclosure: "Photos and film: what changed",
      "disclosure-preview": "Disclosure preview · ShootAgent",
      realtor: "Leads · ShootAgent",
    };
    document.title = titles[route.kind] ?? "ShootAgent";
  }, [day, route.kind]);
  return (
    <>
      {route.kind === "list" && <ShootList />}
      {route.kind === "shoot" && <Workspace shootId={route.shootId} />}
      {route.kind === "launch" && <LaunchView shootId={route.shootId} />}
      {route.kind === "disclosure-preview" && <DisclosurePage source={{ kind: "private", shootId: route.shootId }} />}
      {route.kind === "buyer" && <BuyerPage slug={route.slug} />}
      {route.kind === "disclosure" && <DisclosurePage source={{ kind: "public", slug: route.slug }} />}
      {route.kind === "realtor" && <RealtorView shootId={route.shootId} leadId={route.leadId} />}
      {!day && <Toast />}
    </>
  );
}
