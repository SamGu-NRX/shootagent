"""The listing kit: one zip with everything a listing agent uploads, posts or files.

    <slug>-listing-kit/
      README.txt              what is inside and how to use it
      photos/                 MLS-ready JPEGs (2048 px), showing order, no burned-in text
      photos-labeled/         the same photos; altered ones carry a disclosure strip and a QR code to the
                              original, for social posts, flyers and print
      originals/              the unaltered original paired with each altered photo (AB 723)
      captions.csv            order, file, room, caption, alt text, disclosure, original, sha256
      disclosures.txt         one disclosure per altered photo, ready to paste into photo remarks
      listing.txt             listing details, remarks, and the fair-housing check of the remarks
      provenance/manifest.json  hash chains from the camera's brackets to each published file
      reel/listing-reel.mp4   the 9:16 reel, when one has been rendered

Everything comes from the current publication (published first when the photos changed since).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from ..store import Publication, PublishedPhoto, Shoot, ShootStore, now_iso
from .fairhousing import lint
from .labels import burn_label
from .publish import is_stale, jpeg_bytes, manifest_dict, public_url, publish


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "listing"


def _caption(p: PublishedPhoto) -> str:
    room = (p.room or "").strip()
    label = room.title() if room else p.label
    return label if not re.match(r"^(?:defect_train|request-)", label) else f"Photo {p.order}"


def _alt_text(shoot: Shoot, p: PublishedPhoto, total: int) -> str:
    view = shoot.view(p.view)
    custom = view.meta.get("alt_text")
    if isinstance(custom, str) and custom.strip():
        return custom.strip()
    where = shoot.listing.address or shoot.title
    return f"{_caption(p)}, photo {p.order} of {total} of {where}."


def _readme(shoot: Shoot, pub: Publication, base: str, altered: int, files: list[str]) -> str:
    title = shoot.listing.address or shoot.title
    lines = [
        f"Listing kit: {title}",
        f"Built {now_iso()} from publication v{pub.version} ({pub.created_at}).",
        "",
        "photos/           Upload these to the MLS in this order. File names start with the showing order.",
        "photos-labeled/   Use these outside the MLS (social posts, flyers, print): altered photos carry the",
        "                  required disclosure and a QR code to the unaltered original.",
        "originals/        The unaltered original for each altered photo. California's AB 723 (Business and",
        "                  Professions Code 10140.8) requires altered listing photos to say so and be paired with",
        "                  the original; on a website, link to it.",
        "captions.csv      Room captions, alt text and the disclosure for each photo, for the MLS photo remarks.",
        "disclosures.txt   The disclosure sentences on their own.",
        "listing.txt       Listing details and remarks, with a fair-housing wording check.",
        "provenance/       A hash chain per photo from the camera's brackets to the published file.",
        "",
        f"{altered} of {len(pub.photos)} photos are digitally altered.",
        f"Public disclosure page with every original: {public_url(base, pub.token)}",
        "",
        "Check this kit: `shootagent verify <this zip>` recomputes every hash. Disclosures are generated from the",
        "edit log and QC verdicts; they are not legal advice.",
        "",
        "Files:",
        *[f"  {f}" for f in files],
    ]
    return "\n".join(lines) + "\n"


def _listing_txt(shoot: Shoot) -> str:
    L = shoot.listing
    rows = [
        ("Address", L.address or shoot.title),
        ("Price", L.price),
        ("Beds", f"{L.beds:g}" if L.beds is not None else None),
        ("Baths", f"{L.baths:g}" if L.baths is not None else None),
        ("Square feet", f"{L.sqft:,}" if L.sqft else None),
        ("Headline", L.headline),
    ]
    out = [f"{k}: {v}" for k, v in rows if v]
    out += ["", "Remarks:", L.description or "(none yet: add remarks in the Deliver tab)", ""]
    flags = lint(" ".join(x for x in (L.headline, L.description) if x))
    if flags:
        out.append("Fair-housing wording check:")
        out += [f"  [{f.severity}] \"{f.phrase}\": {f.why} Try: {f.suggestion}" for f in flags]
    else:
        out.append("Fair-housing wording check: no flagged phrases.")
    out.append("(A wording check against common fair-housing guidance, not legal advice.)")
    return "\n".join(out) + "\n"


def build_kit(store: ShootStore, shoot_id: str, base_url: str, include_reel: bool = True) -> dict:
    """Write the kit zip and return {path, name, size, files, built_at, publication}."""
    if is_stale(store, shoot_id):
        publish(store, shoot_id)
    shoot = store.load(shoot_id)
    pub = shoot.publication
    assert pub is not None
    root = f"{slug(shoot.listing.address or shoot.title)}-listing-kit"
    out_dir = store.shoot_dir(shoot_id) / "kit"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "latest.zip"
    tmp = out_dir / ".latest.zip.tmp"
    files: list[tuple[str, int]] = []
    total = len(pub.photos)
    altered = sum(1 for p in pub.photos if p.disclosure)

    def blob_bytes(blob: str) -> bytes:
        return store.blob_path(shoot_id, blob).read_bytes()

    with zipfile.ZipFile(tmp, "w") as z:
        def put(name: str, data: bytes, compress: bool) -> None:
            z.writestr(f"{root}/{name}", data, zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED)
            files.append((name, len(data)))

        rows = []
        disclosures = []
        for p in pub.photos:
            stem = f"{p.order:02d}-{slug(_caption(p))}"
            final = blob_bytes(p.final.blob)
            put(f"photos/{stem}.jpg", final, False)
            url = public_url(base_url, pub.token, p.view)
            if p.disclosure:
                head, _, detail = p.disclosure.partition(":")
                labeled = burn_label(
                    shoot_image(store, shoot_id, p.final.blob), f"{head.strip()} photo",
                    (detail.strip() or "See the original.").rstrip(".") + ".", url,
                )
                data, _, _ = jpeg_bytes(labeled)
                put(f"photos-labeled/{stem}.jpg", data, False)
            else:
                put(f"photos-labeled/{stem}.jpg", final, False)
            original_file = ""
            if p.original is not None and p.disclosure:
                original_file = f"originals/{stem}-original.jpg"
                put(original_file, blob_bytes(p.original.blob), False)
                disclosures.append(f"{stem}.jpg ({_caption(p)}): {p.disclosure} Unaltered original: {url}")
            rows.append({
                "order": p.order,
                "file": f"photos/{stem}.jpg",
                "room": _caption(p),
                "caption": _caption(p),
                "alt_text": _alt_text(shoot, p, total),
                "digitally_altered": "yes" if p.disclosure else "no",
                "disclosure": p.disclosure or "",
                "original_file": original_file,
                "original_url": url if p.disclosure else "",
                "sha256": hashlib.sha256(final).hexdigest(),
            })
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()) if rows else ["order"])
        writer.writeheader()
        writer.writerows(rows)
        put("captions.csv", buf.getvalue().encode("utf-8"), True)
        put("disclosures.txt", ("\n".join(disclosures) or "No photo in this set is digitally altered.").encode("utf-8")
            + b"\n", True)
        put("listing.txt", _listing_txt(shoot).encode("utf-8"), True)
        put("provenance/manifest.json", json.dumps(manifest_dict(shoot, pub), ensure_ascii=False, indent=1).encode("utf-8"),
            True)
        reel = store.shoot_dir(shoot_id) / "reel" / "latest.mp4"
        if include_reel and reel.exists():
            put("reel/listing-reel.mp4", reel.read_bytes(), False)
        readme = _readme(shoot, pub, base_url, altered, [f for f, _ in files] + ["README.txt"])
        put("README.txt", readme.encode("utf-8"), True)
    tmp.replace(path)
    info = {
        "name": f"{root}.zip",
        "size": path.stat().st_size,
        "files": [{"path": f, "size": n} for f, n in files],
        "built_at": now_iso(),
        "publication": pub.version,
        "photos": total,
        "altered": altered,
    }
    (out_dir / "latest.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    return {**info, "path": str(path)}


def shoot_image(store: ShootStore, shoot_id: str, blob: str):
    return store.get_image(shoot_id, blob)


def latest_kit(store: ShootStore, shoot_id: str) -> dict | None:
    p = store.shoot_dir(shoot_id) / "kit" / "latest.json"
    if not p.exists() or not (p.parent / "latest.zip").exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def verify_kit(zip_path: Path) -> dict:
    """Check a kit offline: every chain links and hashes, and every photo and original in the zip matches the
    sha256 its chain recorded."""
    from .provenance import verify_chain

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        manifest_name = next((n for n in names if n.endswith("provenance/manifest.json")), None)
        if manifest_name is None:
            return {"ok": False, "problems": ["no provenance/manifest.json in the zip"], "photos": []}
        manifest = json.loads(z.read(manifest_name))
        prefix = manifest_name[: -len("provenance/manifest.json")]
        by_hash: dict[str, str] = {}
        for n in names:
            if n.endswith(".jpg") and ("/photos/" in n or "/originals/" in n):
                by_hash[hashlib.sha256(z.read(n)).hexdigest()] = n[len(prefix):]
    problems: list[str] = []
    photos = []
    for p in manifest["photos"]:
        res = verify_chain(p["chain"])
        files = {k: by_hash.get((p.get(k) or {}).get("sha256", "")) for k in ("final", "original")}
        if files["final"] is None:
            res["problems"].append("the published photo is missing from photos/ or does not match its hash")
        if p.get("disclosure") and p.get("original") and files["original"] is None:
            res["problems"].append("the paired original is missing from originals/ or does not match its hash")
        ok = not res["problems"]
        photos.append({"order": p["order"], "label": p["label"], "ok": ok, "files": files, "problems": res["problems"]})
        problems += [f"photo {p['order']} ({p['label']}): {x}" for x in res["problems"]]
    return {"ok": not problems, "root": manifest.get("root"), "photos": photos, "problems": problems}
