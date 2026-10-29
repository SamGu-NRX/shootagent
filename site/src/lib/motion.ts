// Motion rules shared by every figure, after the BaseScanning landing page:
// - The markup is the finished state. A figure plays its entrance only if it starts below the screen, once, the
//   first time enough of it scrolls into view; one already in view (a reload part-way down, a link to its section)
//   stays finished instead of vanishing and replaying.
// - Reduced motion shows every finished state at once, and switching it on mid-way finishes what is playing.
// - Keys that operate a control switch the page to keyboard mode (html[data-input="keyboard"]), where controls
//   change at once instead of animating.

import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

const reducedQuery = () => window.matchMedia("(prefers-reduced-motion: reduce)");

// The page is prerendered, so every hook starts from the server's answer and reads the browser's before paint.
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useLayoutEffect(() => {
    const q = reducedQuery();
    const on = () => setReduced(q.matches);
    on();
    q.addEventListener("change", on);
    return () => q.removeEventListener("change", on);
  }, []);
  return reduced;
}

/** Marks the page as keyboard- or pointer-driven, for styles that should not animate under the keyboard. */
export function useInputMode(): void {
  useEffect(() => {
    const keys = ["Enter", " ", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"];
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const onControl = e.target instanceof Element && e.target.closest("a, button, input, select, textarea, summary");
      if (e.key !== "Tab" && !(onControl && keys.includes(e.key))) return;
      document.documentElement.dataset.input = "keyboard";
    };
    const onPointer = () => {
      document.documentElement.dataset.input = "pointer";
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, []);
}

/** Whether an element starts below the screen: only those hide to play their entrance. */
const belowFold = (el: Element) => el.getBoundingClientRect().top > window.innerHeight;

/**
 * "finished" for a figure that should not animate (already in view, reduced motion), "waiting" while it sits below
 * the screen in its starting state, then "playing" once, the first time it is in view. `rootMargin` and `threshold`
 * follow the figure: a tall stacked figure on a phone uses a margin, not a share of its height.
 */
export function useEntrance(
  ref: RefObject<Element | null>,
  { threshold = 0.35, rootMargin = "0px" }: { threshold?: number; rootMargin?: string } = {},
): "finished" | "waiting" | "playing" {
  const reduced = useReducedMotion();
  const [state, setState] = useState<"finished" | "waiting" | "playing">("finished");
  // Decided once, on the first run: "wait" for a figure below the screen, "done" otherwise or once it has played.
  // (React's development double run disconnects the first observer, so the second run has to set up its own.)
  const decided = useRef<"wait" | "done" | null>(null);
  useEffect(() => {
    const el = ref.current;
    // Read the setting itself too: on the first run `reduced` still holds the server's answer (false).
    if (!el || reduced || reducedQuery().matches) return;
    decided.current ??= belowFold(el) ? "wait" : "done";
    if (decided.current === "done") return;
    setState("waiting");
    const observer = new IntersectionObserver(
      ([entry]) => {
        // The first callback reports the current state even below the threshold, so check the ratio.
        if (!entry || !entry.isIntersecting || (threshold > 0 && entry.intersectionRatio < threshold)) return;
        observer.disconnect();
        decided.current = "done";
        setState("playing");
      },
      { threshold, rootMargin },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, reduced, threshold, rootMargin]);
  useEffect(() => {
    if (reduced) setState("finished");
  }, [reduced]);
  return state;
}

/** True while the element is on screen, for pausing autoplay that nobody can see. */
export function useOnScreen(ref: RefObject<Element | null>): boolean {
  const [on, setOn] = useState(true);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(([entry]) => setOn(!!entry?.isIntersecting), { threshold: 0.15 });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return on;
}

/** True while the tab is visible. */
export function usePageVisible(): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const on = () => setVisible(document.visibilityState !== "hidden");
    on();
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return visible;
}
