// The listing photo policy: a port of the product's classifier, src/shootagent/compliance/policy.py on branch dev
// (with its staging fix: "stage" and "furnish" never add a feature). The product's copy is the one that decides; this
// one answers the box on the page, and policy.test.ts holds it to the product's answers (policy.fixture.json).
//
// It follows policy.py line for line: the same clause splitting, word lists, patterns, order of rules and reasons.
// The patterns keep Python's syntax, and `py()` compiles them with Python's meaning (see "Python's re in JS").

export type Verdict = "decline" | "disclose" | "overlay" | "exempt" | "unclear";

export interface Item {
  text: string; // the part of the request it judged, e.g. "remove the power lines"
  verdict: Verdict;
  category: string; // surroundings, condition, view, feature, structure, proportions, removal, ...
  reason: string; // one plain sentence a customer can read
}

// Worst first: a request's verdict is the worst of its parts.
const SEVERITY: Record<Verdict, number> = { decline: 4, disclose: 3, overlay: 2, exempt: 1, unclear: 0 };

const TRUE_PICTURE = "Listing photos must show the property as it is (NAR Code of Ethics, Article 12; MLS photo rules).";

// ---- Python's re in JS ------------------------------------------------------------------------------------------

// Python's \s for text is str.isspace(); JS's \s lacks \x1c-\x1f and \x85 and adds \ufeff.
const SPACE = String.raw`\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000`;
// Python's \w for text is a letter or number in any script, or "_"; JS's \w is ASCII-only, even with the u flag.
const WORD = String.raw`\p{L}\p{N}_`;
// Python's \b is the edge between a \w and a non-\w, so lookarounds rebuild it. A browser without lookbehind (Safari
// before 16.4) keeps JS's ASCII \b rather than failing to load the page.
const BOUNDARY = supportsLookbehind()
  ? `(?:(?<=[${WORD}])(?![${WORD}])|(?<![${WORD}])(?=[${WORD}]))`
  : String.raw`\b`;

function supportsLookbehind(): boolean {
  try {
    return new RegExp("(?<!a)b").test("b");
  } catch {
    return false;
  }
}

/** A Python raw string written over several lines: line breaks and the indentation after them are dropped. */
function r(strings: TemplateStringsArray, ...values: string[]): string {
  return String.raw(strings, ...values).replace(/\n */g, "");
}

/** Compile a pattern written for Python's `re` (the syntax policy.py uses) so that it matches what Python matches:
 * \w, \b and \s as above; `.` as anything but \n (JS's also stops at \r, \u2028 and \u2029); and under re.I, "i"
 * also matches the Turkish "ı" and "İ", as Python's does (JS's "s" and "k" already match the long "ſ" and the
 * Kelvin sign). */
function py(pattern: string, flags = "i"): RegExp {
  const ignoreCase = flags.includes("i");
  let out = "";
  let inClass = false;
  for (let k = 0; k < pattern.length; k++) {
    const ch = pattern[k]!;
    if (ch === "\\") {
      const esc = pattern[++k]!;
      if (esc === "w") out += inClass ? WORD : `[${WORD}]`;
      else if (esc === "s") out += inClass ? SPACE : `[${SPACE}]`;
      else if (esc === "b" && !inClass) out += BOUNDARY;
      else out += ch + esc;
    } else if (inClass) {
      inClass = ch !== "]";
      out += ch;
    } else if (ch === "[") {
      inClass = true;
      out += ch;
    } else if (ch === ".") out += "[^\\n]";
    else if (ch === "i" && ignoreCase) out += "[i\u0131\u0130]";
    else out += ch;
  }
  return new RegExp(out, flags + "u");
}

const IS_SPACE = py(r`\s`, "");
const SPACES = py(r`\s+`, "");

/** Python's str.strip(chars), or str.strip() when no chars are given. */
function strip(s: string, chars?: string): string {
  const drop = (ch: string) => (chars === undefined ? IS_SPACE.test(ch) : chars.includes(ch));
  let a = 0;
  let b = s.length;
  while (a < b && drop(s[a]!)) a++;
  while (b > a && drop(s[b - 1]!)) b--;
  return s.slice(a, b);
}

/** Python's len(s.split()): the number of words between runs of whitespace. */
function wordCount(s: string): number {
  return s.split(SPACES).filter((w) => w.length > 0).length;
}

// ---- lexicons ---------------------------------------------------------------------------------------------------

const REMOVE = r`remove|removing|removed|erase|erasing|delete|deleting|take\s+out|take\s+away|get\s+rid\s+of|eliminate|
  clear(?:\s+away|\s+out)?|declutter|de-clutter|edit\s+out|photoshop\s+out|clone\s+out|hide|hiding|cover(?:\s+up)?|
  conceal|mask\s+out|crop\s+out`;
const ADD = r`add|adding|insert|place|put|include|stage|furnish|install|render|create|build|show`;
const REPAIR = r`fix|fixing|repair|repairing|patch|patching|retouch|touch\s+up|smooth(?:\s+out)?|clean\s+up|cleanup|clean|
  mend|fill\s+in`;
const CHANGE = r`change|changing|replace|replacing|swap|turn|make|paint|repaint|recolou?r|colou?r|use|match|convert|update|
  refresh|improve|enhance|green|redo|transform`;
const ADJUST_VERB = r`brighten|darken|lighten|warm|cool|straighten|crop|sharpen|denoise|level|correct|balance|expose`;
const VERB = py(r`\b(?:${REMOVE}|${ADD}|${REPAIR}|${CHANGE}|${ADJUST_VERB})\b`);

const SURROUNDINGS = py(r`\b(?:power|utility|electric(?:al)?\s+utility|telephone|phone|overhead|transmission|
  high[- ]voltage)\s+(?:lines?|wires?|cables?)\b|
  \b(?:utility|power|telephone|phone|telegraph|electric(?:al)?|light)\s+poles?\b|
  \b(?:cell(?:ular|phone)?|radio|transmission|water|communications?)\s+towers?\b|
  \b(?:substations?|high[- ]?ways?|freeways?|interstates?|overpass(?:es)?|railroads?|railways?|
  (?:train|rail(?:road|way)?)\s+tracks?|billboards?|landfills?|wind\s+turbines?|power\s+plants?)\b|
  \bneighbou?r(?:'s|s'|s)?\s+(?:house|home|building|garage|roof|wall|property|structure|shed)s?\b|
  \b(?:house|home|building|apartment|condo)s?\s+next\s+door\b|
  \badjacent\s+(?:house|home|building|structure)s?\b`);
const CONDITION = py(r`\bcrack(?:s|ed|ing)?\b|\bwater\s+(?:stains?|damage|marks?|spots?)\b|\bmou?ld\b|\bmildew\b|
  \b(?:dry\s+)?rot(?:ten|ting)?\b|\bleak(?:s|ing|y)?\b|\bholes?\s+in\b|\bdents?\b|
  \b(?:water|storm|fire|roof|wall|ceiling|floor|siding|hail|flood|smoke|termite|structural)\s+damage\b|
  \bdamage\s+(?:to|on|in)\b|
  \bpeeling\b|\bchipped\b|\bbroken\s+(?:windows?|glass|tiles?|fences?|gutters?|steps?|doors?|boards?|siding)\b|
  \bmissing\s+(?:shingles?|tiles?|siding|boards?)\b|\brust\s+(?:stains?|marks?|on\s+the)\b|
  \brusted\s+(?:gutters?|railings?|fences?|roof|pipes?)\b|\btermites?\b|\bsagging\b|
  \b(?:ceiling|wall|roof)\s+stains?\b|\bstains?\s+on\s+the\s+(?:ceiling|walls?|roof)\b|
  \bfoundation\s+(?:issues?|problems?|damage)\b|\bpotholes?\b|\bstructural\b`);
const VIEW = py(r`\b(?:an?\s+|the\s+)?(?:ocean|sea|lake|river|bay|beach|mountain|city|skyline|golf(?:\s+course)?|canyon|
  valley|harbou?r)\s+views?\b|
  \b(?:replace|replacing|swap|swapping|change|changing|fake|invent)\s+(?:the\s+|a\s+)?(?:window\s+)?views?\b`);
// Things on or near a structural word that are not the structure: "add a deck chair", "remove the window blinds".
const NOT_STRUCT = r`(?!\s+(?:mats?|stops?|hangers?|wreaths?|signs?|decor|decals?|stickers?|clings?|art|clocks?|
  hangings?|posters?|pictures?|lights?|sconces?|shelf|shelves|mounts?|tvs?|outlets?|plates?|vents?|treatments?|
  coverings?|curtains?|blinds?|screens?|reflections?|glare|clutter|items?|contents?|magnets?|handles?|knobs?|hardware|
  ac\b|a/c|air\s+condition\w*|units?|fans?|heaters?|tools?|toys?|accessories|deck|decks|side|sides|area|areas|edge|
  edges|furniture|chairs?|loungers?|towels?|floats?|cover|covers))`;
const ARTICLES = r`(?:(?:a|an|the|some|new|nice|big|small|large|beautiful|modern|another|second|extra|real|working|
  built-in|in-ground|above-ground)\s+){0,3}`;
// ADD without "stage" and "furnish": staging puts furniture in a space the property has ("stage the bedroom",
// "furnish the patio"), it never adds the space, so it is disclosed as staging below rather than declined here.
const ADD_FEATURE = r`add|adding|insert|place|put|include|install|render|create|build|show`;
const FEATURE_ADD = py(r`\b(?:${ADD_FEATURE})\s+${ARTICLES}(?:swimming\s+pools?|pools?|hot\s+tubs?|jacuzzis?|spas?|decks?|
  patios?|balcon(?:y|ies)|porch(?:es)?|fireplaces?|chimneys?|windows?|skylights?|doors?|garages?|rooms?|bedrooms?|
  bathrooms?|extensions?|(?:second|2nd|third)\s+(?:story|storey|floor)|fences?|gazebos?|pergolas?|sheds?|
  (?:tennis|basketball|pickleball)\s+courts?|driveways?|solar\s+panels?|staircases?|kitchen\s+islands?)\b${NOT_STRUCT}`);
// "declutter the garage" and "clear the counter" name the container, not the thing removed.
const REMOVE_THING = r`remove|removing|removed|erase|erasing|delete|deleting|take\s+out|take\s+away|get\s+rid\s+of|
  eliminate|edit\s+out|photoshop\s+out|clone\s+out|hide|hiding|cover\s+up|conceal|mask\s+out|crop\s+out`;
const STRUCTURE_REMOVE = py(r`\b(?:${REMOVE_THING})\s+(?:(?:the|a|an|that|this|these|those|all|some|both|old|ugly|left|
  right|front|back|interior|small|big|large|half|partial|support)\s+){0,3}(?:walls?|columns?|pillars?|beams?|stairs?|
  staircases?|windows?|doors?|doorways?|fireplaces?|chimneys?|built-?ins?|cabinets?|cabinetry|(?:kitchen\s+)?islands?|
  countertops?|radiators?|garages?|decks?|porch(?:es)?|balcon(?:y|ies)|roofs?|railings?)\b${NOT_STRUCT}`);
const PROPORTIONS = py(r`\b(?:make|making)\s+(?:(?:the|this|it|room|space|yard|lot|kitchen|bedroom|bathroom|house|home|
  garden|backyard|living\s+room)\s+){0,3}(?:look\s+|seem\s+|appear\s+|feel\s+)?(?:much\s+|a\s+lot\s+|way\s+)?
  (?:bigger|larger|more\s+spacious|wider|taller|longer|deeper|roomier)\b|\bstretch\b|\bwiden\s+the\b`);

const GEAR = py(r`\b(?:tripods?|camera(?:\s+gear)?|photographers?|light\s+stands?|flash(?:es)?|
  (?:my|the\s+photographer'?s?)\s+reflections?|reflection\s+of\s+(?:me|the\s+photographer|the\s+camera))\b`);
const OVERLAY = py(r`\b(?:watermarks?|logos?|captions?|text\s+overlay|overlay|lot\s+lines?|property\s+lines?|
  boundar(?:y|ies)|outline\s+(?:the\s+)?(?:lot|property)|arrows?|sign\s+riders?)\b`);
const SKY = py(r`\b(?:sky|skies|clouds?|cloudy|overcast)\b`);
const TWILIGHT = py(r`\b(?:twilight|dusk|sunset|night\s+(?:shot|photo|look|scene)|evening\s+look)\b`);
const LAWN = py(r`\b(?:lawn|grass|turf|yard)\b.*\b(?:green(?:er)?|fill|fix|patchy|brown|dead|dry|lush)\b|
  \b(?:green(?:er)?|lush)\b.*\b(?:lawn|grass|turf)\b`);
const FIRE = py(r`\b(?:fire|fires|flames?|fire\s+in\s+the\s+fireplace|light\s+the\s+fireplace)\b`);
const SCREEN = py(r`\b(?:tv|tvs|television|screen|monitor)\b`);
const STAGING = py(r`\b(?:virtual(?:ly)?\s+stag\w*|stag(?:e|ing)\b|furnish\w*|furniture)\b`);
const COLOUR_WORDS = r`white|off-white|cream|ivory|beige|tan|grey|gray|greige|taupe|black|charcoal|navy|blue|green|sage|
  olive|yellow|gold|orange|red|pink|purple|brown|walnut|oak|espresso|natural`;
const COLOUR = py(r`#[0-9a-f]{3}(?:[0-9a-f]{3})?\b|\bpaint(?:ed|ing)?\b|\brepaint\w*\b|\brecolou?r\w*\b|
  \bcolou?r\b|\bstain(?:ed)?\s+(?:the\s+)?(?:floors?|cabinets?|deck|wood)\b|
  \b(?:${COLOUR_WORDS})\s+(?:walls?|floors?|flooring|cabinets?|doors?|trim|ceiling|siding|shutters?|countertops?)\b|
  \b(?:walls?|floors?|cabinets?|doors?|trim|ceiling|siding|shutters?)\s+(?:to\s+)?(?:${COLOUR_WORDS})\b`);
const ADJUST = py(r`\b(?:bright(?:en|er|ness)?|lighten|lighter|darken|darker|exposure|expose|white\s+balance|
  warm(?:er|th)?|cool(?:er)?|colou?r\s+correct\w*|vibran(?:t|ce)|saturat\w*|contrast|straighten\w*|level|crop|sharpen\w*|
  sharper|denois\w*|noise|grain|hdr|tone|tones|highlights?|shadows?|clarity|dull|dim|cast|tint|vertical(?:s)?|
  perspective|exposure)\b`);
const REMOVE_VERB = py(r`\b(?:${REMOVE})\b`);
const ADD_VERB = py(r`\b(?:${ADD})\b`);
const REPAIR_VERB = py(r`\b(?:${REPAIR})\b`);
const CHANGE_VERB = py(r`\b(?:${CHANGE})\b`);

const SENTENCES = py(r`[.;!?\n:]+|\(|\)`, "");
const SPLIT = py(r`,\s*(?:and|or|then|but|also|plus)?\s*|\s+(?:and|&|then|also|plus|but|while|as\s+well\s+as)\s+`);
// A part that opens with a preposition is context for the last object ("in front of the garage"), not a new one.
// (Python calls .match, which anchors at the start; hence the ^.)
const PREPOSITION = py(r`^(?:in|on|at|behind|beside|next|near|along|under|over|by|of|for|with|from|to|between|inside|
  outside|around|across|above|below|against|beyond|past|toward|towards|where|which|that|so)\b`);
const KEEP = py(r`\b(?:keep|keeping|preserve|preserving|retain|retaining|leave|leaving|maintain|maintaining|protect|
  do\s+not|don'?t|must\s+stay|should\s+stay|stays?|intact|unchanged|untouched|as[- ]is|in\s+place|
  except|unless|only\s+if)\b`);
// The patterns classify_clause writes inline (the first is a re.match, so it is anchored too).
const OPENS_WITH_REMOVE_OR_ADD = py(r`^\s*(?:${REMOVE}|${ADD})\b`);
const BLUE = py(r`\bblue\b`);
const LIGHT_THE_FIREPLACE = py(r`\blight\s+the\s+fireplace\b`);

export const REASONS: Record<string, string> = {
  surroundings:
    "Power lines, poles, towers, roads and neighbouring buildings are part of the property's surroundings; removing " +
    `them misrepresents the property. ${TRUE_PICTURE}`,
  condition: `Hiding damage or wear (cracks, stains, leaks, mould, rot) misrepresents the property's condition. ${TRUE_PICTURE}`,
  view: `Changing or inventing the view misrepresents the property's location. ${TRUE_PICTURE}`,
  feature: `Adding a feature the property does not have (a pool, a room, a fireplace) misrepresents it. ${TRUE_PICTURE}`,
  structure: `Removing walls, windows, doors or built-ins changes the property's structure in the photo. ${TRUE_PICTURE}`,
  proportions: `Stretching or enlarging a space misrepresents its size. ${TRUE_PICTURE}`,
  gear: "The photographer's equipment is not part of the property; removing it is not an alteration.",
  overlay:
    "Text and graphics laid over the photo (watermarks, lot lines) are not alterations of the property; lot lines " +
    "should be marked approximate.",
  sky: "A replaced or enhanced sky changes the scene; the photo is marked digitally altered (AB 723).",
  twilight: "A day-to-twilight conversion changes the scene; the photo is marked digitally altered (AB 723).",
  lawn: "Greening or filling a lawn changes how the property looks; the photo is marked digitally altered (AB 723).",
  fire: "A fire added to a real fireplace is a common enhancement; the photo is marked digitally altered (AB 723).",
  screen: "Replacing what a TV or monitor shows is a minor change; the photo is still marked digitally altered (AB 723).",
  staging: "Virtual staging must be disclosed: the photo is marked virtually staged (AB 723).",
  colour:
    "Changing paint, flooring or cabinet colour changes physical features; the photo is marked digitally altered " +
    "(AB 723).",
  removal:
    "Removing objects from the scene is a digital alteration under AB 723; the photo is marked as altered and paired " +
    "with the unaltered original.",
  addition:
    "Adding objects to the scene is a digital alteration under AB 723; the photo is marked as altered and paired with " +
    "the unaltered original.",
  change: "Changing what the scene shows is a digital alteration under AB 723; the photo is marked as altered.",
  adjustment:
    "Exposure, colour correction, straightening, cropping and sharpening are not alterations under AB 723; no " +
    "disclosure is needed.",
  keep: "Asks to keep part of the scene as it is; nothing to disclose.",
  unclear: "Not recognised as a specific edit; the agent decides from the photo and discloses any alteration.",
};

// "between the kitchen and the dining room" is one place, so its "and" is hidden from the splitter as "\u2227".
const BETWEEN = py(r`\b(between\s+(?:the\s+)?[\w'-]+(?:\s+[\w'-]+)?)\s+and\s+`, "gi");

/** Split a request into parts. A verb carries into the short list items right after it ("remove the bins and the
 * cars" -> "remove the bins", "remove the cars"), never across sentences, never into a long phrase (context such as
 * "in front of the neighbour's garage"), and a keep clause ("keep the windows, doors and trim intact") stops it.
 * "between the kitchen and the dining room" stays whole. */
function clauses(text: string): string[] {
  const hidden = (text || "").replace(BETWEEN, (_m, between: string) => `${between} \u2227 `);
  const out: string[] = [];
  for (const sentence of hidden.split(SENTENCES)) {
    let verb = "";
    for (const raw of sentence.split(SPLIT)) {
      const p = strip(raw, " -:\t\"'").replaceAll("\u2227", "and");
      if ([...p].length < 2) continue; // Python's len() counts code points, not UTF-16 units
      if (KEEP.test(p)) {
        verb = "keep";
        out.push(p);
        continue;
      }
      const m = VERB.exec(p);
      if (m) {
        verb = m[0];
        out.push(p);
      } else if (verb && wordCount(p) <= 6 && !PREPOSITION.test(p)) {
        out.push(`${verb} ${p}`);
      } else {
        verb = "";
        out.push(p);
      }
    }
  }
  return out;
}

export function classifyClause(clause: string): Item {
  const c = strip(clause);
  const lower = c.toLowerCase();
  const removing = REMOVE_VERB.test(c);
  const repairing = REPAIR_VERB.test(c);
  const changing = CHANGE_VERB.test(c);
  const adding = ADD_VERB.test(c);
  const editing = removing || repairing || changing || adding;
  const item = (verdict: Verdict, category: string): Item => ({ text: c, verdict, category, reason: REASONS[category]! });

  if (KEEP.test(c) && !OPENS_WITH_REMOVE_OR_ADD.test(c)) return item("exempt", "keep");

  // Refusals first: a request that would misrepresent the property is declined whatever else it asks.
  if (SURROUNDINGS.test(c) && (removing || repairing || changing)) return item("decline", "surroundings");
  if (CONDITION.test(c) && (removing || repairing || changing) && !GEAR.test(c)) return item("decline", "condition");
  if (VIEW.test(c) && editing) return item("decline", "view");
  if (FEATURE_ADD.test(c)) return item("decline", "feature");
  if (STRUCTURE_REMOVE.test(c)) return item("decline", "structure");
  if (PROPORTIONS.test(c)) return item("decline", "proportions");

  if (GEAR.test(c) && removing) return item("exempt", "gear");
  if (OVERLAY.test(c)) return item("overlay", "overlay");
  if (STAGING.test(c) && (adding || changing || lower.includes("stag"))) return item("disclose", "staging");
  if (TWILIGHT.test(c)) return item("disclose", "twilight");
  if (SKY.test(c) && (changing || adding || removing || BLUE.test(c))) return item("disclose", "sky");
  if (LAWN.test(c)) return item("disclose", "lawn");
  if (FIRE.test(lower.replaceAll("fireplace", "")) || LIGHT_THE_FIREPLACE.test(c)) return item("disclose", "fire");
  if (SCREEN.test(c) && (adding || changing || lower.includes("image") || lower.includes("picture"))) {
    return item("disclose", "screen");
  }
  if (COLOUR.test(c) && !ADJUST.test(c.replaceAll("colour correct", "").replaceAll("color correct", ""))) {
    return item("disclose", "colour");
  }
  if (COLOUR.test(c) && (changing || adding)) return item("disclose", "colour");
  if (removing) return item("disclose", "removal");
  if (ADJUST.test(c)) return item("exempt", "adjustment");
  if (adding) return item("disclose", "addition");
  if (changing || repairing) return item("disclose", "change");
  return item("unclear", "unclear");
}

/** Judge a customer's request part by part. The verdict is the worst part: decline > disclose > overlay > exempt >
 * unclear. */
export function classify(text: string): { verdict: Verdict; items: Item[] } {
  const items = clauses(text).map(classifyClause);
  const verdict = items.reduce<Verdict>((w, i) => (SEVERITY[i.verdict] > SEVERITY[w] ? i.verdict : w), "unclear");
  return { verdict, items };
}
