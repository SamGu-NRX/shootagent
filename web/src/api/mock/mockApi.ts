// In-browser stand-in for the FastAPI server: same Api interface, scripted runs, timed trace events.

import { ApiError, type Api, type MaskPoint } from "../client";
import type { Comment, CommentView, KitInfo, Mask, ReviewShoot, RunSummary, Shoot, ShootSummary, TraceEvent } from "../types";
import { makeShoot } from "./data";
import { classifyRequest, complianceReport, lintListing, publishMock, type RunInfo } from "./delivery";
import { chatScript, copyScript, qcScript, reelScript, type Step } from "./scripts";

interface MockRun {
  id: string;
  shootId: string;
  mode: string;
  request: string | null;
  origin: string | null;
  started: string;
  events: TraceEvent[];
  done: boolean;
  usd: number;
  summary: string | null;
  subs: Set<(e: TraceEvent) => void>;
  ends: Set<() => void>;
  /** runs queued behind this one start when it ends */
  after: (() => void)[];
}

const clone = <T>(x: T): T => structuredClone(x);

function runId(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${Math.random().toString(16).slice(2, 8)}`;
}

/** Draws a soft blob around the clicked points, standing in for SAM. Returns a PNG data URL. */
function fakeMask(points: MaskPoint[]): { url: string; bbox: number[]; area: number } {
  const w = 400;
  const h = 266;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const g = canvas.getContext("2d")!;
  g.fillStyle = "#000";
  g.fillRect(0, 0, w, h);
  const inc = points.filter((p) => p[2] === 1);
  g.fillStyle = "#fff";
  g.filter = "blur(4px)";
  for (const [x, y] of inc) {
    g.beginPath();
    g.ellipse(x * w, y * h, w * 0.06, h * 0.09, 0, 0, Math.PI * 2);
    g.fill();
  }
  g.fillStyle = "#000";
  for (const [x, y] of points.filter((p) => p[2] === 0)) {
    g.beginPath();
    g.arc(x * w, y * h, w * 0.04, 0, Math.PI * 2);
    g.fill();
  }
  const xs = inc.map((p) => p[0]);
  const ys = inc.map((p) => p[1]);
  const bbox = [Math.min(...xs) - 0.06, Math.min(...ys) - 0.09, Math.max(...xs) + 0.06, Math.max(...ys) + 0.09].map((v) =>
    Math.min(1, Math.max(0, v)),
  );
  return { url: canvas.toDataURL("image/png"), bbox, area: Math.min(1, inc.length * 0.017) };
}

export function createMockApi(speed = 1): Api {
  const shoots = new Map<string, Shoot>();
  const runs = new Map<string, MockRun>();
  let counter = 0;

  const fresh = makeShoot("demo-maple", "412 Maple Court");
  shoots.set(fresh.id, fresh);

  const get = (id: string) => {
    const s = shoots.get(id);
    if (!s) throw new ApiError(404, "not_found", `No shoot "${id}". Go back to the shoot list and pick one.`);
    return s;
  };

  function start(shoot: Shoot, mode: string, steps: Step[], instant = false, behind?: MockRun, info: RunInfo = {}): MockRun {
    const run: MockRun = {
      id: runId() + (instant ? `-${++counter}` : ""),
      shootId: shoot.id,
      mode,
      request: info.request ?? null,
      origin: info.origin ?? null,
      started: new Date().toISOString(),
      events: [],
      done: false,
      usd: 0,
      summary: null,
      subs: new Set(),
      ends: new Set(),
      after: [],
    };
    runs.set(run.id, run);
    shoot.runs.push(run.id);
    const ctx = { shoot, runId: run.id };
    let clock = Date.now() - (instant ? steps.reduce((a, s) => a + s.gap, 0) : 0);
    const t0 = clock;

    const emit = (step: Step, seq: number) => {
      const data = step.data(ctx) as Record<string, unknown>;
      if (step.kind === "usage") run.usd += Number(data.usd ?? 0);
      if (step.kind === "run_end") {
        data.ms = clock - t0;
        data.usd = Math.round(run.usd * 10000) / 10000;
        run.done = true;
        run.summary = String(data.summary ?? "");
      }
      const ev = { run: run.id, seq, t: new Date(clock).toISOString(), kind: step.kind, view: step.view ?? null, data } as TraceEvent;
      run.events.push(ev);
      run.subs.forEach((f) => f(ev));
      if (run.done) {
        run.ends.forEach((f) => f());
        run.after.splice(0).forEach((f) => f());
      }
    };

    if (instant) {
      steps.forEach((step, i) => {
        clock += step.gap;
        emit(step, i + 1);
      });
    } else {
      let i = 0;
      if (behind) {
        behind.after.push(() => {
          clock = Date.now();
          next();
        });
        return run;
      }
      function next() {
        const step = steps[i];
        if (!step) return;
        setTimeout(() => {
          clock = Date.now();
          emit(step, ++i);
          next();
        }, step.gap / speed);
      }
      next();
    }
    return run;
  }

  function queueOrStart(shoot: Shoot, mode: string, build: () => Step[], info: RunInfo = {}): { run_id: string } {
    // docs/api.md: a request while a run is active is queued, not run in parallel.
    const busy = [...runs.values()].filter((r) => r.shootId === shoot.id && !r.done).pop();
    return { run_id: start(shoot, mode, build(), false, busy, info).id };
  }

  // ---- delivery -------------------------------------------------------------------------------------------
  const runInfo = (id: string | null): RunInfo => {
    const r = id ? runs.get(id) : undefined;
    return r ? { mode: r.mode, request: r.request, origin: r.origin } : {};
  };
  const qcDone = (shootId: string) => [...runs.values()].some((r) => r.shootId === shootId && r.mode === "qc" && r.done);
  const report = (shoot: Shoot) => complianceReport(shoot, runInfo, qcDone(shoot.id));
  const kits = new Map<string, KitInfo>();
  /** Demo links name their shoot, so a link opened in a new tab finds (a fresh copy of) it. */
  const byToken = (token: string, kind: "public" | "client"): Shoot => {
    const m = kind === "public" ? token.match(/^demo-(.+)$/) : token.match(/^demo-r-(.+)-\d+$/);
    const id = m?.[1];
    if (!id || (kind === "public" && id.startsWith("r-"))) throw new ApiError(404, "link_not_found", "This link is not valid. Ask the photographer for a new one.");
    if (!shoots.has(id)) shoots.set(id, makeShoot(id, "412 Maple Court"));
    const shoot = shoots.get(id)!;
    if (kind === "client") {
      const share = (shoot.shares ??= []).find((x) => x.token === token);
      if (share?.revoked) throw new ApiError(404, "link_not_found", "This review link was turned off. Ask the photographer.");
      if (!share) shoot.shares.push({ token, role: "client", label: "", created_at: new Date().toISOString(), revoked: false, max_requests: 20 });
    }
    return shoot;
  };
  const commentView = (shoot: Shoot, c: Comment): CommentView => {
    let state: CommentView["state"] = c.status;
    let reason = c.note;
    let message = "";
    let steps = 0;
    let result_version: string | null = null;
    if (c.status !== "declined" && c.status !== "undone" && c.run) {
      const r = runs.get(c.run);
      if (!r) state = "stopped";
      else {
        steps = r.events.filter((e) => e.kind === "tool_call").length;
        const msgs = r.events.filter((e) => e.kind === "message").map((e) => (e.data as { text: string }).text);
        message = msgs[msgs.length - 1] ?? "";
        if (!r.done) state = r.events.length ? "working" : "queued";
        else {
          const results = r.events
            .filter((e) => e.kind === "view_result" && e.view === c.view)
            .flatMap((e) => (e.data as { results: { status: string; reason: string }[] }).results);
          const passed = r.events.filter((e) => e.kind === "check" && e.view === c.view && (e.data as { passed: boolean }).passed);
          if (passed.length) {
            state = "applied";
            result_version = (passed[passed.length - 1]!.data as { version: string }).version;
          } else if (results.length && results.every((x) => x.status === "declined")) state = "declined";
          else if (results.some((x) => x.status === "failed")) state = "failed";
          else state = "not_needed";
          reason = results.map((x) => x.reason).join(" ") || message || r.summary || "";
        }
      }
    }
    const view = shoot.views.find((v) => v.id === c.view);
    return {
      ...c,
      state,
      reason,
      message,
      steps,
      result_version,
      result_blob: result_version ? (view?.versions.find((v) => v.id === result_version)?.blob ?? null) : null,
      current: view?.head ?? null,
    };
  };
  const reviewOf = (shoot: Shoot, token: string): ReviewShoot => {
    const rep = report(shoot);
    const byView = new Map(rep.photos.map((p) => [p.view, p]));
    const share = shoot.shares?.find((x) => x.token === token);
    const used = (shoot.comments ?? []).filter((c) => c.share === token && c.run).length;
    return {
      token,
      label: share?.label ?? "",
      title: shoot.listing.address ?? shoot.title,
      listing: shoot.listing,
      requests_left: Math.max(0, (share?.max_requests ?? 20) - used),
      agent_available: true,
      public_token: shoot.publication ? (shoot.public_token ?? null) : null,
      views: shoot.views
        .filter((v) => v.head && byView.get(v.id)?.deliver)
        .map((v) => ({
          id: v.id,
          label: v.label || v.id,
          room: v.room,
          blob: v.versions.find((x) => x.id === v.head)!.blob,
          original_blob: v.versions[0]!.blob,
          edited: v.head !== v.versions[0]!.id,
          head: v.head!,
          approved: v.approved,
          client: v.client ?? null,
          status: byView.get(v.id)!.status,
          disclosure: byView.get(v.id)!.disclosure,
        })),
      comments: (shoot.comments ?? []).filter((c) => c.share === token).map((c) => commentView(shoot, c)),
    };
  };

  const delay = <T>(v: T, ms = 120) => new Promise<T>((r) => setTimeout(() => r(v), ms));

  return {
    mode: "mock",
    async listShoots(): Promise<ShootSummary[]> {
      return delay(
        [...shoots.values()]
          .sort((a, b) => b.created_at.localeCompare(a.created_at))
          .map((s) => ({ id: s.id, title: s.title, created_at: s.created_at, view_count: s.views.length, thumb: s.views[0]?.finished ?? null })),
      );
    },
    async createDemo(preset) {
      counter += 1;
      const title = preset === "chat" ? "88 Larkspur Lane" : preset === "showcase" ? "Demo shoot: QC and customer requests" : `412 Maple Court (${counter})`;
      const shoot = makeShoot(`demo-${preset}-${counter}`, title);
      if (preset === "chat") start(shoot, "qc", qcScript(shoot), true);
      shoots.set(shoot.id, shoot);
      return delay(clone(shoot), 400);
    },
    async uploadShoot() {
      throw new ApiError(501, "mock_upload", "Upload needs the live server. Start `shootagent serve` and reload with ?api=live.");
    },
    async getShoot(id) {
      return delay(clone(get(id)), 60);
    },
    async setHead(id, viewId, version) {
      const v = get(id).views.find((x) => x.id === viewId);
      if (!v?.versions.some((x) => x.id === version)) throw new ApiError(404, "not_found", `No version ${version}.`);
      v.head = version;
      return delay(clone(v), 60);
    },
    async approve(id, viewId, approved) {
      const v = get(id).views.find((x) => x.id === viewId)!;
      v.approved = approved;
      return delay(clone(v), 60);
    },
    async createMask(id, viewId, points) {
      if (!points.some((p) => p[2] === 1)) throw new ApiError(400, "no_points", "Shift-click at least one point inside the object.");
      const { url, bbox, area } = fakeMask(points);
      const mask: Mask = {
        id: "m_" + Math.random().toString(16).slice(2, 10),
        view: viewId,
        blob: url,
        source: "user",
        query: null,
        bbox,
        area,
        created_at: new Date().toISOString(),
      };
      get(id).masks[mask.id] = mask;
      return delay(clone(mask), 500);
    },
    async runQc(id) {
      const shoot = get(id);
      return queueOrStart(shoot, "qc", () => qcScript(shoot));
    },
    async chat(id, body) {
      const shoot = get(id);
      return queueOrStart(shoot, "chat", () => chatScript(shoot, body.message, body.views, body.mask), { mode: "chat", request: body.message });
    },
    async reel(id) {
      const shoot = get(id);
      if (!shoot.views.some((v) => v.approved)) {
        throw new ApiError(400, "nothing_approved", "Approve at least one photo before building a reel.");
      }
      return queueOrStart(shoot, "reel", () => reelScript(shoot));
    },
    async draftCopy(id) {
      const shoot = get(id);
      return queueOrStart(shoot, "copy", () => copyScript(shoot), { mode: "copy" });
    },
    async listRuns(id): Promise<RunSummary[]> {
      return delay(
        [...runs.values()]
          .filter((r) => r.shootId === id)
          .map((r) => ({ id: r.id, mode: r.mode, started: r.started, done: r.done, usd: r.usd, summary: r.summary })),
        40,
      );
    },
    async cancelRun() {
      throw new ApiError(501, "mock_cancel", "Demo runs can't be cancelled; they finish in under a minute.");
    },
    subscribeRun(_id, run, onEvent, onEnd) {
      const r = runs.get(run);
      if (!r) {
        queueMicrotask(() => onEnd(new ApiError(404, "not_found", `No run ${run}.`)));
        return { close() {} };
      }
      let open = true;
      const sub = (e: TraceEvent) => open && onEvent(e);
      const end = () => open && onEnd();
      queueMicrotask(() => {
        if (!open) return;
        r.events.forEach(sub);
        if (r.done) end();
        else {
          r.subs.add(sub);
          r.ends.add(end);
        }
      });
      return {
        close() {
          open = false;
          r.subs.delete(sub);
          r.ends.delete(end);
        },
      };
    },
    blobUrl(_shootId, blob, w) {
      if (blob.startsWith("data:")) return blob;
      if (w && w <= 400 && blob.endsWith(".jpg") && !blob.includes("_w360")) return `/mock/${blob.replace(".jpg", "_w360.jpg")}`;
      return `/mock/${blob}`;
    },
    reelUrl: () => "/mock/reel-not-rendered.mp4",

    async getCompliance(id) {
      return delay(report(get(id)), 120);
    },
    async setFindingVerdict(id, viewId, findingId, verdict) {
      const v = get(id).views.find((x) => x.id === viewId);
      const f = v?.findings.find((x) => x.id === findingId);
      if (!v || !f) throw new ApiError(404, "finding_not_found", `No finding ${findingId}.`);
      f.status = "dismissed";
      f.evidence = { ...f.evidence, photographer: verdict, agent_note: verdict === "disclose" ? "Photographer: disclose as altered." : "Photographer: not an alteration of the property." };
      return delay(clone(v), 60);
    },
    async publish(id) {
      const shoot = get(id);
      shoot.public_token ??= `demo-${shoot.id}`;
      const pub = await publishMock(shoot, report(shoot), (blob) => this.blobUrl(shoot.id, blob));
      shoot.publication = pub;
      const { heads: _h, manifest_blob: _m, ...summary } = pub;
      return delay(clone({ ...summary, photos: pub.photos.map(({ room: _r, changes: _c, capture: _cap, chain: _ch, ...p }) => p) }), 300);
    },
    async patchListing(id, fields) {
      const shoot = get(id);
      shoot.listing = { ...shoot.listing, ...fields };
      return delay(clone(shoot), 80);
    },
    async createShare(id, label) {
      const shoot = get(id);
      const share = { token: `demo-r-${shoot.id}-${(shoot.shares?.length ?? 0) + 1}`, role: "client" as const, label: label ?? "", created_at: new Date().toISOString(), revoked: false, max_requests: 20 };
      (shoot.shares ??= []).push(share);
      return delay(clone(share), 120);
    },
    async revokeShare(id, token) {
      const share = get(id).shares?.find((x) => x.token === token);
      if (share) share.revoked = true;
      await delay(null, 60);
    },
    async listComments(id) {
      const shoot = get(id);
      return delay((shoot.comments ?? []).map((c) => commentView(shoot, c)), 60);
    },
    async buildKit(id) {
      const shoot = get(id);
      if (!shoot.publication || report(shoot).stale) await this.publish(id);
      const pub = shoot.publication!;
      const slug = (x: string) => x.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      const files: KitInfo["files"] = [];
      for (const p of pub.photos) {
        const stem = `${String(p.order).padStart(2, "0")}-${slug(p.label)}`;
        files.push({ path: `photos/${stem}.jpg`, size: 420_000 }, { path: `photos-labeled/${stem}.jpg`, size: 430_000 });
        if (p.disclosure && p.original) files.push({ path: `originals/${stem}-original.jpg`, size: 400_000 });
      }
      files.push(
        { path: "captions.csv", size: 1500 },
        { path: "disclosures.txt", size: 300 },
        { path: "listing.txt", size: 400 },
        { path: "provenance/manifest.json", size: 20_000 },
        { path: "README.txt", size: 1800 },
      );
      const info: KitInfo = {
        name: `${slug(shoot.listing.address ?? shoot.title)}-listing-kit.zip`,
        size: files.reduce((n, f) => n + f.size, 0),
        files,
        built_at: new Date().toISOString(),
        publication: pub.version,
        photos: pub.photos.length,
        altered: pub.photos.filter((p) => p.disclosure).length,
        url: "",
        demo: true,
      };
      kits.set(id, info);
      return delay(clone(info), 900);
    },
    async getKit(id) {
      return delay(kits.has(id) ? clone(kits.get(id)!) : null, 40);
    },
    async lintListing(text) {
      return delay(lintListing(text), 60);
    },
    async lintRequest(text) {
      return delay(classifyRequest(text), 30);
    },
    async getPublic(token) {
      const shoot = byToken(token, "public");
      if (!shoot.publication) await this.publish(shoot.id);
      const pub = shoot.publication!;
      return delay(
        clone({
          token,
          title: shoot.listing.address ?? shoot.title,
          listing: shoot.listing,
          version: pub.version,
          published_at: pub.created_at,
          root: pub.root,
          photos: pub.photos.map((p) => ({
            ...p,
            changes: p.changes.map((c) => ({ kind: c.kind, what: c.what, by: c.by, category: c.category ?? null })),
          })),
        }),
        120,
      );
    },
    publicBlobUrl(_token, blob, w) {
      return this.blobUrl("", blob, w);
    },
    async getReview(token) {
      return delay(clone(reviewOf(byToken(token, "client"), token)), 100);
    },
    reviewBlobUrl(_token, blob, w) {
      return this.blobUrl("", blob, w);
    },
    async reviewVerdict(token, viewId, status) {
      const shoot = byToken(token, "client");
      const v = shoot.views.find((x) => x.id === viewId);
      if (!v) throw new ApiError(404, "view_not_found", `No photo ${viewId}.`);
      v.client = status ? { status, at: new Date().toISOString(), share: token } : null;
      return delay(clone(reviewOf(shoot, token)), 80);
    },
    async reviewComment(token, body) {
      const shoot = byToken(token, "client");
      const view = shoot.views.find((x) => x.id === body.view);
      if (!view) throw new ApiError(404, "view_not_found", `No photo ${body.view}.`);
      const text = body.text.trim().replace(/\s+/g, " ");
      const policy = classifyRequest(text);
      const comment: Comment = {
        id: "c_" + Math.random().toString(16).slice(2, 10),
        view: view.id,
        text,
        author: "client",
        share: token,
        point: body.point ?? null,
        mask: null,
        run: null,
        base_version: view.head,
        policy,
        status: "sent",
        note: "",
        created_at: new Date().toISOString(),
      };
      const allowed = policy.items.filter((i) => i.verdict !== "decline");
      if (policy.items.length && !allowed.length) {
        comment.status = "declined";
        comment.note = [...new Set(policy.items.map((i) => i.reason))].join(" ");
      } else {
        let maskId: string | undefined;
        if (body.point) {
          const mask = await this.createMask(shoot.id, view.id, [[body.point[0], body.point[1], 1]]);
          maskId = mask.id;
        }
        const { run_id } = queueOrStart(shoot, "chat", () => chatScript(shoot, text, [view.id], maskId, { origin: "client", comment: comment.id }), {
          mode: "chat",
          request: text,
          origin: "client",
        });
        comment.run = run_id;
        comment.mask = maskId ?? null;
        comment.status = "started";
      }
      (shoot.comments ??= []).push(comment);
      return delay(clone(commentView(shoot, comment)), 200);
    },
    async reviewUndo(token, commentId) {
      const shoot = byToken(token, "client");
      const c = shoot.comments?.find((x) => x.id === commentId && x.share === token);
      if (!c) throw new ApiError(404, "comment_not_found", "No such request on this link.");
      const v = shoot.views.find((x) => x.id === c.view)!;
      if (!c.base_version || v.head === c.base_version) throw new ApiError(400, "nothing_to_undo", "This photo is already back to how it was before the request.");
      v.head = c.base_version;
      c.status = "undone";
      c.note = "Undone by the client.";
      return delay(clone(commentView(shoot, c)), 80);
    },
    subscribeReviewRun(token, run, onEvent, onEnd) {
      return this.subscribeRun(byToken(token, "client").id, run, onEvent, onEnd);
    },
  };
}
