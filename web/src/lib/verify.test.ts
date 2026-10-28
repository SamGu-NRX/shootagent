import { describe, expect, it } from "vitest";
import { canonical, classifyRequest, disclosureText, sha256Text } from "../api/mock/delivery";
import type { ChainEntry, Change } from "../api/types";
import { verifyChain } from "./verify";

async function chain(bodies: Record<string, unknown>[]): Promise<ChainEntry[]> {
  const out: ChainEntry[] = [];
  for (const b of bodies) {
    const body = canonical({ ...b, prev: out.length ? out[out.length - 1]!.hash : null });
    out.push({ hash: await sha256Text(body), body });
  }
  return out;
}

describe("edit record verification", () => {
  it("accepts an intact chain and names the record that was changed", async () => {
    const c = await chain([{ step: "captured" }, { step: "edit", what: "car removed" }, { step: "published" }]);
    expect((await verifyChain(c)).ok).toBe(true);
    const forged = c.map((e, i) => (i === 1 ? { ...e, body: e.body.replace("car removed", "nothing") } : e));
    const res = await verifyChain(forged);
    expect(res.ok).toBe(false);
    expect(res.problems[0]).toMatch(/Record 2/);
    const reordered = [c[0]!, c[2]!, c[1]!];
    expect((await verifyChain(reordered)).ok).toBe(false);
  });

  it("hashes canonical text the same way the server does (sorted keys, no spaces)", async () => {
    expect(canonical({ b: 1, a: [true, null, "x"] })).toBe('{"a":[true,null,"x"],"b":1}');
    // sha256("abc"), a standard test vector
    expect(await sha256Text("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
});

describe("demo-data listing photo policy", () => {
  it.each([
    ["remove the grey car", "disclose"],
    ["half a stop brighter on every photo", "exempt"],
    ["remove the power lines", "decline"],
    ["fix the crack in the driveway", "decline"],
    ["add a pool to the backyard", "decline"],
    ["add fire to the fireplace", "disclose"],
    ["add a 'Listed by Acme' watermark", "overlay"],
  ])("%s -> %s", (text, want) => {
    expect(classifyRequest(text).verdict).toBe(want);
  });

  it("writes one disclosure sentence from the alterations only", () => {
    const c = (kind: Change["kind"], what: string, category = "removal"): Change => ({ kind, what, by: "shootagent", category });
    expect(disclosureText([c("alteration", "car removed."), c("adjustment", "exposure +0.50 EV")])).toBe("Digitally altered: car removed.");
    expect(disclosureText([c("alteration", "virtually staged", "staging"), c("alteration", "sofa added")])).toBe("Virtually staged: sofa added.");
    expect(disclosureText([c("restoration", "lamp restored")])).toBeNull();
  });
});
