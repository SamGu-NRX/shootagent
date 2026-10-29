// Hash routes. #/r/<shoot>/<lead> is also the click URL of the realtor's push notification
// (src/shootagent/listing/routes.py), so its shape is a contract with the server.

export type Route =
  | { kind: "list" }
  | { kind: "shoot"; shootId: string }
  | { kind: "launch"; shootId: string }
  | { kind: "disclosure-preview"; shootId: string }
  | { kind: "buyer"; slug: string }
  | { kind: "disclosure"; slug: string }
  | { kind: "realtor"; shootId?: string; leadId?: string };

export function parseHash(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split(/[?]/)[0]!.split("/").filter(Boolean).map(decodeURIComponent);
  const [a, b, c, d] = parts;
  if (a === "s" && b) {
    if (c === "launch") return { kind: "launch", shootId: b };
    if (c === "disclosure") return { kind: "disclosure-preview", shootId: b };
    return { kind: "shoot", shootId: b };
  }
  if (a === "l" && b) return c === "disclosure" ? { kind: "disclosure", slug: b } : { kind: "buyer", slug: b };
  if (a === "r") return { kind: "realtor", shootId: b, leadId: c ?? d };
  return { kind: "list" };
}

