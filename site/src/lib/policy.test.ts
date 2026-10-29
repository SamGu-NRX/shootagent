import { describe, expect, it } from "vitest";
import { classify, REASONS } from "./policy";
import fixture from "./policy.fixture.json";

// Parity with the product. Each case in policy.fixture.json is one request and the product's answer to it, from
// classify_request in src/shootagent/compliance/policy.py (branch dev, with the staging fix): the verdict, and each
// part's text, verdict and category. The port must give exactly the same answer, part for part.
//
// The groups: "page" is the box's six examples and its default text; "reviewer" the 103 requests of the port review;
// "tricky" phrasings that stress the splitter
// and the rules (negations, lists, "between", twilight, staging, sky, lawn, fire, TV, hex colours, sizes, the
// neighbour's house, power lines, cracks, mould); "engine" inputs where Python's re and JS RegExp part ways (Unicode
// \b and \w, Python's \s and str.strip(), "." and \r, re.I with "ı" and "İ", len() in code points).
//
// No case is exempt. What policy.ts cannot reproduce: Python 3.12 has Unicode 15.0 and browsers ship newer
// versions, so a letter added since then is a \w here but not in the product; and under the i flag JS counts U+0345
// (a Greek combining mark) as a \w. No request in the fixture uses either.
//
// AutoHDR's saved customer requests also match (115 distinct texts, checked when the port was written) but stay out
// of this public fixture; the product's own test reads them from AUTOHDR_EDITOR_REQUESTS.
//
// To rebuild the fixture after policy.py changes, run classify_request over the same texts and store, per case,
// {text, verdict, items: [[part.text, part.verdict, part.category], ...]}, plus REASONS as "reasons".

type Case = { text: string; verdict: string; items: string[][] };
const groups: Record<string, Case[]> = fixture.groups;

const title = (text: string) => JSON.stringify(text.length > 70 ? `${text.slice(0, 70)}…` : text);

for (const [group, cases] of Object.entries(groups)) {
  describe(group, () => {
    it.each(cases.map((c) => [title(c.text), c] as const))("%s", (_, c) => {
      const got = classify(c.text);
      expect({ verdict: got.verdict, items: got.items.map((i) => [i.text, i.verdict, i.category]) }).toEqual({
        verdict: c.verdict,
        items: c.items,
      });
    });
  });
}

it("gives the product's reasons word for word", () => {
  expect(REASONS).toEqual(fixture.reasons);
});

it("gives every part the reason for its category", () => {
  for (const c of Object.values(groups).flat()) {
    for (const i of classify(c.text).items) expect(i.reason).toBe(REASONS[i.category]);
  }
});
