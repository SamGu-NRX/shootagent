"""Which photos a film may use: approved ones, after QC.

A film shows buyers the photos as they are, so it must not use one QC has not looked at (the demo shoot's
game room still had the dataset's ghosted chairs). The film uses approved views only. A view QC split into
camera positions (`meta["split_into"]`) is replaced by its parts and never filmed itself.

`review_state` reads the shoot's run traces to tell which views QC has checked, so the launch flow can run
QC on the rest and approve the views QC left with no open or failed finding.
"""
from __future__ import annotations

from ..store import Shoot, ShootStore
from ..trace import read_events


class NeedsReview(ValueError):
    """A film was asked for before its photos were reviewed. The message says what to do."""


def qc_checked(store: ShootStore, shoot: Shoot) -> set[str]:
    """Views `run_qc_checks` ran on successfully in any of the shoot's runs."""
    out: set[str] = set()
    for rid in shoot.runs:
        events = read_events(store.shoot_dir(shoot.id) / "runs" / f"{rid}.jsonl")
        calls = {e["data"].get("call_id"): e["data"].get("input", {}).get("view") for e in events
                 if e["kind"] == "tool_call" and e["data"].get("tool") == "run_qc_checks"}
        out |= {calls.get(e["data"].get("call_id")) for e in events if e["kind"] == "tool_result"
                and e["data"].get("tool") == "run_qc_checks" and e["data"].get("ok")} - {None}
    return out


def review_state(store: ShootStore, shoot: Shoot) -> dict[str, str]:
    """Per view: "approved", "split" (replaced by its parts), "not_deliverable" (QC recommended against
    delivering it), "qc_ok", "open_findings" or "needs_qc"."""
    checked = qc_checked(store, shoot)
    state = {}
    for v in shoot.views:
        if not v.head:
            continue
        if v.meta.get("split_into"):
            state[v.id] = "split"
        elif v.approved:
            state[v.id] = "approved"
        elif (v.meta.get("delivery") or {}).get("deliver") is False:
            state[v.id] = "not_deliverable"
        elif v.id in checked or (v.derived_from and v.derived_from in checked):
            state[v.id] = "open_findings" if any(f.status in ("open", "failed") for f in v.findings) else "qc_ok"
        else:
            state[v.id] = "needs_qc"
    return state


def film_scope(store: ShootStore, shoot: Shoot, views: list[str] | None) -> list[str]:
    """The approved views to film (in shoot order), or NeedsReview saying what is missing."""
    state = review_state(store, shoot)
    if views:
        split = [v for v in views if state.get(v) == "split"]
        if split:
            parts = {v.id: v.meta["split_into"] for v in shoot.views if v.id in split}
            raise NeedsReview("QC split " + "; ".join(f"{v} into {', '.join(p)}" for v, p in parts.items())
                              + " (one view per camera position). Film those views instead.")
        unapproved = [v for v in views if state.get(v) != "approved"]
        if unapproved:
            raise NeedsReview(f"Approve {', '.join(unapproved)} before filming {'it' if len(unapproved) == 1 else 'them'}: "
                              + _what_to_do(state, unapproved))
        return list(views)
    scope = [v for v, s in state.items() if s == "approved"]
    if not scope:
        raise NeedsReview("No photo is approved for the film yet. " + _what_to_do(state, list(state)))
    return scope


def _what_to_do(state: dict[str, str], views: list[str]) -> str:
    need = [v for v in views if state.get(v) == "needs_qc"]
    open_ = [v for v in views if state.get(v) == "open_findings"]
    parts = []
    if need:
        parts.append(f"run QC on {', '.join(need)}")
    if open_:
        parts.append(f"resolve the open or failed QC findings on {', '.join(open_)}")
    parts.append("approve the photos to use, then make the film")
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."
