"""Disclosures from the op log and QC verdicts, publication with hash chains, and the listing kit."""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))
from fakes import make_shoot

from shootagent.compliance.disclosure import build_report, disclosure_text
from shootagent.compliance.kit import build_kit, verify_kit
from shootagent.compliance.provenance import sha256_file, verify_chain
from shootagent.compliance.publish import is_stale, publish
from shootagent.store import Bracket, Change, Finding
from shootagent.trace import TraceRun


def edit(store, sid, view, op, mode, request=None, **params):
    """Add a version as an agent run of `mode` would, with its run_start on disk."""
    trace = TraceRun(store.shoot_dir(sid), mode)
    trace.emit("run_start", mode=mode, request=request, views=[view])
    img = store.get_image(sid, store.load(sid).view(view).head_version.blob)
    out = img.copy()
    out[:4, :4] = (out[:4, :4].astype(int) + 37) % 256
    return store.add_version(sid, view, out, op=op, params=params, run=trace.id, note=f"{op} intent")


def mark_qc(store, sid, *views):
    def apply(s):
        for v in views:
            s.view(v).meta["qc_at"] = "2026-10-28T00:00:00+00:00"
    store.mutate(sid, apply)


def report_of(store, sid):
    return {p.view: p for p in build_report(store, sid).photos}


def test_customer_removal_is_disclosed_and_qc_fixes_are_restorations(tmp_path):
    store, sid = make_shoot(tmp_path, n_views=3)
    mark_qc(store, sid, "v01", "v02", "v03")
    edit(store, sid, "v01", "remove_object", "chat", request="remove the grey car", what="grey car")
    edit(store, sid, "v02", "restore_from_brackets", "qc")
    edit(store, sid, "v02", "recolor_region", "qc", hex="#6F8FB5")
    edit(store, sid, "v03", "adjust_exposure", "chat", ev=0.5)
    r = report_of(store, sid)
    assert r["v01"].status == "altered" and r["v01"].disclosure == "Digitally altered: grey car removed."
    assert r["v01"].changes[0].requested_by == "customer" and r["v01"].changes[0].request == "remove the grey car"
    assert r["v02"].status == "unaltered" and [c.kind for c in r["v02"].changes] == ["restoration", "restoration"]
    assert r["v03"].status == "unaltered" and r["v03"].changes[0].what == "exposure +0.50 EV"


def test_only_the_head_chain_counts(tmp_path):
    store, sid = make_shoot(tmp_path, n_views=1)
    mark_qc(store, sid, "v01")
    edit(store, sid, "v01", "remove_object", "chat", what="car")
    store.set_head(sid, "v01", "v01.0")  # the customer undid it
    assert report_of(store, sid)["v01"].status == "unaltered"


def test_editor_alterations_come_from_qc_verdicts(tmp_path):
    store, sid = make_shoot(tmp_path, n_views=3)
    mark_qc(store, sid, "v01", "v02", "v03")

    def findings(s):
        s.view("v01").findings.append(Finding(type="window_sky", explanation="sky differs", status="dismissed",
                                              evidence={"agent_note": "legitimate sky replacement"}))
        s.view("v02").findings.append(Finding(type="camera_person_removal", explanation="lamp missing", status="open",
                                              confidence=0.55))
        s.view("v03").findings.append(Finding(type="camera_person_removal", explanation="tripod gone",
                                              status="dismissed", evidence={"agent_note": "tripod removal ordered"}))
    store.mutate(sid, findings)
    r = report_of(store, sid)
    assert r["v01"].status == "altered" and r["v01"].changes[0].by == "editor"
    assert r["v01"].disclosure == "Digitally altered: sky replaced."
    assert r["v02"].status == "review"
    assert r["v03"].status == "unaltered" and r["v03"].changes[0].category == "gear"
    assert not build_report(store, sid).ready


def test_unchecked_brackets_make_a_photo_unverified_and_split_views_are_superseded(tmp_path):
    store, sid = make_shoot(tmp_path, n_views=2)

    def apply(s):
        s.view("v01").brackets = [Bracket(blob=s.view("v01").finished, index=0)]
        s.view("v02").meta["split_into"] = ["v02-a", "v02-b"]
    store.mutate(sid, apply)
    r = report_of(store, sid)
    assert r["v01"].status == "unverified"
    assert r["v02"].status == "superseded" and not r["v02"].deliver


def test_disclosure_wording():
    alt = lambda what, cat="removal": Change(kind="alteration", what=what, category=cat)
    assert disclosure_text([alt("car removed"), Change(kind="adjustment", what="exposure +0.5 EV")]) == "Digitally altered: car removed."
    assert disclosure_text([alt("virtually staged", "staging"), alt("sofa added")]) == "Virtually staged: sofa added."
    assert disclosure_text([Change(kind="overlay", what="watermark")]) is None


def test_publish_chains_verify_and_detect_tampering(tmp_path):
    store, sid = make_shoot(tmp_path, n_views=2)
    mark_qc(store, sid, "v01", "v02")
    edit(store, sid, "v01", "remove_object", "chat", what="trash bin")
    pub = publish(store, sid)
    assert not is_stale(store, sid)
    p1 = next(p for p in pub.photos if p.view == "v01")
    assert p1.disclosure and p1.original is not None and p1.original_kind == "delivered"
    digest = lambda b: sha256_file(store.blob_path(sid, b)) if store.blob_path(sid, b).exists() else None
    for p in pub.photos:
        res = verify_chain(p.chain, digest)
        assert res["ok"] and res["images_checked"] >= 2, res
    # Rewriting the published file breaks the chain's record of it.
    path = store.blob_path(sid, p1.final.blob)
    path.write_bytes(path.read_bytes()[:-8] + b"tampered")
    assert not verify_chain(p1.chain, digest)["ok"]
    # A new edit makes the publication stale; republishing keeps the token and bumps the version.
    edit(store, sid, "v02", "adjust_exposure", "chat", ev=0.3)
    assert is_stale(store, sid)
    again = publish(store, sid)
    assert again.token == pub.token and again.version == 2


def test_listing_kit_pairs_altered_photos_with_originals_and_verifies(tmp_path):
    store, sid = make_shoot(tmp_path, n_views=2)
    mark_qc(store, sid, "v01", "v02")
    edit(store, sid, "v02", "remove_object", "chat", what="car")
    info = build_kit(store, sid, "http://example.test")
    names = {f["path"] for f in info["files"]}
    assert {"captions.csv", "disclosures.txt", "listing.txt", "README.txt", "provenance/manifest.json"} <= names
    assert sum(n.startswith("photos/") for n in names) == 2 and sum(n.startswith("originals/") for n in names) == 1
    assert info["altered"] == 1
    with zipfile.ZipFile(info["path"]) as z:
        captions = z.read(next(n for n in z.namelist() if n.endswith("captions.csv"))).decode()
    assert "example.test/#/d/" in captions and "Digitally altered: car removed." in captions
    assert verify_kit(Path(info["path"]))["ok"]


def test_a_leftover_defect_is_not_an_alteration_and_the_photographer_rules_on_findings(tmp_path):
    store, sid = make_shoot(tmp_path, n_views=2)
    mark_qc(store, sid, "v01", "v02")

    def findings(s):
        s.view("v01").findings.append(Finding(id="f_hand", type="camera_person_removal",
                                              explanation="A hand enters the sky from the upper-left edge.", status="failed"))
        s.view("v02").findings.append(Finding(id="f_lamp", type="camera_person_removal",
                                              explanation="The floor lamp's stand is missing.", status="open",
                                              evidence={"agent_note": "Can't tell if the lamp was removed on purpose."}))
    store.mutate(sid, findings)
    r = report_of(store, sid)
    assert r["v01"].status == "unaltered" and r["v02"].status == "review"

    def rule(s):
        f = s.view("v02").findings[0]
        f.status, f.evidence = "dismissed", {"photographer": "disclose"}
    store.mutate(sid, rule)
    r = report_of(store, sid)
    assert r["v02"].status == "altered" and r["v02"].disclosure == "Digitally altered: object removed."


def test_readiness_counts_only_the_listing_set(tmp_path):
    store, sid = make_shoot(tmp_path, n_views=2)
    mark_qc(store, sid, "v01", "v02")
    store.mutate(sid, lambda s: s.view("v02").findings.append(
        Finding(type="paint_color", explanation="wall colour differs", status="open", confidence=0.7)))
    assert not build_report(store, sid).ready
    store.mutate(sid, lambda s: setattr(s.view("v01"), "approved", True))  # v02 leaves the listing set
    assert build_report(store, sid).ready


def test_unreviewed_low_confidence_candidates_do_not_block_publishing(tmp_path):
    store, sid = make_shoot(tmp_path, n_views=1)
    mark_qc(store, sid, "v01")
    store.mutate(sid, lambda s: s.view("v01").findings.extend([
        Finding(type="paint_color", explanation="surface colour differs", status="open", confidence=0.2),
        Finding(type="camera_person_removal", explanation="Something in every bracket is missing here", status="open",
                confidence=0.43),
    ]))
    r = report_of(store, sid)["v01"]
    # the possible erased object (0.43) needs a ruling; the faint colour candidate is only noted
    assert r.status == "review" and [c.category for c in r.changes] == ["camera_person_removal"]
    assert any("nobody has ruled on" in n for n in r.notes)
