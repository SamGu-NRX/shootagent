"""Shoot-level tools: the plan, findings, per-view request results, and the listing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..store import Finding
from ._common import ToolError, load_view, norm_box
from .base import ToolContext, ToolInput, ToolResult, tool


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: str | None = Field(
        None, description="View id the step applies to, or omit for every view / the shoot."
    )
    intent: str = Field(description="What this step achieves, in plain words.")
    tool: str | None = Field(None, description="The tool you expect to use.")


class PlanIn(ToolInput):
    steps: list[PlanStep] = Field(min_length=1, description="Ordered steps.")
    text: str = Field("", description="One or two sentences summarising the approach for the customer.")


@tool(
    "submit_plan",
    "State your plan before doing anything else: which views the work touches and what you will do to each. "
    "Call it exactly once, first. Keep steps concrete (one per view, or one for 'every view' when the same "
    "edit applies everywhere). If you later change approach, just proceed; the trace records what you did.",
    kind="shoot",
)
def submit_plan(ctx: ToolContext, args: PlanIn) -> ToolResult:
    steps = [s.model_dump(exclude_none=True) for s in args.steps]
    ctx.trace.emit("plan", steps=steps, text=args.text)
    return ToolResult(summary=f"Plan recorded ({len(steps)} steps).", data={"steps": len(steps)})


class SetFindingIn(ToolInput):
    view: str = Field(description="View id.")
    finding: str = Field(
        description="Finding id from run_qc_checks, or 'new' to record a defect you spotted yourself."
    )
    status: Literal["open", "fixed", "dismissed", "failed"] = Field(
        description="'fixed' after a verified fix, 'dismissed' when the candidate is a legitimate edit or a false "
        "alarm, 'failed' when every fix attempt failed its check, 'open' to keep it for a human."
    )
    note: str = Field(description="Why, in one sentence. Shown to the photographer.")
    type: str | None = Field(
        None, description="For 'new': AutoHDR taxonomy type, e.g. 'camera_person_removal'."
    )
    region: list[float] | None = Field(None, description="For 'new': box [x0, y0, x1, y1] as fractions 0..1.")


@tool(
    "set_finding",
    "Record your verdict on a candidate defect: fixed, dismissed (legitimate edit such as sky replacement, "
    "declutter or TV-screen fill, or a false alarm), failed, or open. Every candidate from run_qc_checks needs a "
    "verdict before you finish. Use finding='new' for a defect you saw that the checks missed.",
    kind="shoot",
)
def set_finding(ctx: ToolContext, args: SetFindingIn) -> ToolResult:
    load_view(ctx, args.view)
    result: dict = {}

    def apply(shoot):
        view = shoot.view(args.view)
        if args.finding == "new":
            if not args.type:
                raise ToolError("A new finding needs a type.")
            f = Finding(
                type=args.type,
                explanation=args.note,
                region=list(norm_box(args.region)) if args.region else None,
                status=args.status,
                evidence={"source": "agent"},
            )
            view.findings.append(f)
        else:
            matches = [f for f in view.findings if f.id == args.finding]
            if not matches:
                raise ToolError(
                    f"No finding {args.finding!r} on {args.view}; ids: "
                    f"{', '.join(f.id for f in view.findings) or 'none'}."
                )
            f = matches[0]
            f.status = args.status
            f.evidence = {**f.evidence, "agent_note": args.note}
        result["finding"] = f.model_dump()

    ctx.store.mutate(ctx.shoot_id, apply)
    ctx.trace.emit("finding", view=args.view, finding=result["finding"])
    return ToolResult(
        summary=f"{result['finding']['id']} on {args.view}: {args.status} ({args.note}).",
        data={"finding": result["finding"]["id"], "status": args.status},
    )


class RequestResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request: str = Field(description="The part of the customer's request this covers, in a few words.")
    status: Literal["applied", "not_needed", "failed"] = Field(
        description="'applied' after a verified edit, 'not_needed' when it does not apply to this photo, "
        "'failed' when every attempt failed its check."
    )
    reason: str = Field(description="One sentence: what was done, or why it was not needed.")


class ReportViewIn(ToolInput):
    view: str = Field(description="View id.")
    results: list[RequestResult] = Field(min_length=1, description="One entry per part of the request.")


@tool(
    "report_view_result",
    "In chat mode, close out one view: for each part of the customer's request say whether it was applied, "
    "not needed (does not apply to this photo, e.g. 'remove the car' on an interior), or failed. Call it once "
    "for every view in scope, including untouched ones, after any edits on that view.",
    kind="shoot",
)
def report_view_result(ctx: ToolContext, args: ReportViewIn) -> ToolResult:
    load_view(ctx, args.view)
    results = [r.model_dump() for r in args.results]

    def apply(shoot):
        meta = shoot.view(args.view).meta
        meta["instruction_results"] = {"run": ctx.trace.id, "results": results}

    ctx.store.mutate(ctx.shoot_id, apply)
    ctx.trace.emit("view_result", view=args.view, results=results)
    counts = {s: sum(r["status"] == s for r in results) for s in ("applied", "not_needed", "failed")}
    return ToolResult(
        summary=f"{args.view}: " + ", ".join(f"{r['request']} {r['status']}" for r in results) + ".",
        data={"view": args.view, "results": results, "counts": counts},
    )


class ListingIn(ToolInput):
    address: str | None = None
    price: str | None = None
    beds: float | None = None
    baths: float | None = None
    sqft: int | None = None
    headline: str | None = Field(None, description="One-line marketing headline.")


@tool(
    "set_listing",
    "Set listing details (address, price, beds, baths, sqft, headline) when the customer gives them. Only the "
    "fields you pass change.",
    kind="shoot",
)
def set_listing(ctx: ToolContext, args: ListingIn) -> ToolResult:
    fields = args.model_dump(exclude_none=True)
    if not fields:
        raise ToolError("Pass at least one listing field.")

    def apply(shoot):
        for k, v in fields.items():
            setattr(shoot.listing, k, v)

    shoot = ctx.store.mutate(ctx.shoot_id, apply)
    return ToolResult(
        summary="Listing updated: " + ", ".join(f"{k}={v}" for k, v in fields.items()) + ".",
        data={"listing": shoot.listing.model_dump(exclude_none=True)},
    )
