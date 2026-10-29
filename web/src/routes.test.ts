import { describe, expect, it } from "vitest";
import { parseHash } from "./routes";

describe("parseHash", () => {
  it("opens the lead a push notification links to", () => {
    expect(parseHash("#/r/c46bbca496/l_a5b1fbaa77")).toEqual({ kind: "realtor", shootId: "c46bbca496", leadId: "l_a5b1fbaa77" });
  });
  it("routes public slugs to the buyer page and its disclosure", () => {
    expect(parseHash("#/l/c46bbca496-f1f99192cdc3")).toEqual({ kind: "buyer", slug: "c46bbca496-f1f99192cdc3" });
    expect(parseHash("#/l/c46bbca496-f1f99192cdc3/disclosure")).toEqual({ kind: "disclosure", slug: "c46bbca496-f1f99192cdc3" });
  });
});
