// The listing photo policy, abridged from the product (src/shootagent/compliance/policy.py). The product's version
// is the one that decides; this copy answers the box on the page the same way for the common phrasings.

export type Verdict = "decline" | "disclose" | "overlay" | "exempt" | "unclear";

export interface Item {
  text: string;
  verdict: Verdict;
  category: string;
  reason: string;
}

const TRUE_PICTURE = "Listing photos must show the property as it is (NAR Code of Ethics, Article 12; MLS photo rules).";

export const REASONS: Record<string, string> = {
  surroundings: `Power lines, poles, towers, roads and neighbouring buildings are part of the property's surroundings; removing them misrepresents the property. ${TRUE_PICTURE}`,
  condition: `Hiding damage or wear (cracks, stains, leaks, mould, rot) misrepresents the property's condition. ${TRUE_PICTURE}`,
  view: `Changing or inventing the view misrepresents the property's location. ${TRUE_PICTURE}`,
  feature: `Adding a feature the property doesn't have (a pool, a room, a fireplace) misrepresents it. ${TRUE_PICTURE}`,
  structure: `Removing walls, windows, doors or built-ins changes the property's structure in the photo. ${TRUE_PICTURE}`,
  proportions: `Stretching or enlarging a space misrepresents its size. ${TRUE_PICTURE}`,
  gear: "The photographer's equipment isn't part of the property. Removing it isn't an alteration.",
  overlay: "Text and graphics laid over the photo, like a watermark or lot lines, aren't alterations of the property.",
  staging: "Virtual staging is allowed. The photo is marked virtually staged and paired with the original.",
  sky: "A replaced sky changes the scene. The photo is marked digitally altered and paired with the original.",
  lawn: "A greener lawn changes how the property looks. The photo is marked digitally altered.",
  fire: "A fire in a real fireplace is a common enhancement. The photo is marked digitally altered.",
  screen: "A new picture on a TV is a minor change, but the photo is still marked digitally altered.",
  colour: "Changing paint, flooring or cabinet colour changes physical features. The photo is marked digitally altered.",
  removal: "Removing objects is a digital alteration under AB 723. The photo is marked as altered and paired with the original.",
  addition: "Adding objects is a digital alteration under AB 723. The photo is marked as altered and paired with the original.",
  change: "Changing what the scene shows is a digital alteration under AB 723. The photo is marked as altered.",
  adjustment: "Exposure, colour correction, straightening and sharpening aren't alterations under AB 723. No disclosure needed.",
  unclear: "Not one the check recognises. The agent decides from the photo and discloses any alteration.",
};

const RULES: [RegExp, Verdict, string][] = [
  [/\b(?:power|utility|telephone|overhead|electric(?:al)?)\s+(?:lines?|wires?|cables?|poles?)\b|\bneighbou?r(?:'s|s)?\s+(?:house|home|building|garage|roof)s?\b|\b(?:cell|radio|transmission)\s+towers?\b|\b(?:highway|freeway|railroad|train\s+tracks?|billboards?)\b/i, "decline", "surroundings"],
  [/\bcrack(?:s|ed)?\b|\bwater\s+(?:stains?|damage)\b|\bmou?ld\b|\bpeeling\b|\bholes?\s+in\b|\b(?:ceiling|wall)\s+stains?\b|\bstains?\s+on\s+the\s+(?:ceiling|walls?)\b|\brot(?:ten)?\b|\bleak(?:s|ing)?\b/i, "decline", "condition"],
  [/\b(?:ocean|sea|lake|mountain|city|beach|golf)\s+views?\b|\b(?:replace|swap|change)\s+(?:the\s+)?view\b/i, "decline", "view"],
  [/\b(?:add|put|build|insert)\s+(?:a\s+|an\s+)?(?:swimming\s+)?(?:pool|hot\s+tub|deck|fireplace|balcony|room|window|garage)\b(?!\s+(?:deck|furniture|chairs?))/i, "decline", "feature"],
  [/\b(?:remove|erase|delete)\s+(?:the\s+)?(?:walls?|windows?|doors?|stairs?|staircase|columns?|beams?)\b(?!\s+(?:art|mats?|decor|clocks?|stickers?))/i, "decline", "structure"],
  [/\b(?:look|seem|appear)\s+(?:much\s+)?(?:bigger|larger|more\s+spacious|wider)\b/i, "decline", "proportions"],
  [/\b(?:tripod|camera|photographer|light\s+stand)s?\b/i, "exempt", "gear"],
  [/\b(?:watermarks?|logos?|lot\s+lines?|property\s+lines?|captions?)\b/i, "overlay", "overlay"],
  [/\bvirtual(?:ly)?\s+stag\w*|\bstag(?:e|ing)\b|\bfurnish\w*/i, "disclose", "staging"],
  [/\b(?:sky|skies|clouds?)\b/i, "disclose", "sky"],
  [/\b(?:lawn|grass)\b/i, "disclose", "lawn"],
  [/\b(?:fire|flames?)\b(?!place)/i, "disclose", "fire"],
  [/\b(?:tv|television|screen)\b/i, "disclose", "screen"],
  [/#[0-9a-f]{3,6}\b|\bcolou?r\b|\bpaint\w*|\brecolou?r\w*/i, "disclose", "colour"],
  [/\b(?:remove|erase|delete|take\s+out|get\s+rid\s+of|declutter|clear)\b/i, "disclose", "removal"],
  [/\b(?:bright\w*|dark\w*|lighten|exposure|warm\w*|cool\w*|white\s+balance|straighten\w*|sharpen\w*|contrast)\b/i, "exempt", "adjustment"],
  [/\b(?:add|insert|place|put)\b/i, "disclose", "addition"],
  [/\b(?:change|replace|swap|make|fix|repair)\b/i, "disclose", "change"],
];

const SEVERITY: Record<Verdict, number> = { decline: 4, disclose: 3, overlay: 2, exempt: 1, unclear: 0 };
const VERB = /\b(remove|erase|delete|add|put|place|replace|change|make|fix|repair|brighten|darken|paint|stage|declutter|clear)\b/i;
const EDITING = /\b(remove|erase|delete|hide|cover|fix|repair|replace|change|swap|add|put|build|insert|make|clean)\b/i;

function clauses(text: string): string[] {
  const out: string[] = [];
  for (const sentence of text.split(/[.;!?\n]+/)) {
    let verb = "";
    for (const raw of sentence.split(/,\s*(?:and\s+|then\s+)?|\s+(?:and|then|also|but)\s+/i)) {
      const p = raw.trim();
      if (p.length < 2) continue;
      const m = p.match(VERB);
      if (m) {
        verb = m[1]!;
        out.push(p);
      } else if (verb && p.split(/\s+/).length <= 6 && !/^(?:in|on|at|behind|beside|near|by|of|for|with|from|to)\b/i.test(p)) {
        out.push(`${verb} ${p}`);
      } else out.push(p);
    }
  }
  return out;
}

export function classifyClause(c: string): Item {
  for (const [re, verdict, category] of RULES) {
    if (!re.test(c)) continue;
    if (verdict === "decline" && ["surroundings", "condition", "view"].includes(category) && !EDITING.test(c)) continue;
    return { text: c, verdict, category, reason: REASONS[category]! };
  }
  return { text: c, verdict: "unclear", category: "unclear", reason: REASONS.unclear! };
}

export function classify(text: string): { verdict: Verdict; items: Item[] } {
  const items = clauses(text).map(classifyClause);
  const verdict = items.reduce<Verdict>((w, i) => (SEVERITY[i.verdict] > SEVERITY[w] ? i.verdict : w), "unclear");
  return { verdict, items };
}
