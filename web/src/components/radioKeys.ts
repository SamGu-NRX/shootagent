/**
 * Arrow-key handling for a role="radiogroup" of buttons (APG radio pattern): arrows move focus and
 * selection together, wrapping at the ends. Pair with tabIndex={checked ? 0 : -1} on each radio so the
 * group is a single tab stop.
 */
export function radioKeys(e: React.KeyboardEvent<HTMLElement>) {
  const keys = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"];
  if (!keys.includes(e.key)) return;
  const radios = [...e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=radio]:not(:disabled)")];
  const i = radios.indexOf(document.activeElement as HTMLButtonElement);
  if (i < 0) return;
  e.preventDefault();
  e.stopPropagation();
  const n = radios.length;
  const next =
    e.key === "Home" ? 0 : e.key === "End" ? n - 1 : (i + (e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1) + n) % n;
  radios[next]!.focus();
  radios[next]!.click();
}
