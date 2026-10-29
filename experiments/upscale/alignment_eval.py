"""Measure source alignment and evaluate aligned guided upsampling variants."""
from __future__ import annotations

import csv
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import evaluate

OUT = evaluate.OUT / "alignment"
ALIGN_IDS = sorted({p.name.split("_")[0] for p in evaluate.ROOT.glob("*_hr.png")})[:-100][::20][:20]
TEST_IDS = sorted({p.name.split("_")[0] for p in evaluate.ROOT.glob("*_hr.png")})[-100:]


def alignment(src: np.ndarray, lr: np.ndarray) -> dict:
    src_lr = evaluate.resize(src, lr.shape[:2], cv2.INTER_AREA)
    src_gray = cv2.cvtColor(np.uint8(src_lr * 255), cv2.COLOR_RGB2GRAY)
    lr_gray = cv2.cvtColor(np.uint8(lr * 255), cv2.COLOR_RGB2GRAY)
    sift = cv2.SIFT_create(nfeatures=2500)
    kp1, des1 = sift.detectAndCompute(src_gray, None)
    kp2, des2 = sift.detectAndCompute(lr_gray, None)
    if des1 is None or des2 is None:
        raise RuntimeError("SIFT found no descriptors")
    matches = cv2.BFMatcher().knnMatch(des1, des2, k=2)
    good = [m for m, n in matches if m.distance < 0.72 * n.distance]
    if len(good) < 8:
        raise RuntimeError(f"only {len(good)} SIFT matches")
    points1 = np.float32([kp1[m.queryIdx].pt for m in good])
    points2 = np.float32([kp2[m.trainIdx].pt for m in good])
    h, mask = cv2.findHomography(points1, points2, cv2.RANSAC, 4.0)
    if h is None or mask is None:
        raise RuntimeError("homography estimation failed")
    inliers = mask.ravel().astype(bool)
    residual = np.linalg.norm((cv2.perspectiveTransform(points1[None], h)[0] - points2), axis=1)
    height, width = lr.shape[:2]
    corners = np.float32([[[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]]])
    warped = cv2.perspectiveTransform(corners, h)[0]
    top = warped[1] - warped[0]
    left = warped[3] - warped[0]
    scale = float((np.linalg.norm(top) / max(width - 1, 1) + np.linalg.norm(left) / max(height - 1, 1)) / 2)
    rotation = float(math.degrees(math.atan2(top[1], top[0])))
    center = warped.mean(axis=0)
    displacement = center - np.array([(width - 1) / 2, (height - 1) / 2])
    return {
        "matches": len(good),
        "inliers": int(inliers.sum()),
        "inlier_ratio": float(inliers.mean()),
        "median_reprojection_px": float(np.median(residual[inliers])),
        "p90_reprojection_px": float(np.percentile(residual[inliers], 90)),
        "shift_x_px": float(displacement[0]),
        "shift_y_px": float(displacement[1]),
        "shift_px": float(np.linalg.norm(displacement)),
        "scale": scale,
        "rotation_deg": rotation,
        "homography": h.tolist(),
    }


def warp_source(src: np.ndarray, lr: np.ndarray, hr_shape: tuple[int, int], fit: dict) -> np.ndarray:
    src_h, src_w = src.shape[:2]
    lr_h, lr_w = lr.shape[:2]
    hr_h, hr_w = hr_shape
    source_to_lr = np.array([[lr_w / src_w, 0, 0], [0, lr_h / src_h, 0], [0, 0, 1]], dtype=np.float64)
    lr_to_hr = np.array([[hr_w / lr_w, 0, 0], [0, hr_h / lr_h, 0], [0, 0, 1]], dtype=np.float64)
    h_full = lr_to_hr @ np.asarray(fit["homography"]) @ source_to_lr
    return cv2.warpPerspective(src, h_full, (hr_w, hr_h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)


def aligned_guided(src_aligned: np.ndarray, lr: np.ndarray, target_shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    return evaluate.render_guided(src_aligned, lr, target_shape)


def detail_transfer(src_aligned: np.ndarray, lr: np.ndarray, target_shape: tuple[int, int], alpha: float = 0.6) -> np.ndarray:
    base = evaluate.resize(lr, target_shape, cv2.INTER_LANCZOS4)
    source = evaluate.resize(src_aligned, target_shape, cv2.INTER_CUBIC)
    blur = cv2.GaussianBlur(source, (0, 0), 2.0)
    detail = source - blur
    base_blur = cv2.GaussianBlur(base, (0, 0), 2.0)
    source_luma = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
    base_luma = cv2.cvtColor(base_blur, cv2.COLOR_RGB2GRAY)
    gx1 = cv2.Sobel(source_luma, cv2.CV_32F, 1, 0, ksize=3)
    gy1 = cv2.Sobel(source_luma, cv2.CV_32F, 0, 1, ksize=3)
    gx2 = cv2.Sobel(base_luma, cv2.CV_32F, 1, 0, ksize=3)
    gy2 = cv2.Sobel(base_luma, cv2.CV_32F, 0, 1, ksize=3)
    cov = cv2.GaussianBlur(gx1 * gx2 + gy1 * gy2, (0, 0), 3.0)
    e1 = cv2.GaussianBlur(gx1 * gx1 + gy1 * gy1, (0, 0), 3.0)
    e2 = cv2.GaussianBlur(gx2 * gx2 + gy2 * gy2, (0, 0), 3.0)
    corr = cov / np.sqrt(np.maximum(e1 * e2, 1e-8))
    gate = np.clip((corr - 0.15) / 0.55, 0.0, 1.0)[..., None]
    return np.clip(base + alpha * gate * detail, 0.0, 1.0)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    alignment_rows = []
    for ident in ALIGN_IDS:
        src, lr, hr = evaluate.load_triplet(ident)
        try:
            fit = alignment(src, lr)
            fit.update({"id": ident, "src_aspect": src.shape[1] / src.shape[0], "lr_aspect": lr.shape[1] / lr.shape[0], "hr_aspect": hr.shape[1] / hr.shape[0], "src_shape": list(src.shape[:2]), "lr_shape": list(lr.shape[:2]), "hr_shape": list(hr.shape[:2])})
            alignment_rows.append(fit)
        except RuntimeError as exc:
            alignment_rows.append({"id": ident, "error": str(exc)})
    valid = [r for r in alignment_rows if "error" not in r]
    rows = []
    for index, ident in enumerate(TEST_IDS, 1):
        src, lr, hr = evaluate.load_triplet(ident)
        fit = alignment(src, lr)
        aligned = warp_source(src, lr, hr.shape[:2], fit)
        t0 = time.perf_counter(); guided, _ = aligned_guided(aligned, lr, hr.shape[:2]); guided_time = time.perf_counter() - t0
        t0 = time.perf_counter(); residual = evaluate.residual_from_guided(guided, lr, 0.02, 0.04); residual_time = time.perf_counter() - t0
        t0 = time.perf_counter(); detail = detail_transfer(aligned, lr, hr.shape[:2]); detail_time = time.perf_counter() - t0
        for name, pred, elapsed in (("aligned_guided", guided, guided_time), ("aligned_guided_residual", residual, residual_time), ("aligned_detail_transfer", detail, detail_time)):
            psnr, ssim = evaluate.metrics(pred, hr)
            rows.append({"id": ident, "method": name, "psnr": psnr, "ssim": ssim, "cpu_seconds": elapsed})
        if index % 10 == 0:
            print(f"evaluated {index}/{len(TEST_IDS)}", flush=True)
    summary = []
    for method in ("aligned_guided", "aligned_guided_residual", "aligned_detail_transfer"):
        vals = [r for r in rows if r["method"] == method]
        summary.append({"method": method, "psnr": float(np.mean([r["psnr"] for r in vals])), "ssim": float(np.mean([r["ssim"] for r in vals])), "cpu_seconds": float(np.mean([r["cpu_seconds"] for r in vals])), "cpu_cost_usd_per_image": float(np.mean([r["cpu_seconds"] for r in vals]) * 0.04 / 3600)})
    worst = sorted([r for r in rows if r["method"] == "aligned_guided_residual"], key=lambda r: r["psnr"])[:3]
    contact_paths = []
    contact_dir = OUT / "contact_sheets"
    contact_dir.mkdir(parents=True, exist_ok=True)
    for record in worst:
        ident = record["id"]
        src, lr, hr = evaluate.load_triplet(ident)
        aligned = warp_source(src, lr, hr.shape[:2], alignment(src, lr))
        residual = evaluate.residual_from_guided(aligned_guided(aligned, lr, hr.shape[:2])[0], lr, 0.02, 0.04)
        base = evaluate.resize(lr, hr.shape[:2], cv2.INTER_LANCZOS4)
        panels = [base, residual, hr]
        h, w = hr.shape[:2]
        crop_h, crop_w = min(700, h), min(1000, w)
        x, y = 0, 0
        thumbs = []
        for panel in panels:
            image = cv2.resize(np.uint8(panel[y:y + crop_h, x:x + crop_w] * 255), (420, round(crop_h * 420 / crop_w)), interpolation=cv2.INTER_AREA)
            thumbs.append(image)
        sheet = np.concatenate(thumbs, axis=1)
        path = contact_dir / f"aligned_case_{ident}.jpg"
        cv2.imwrite(str(path), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
        contact_paths.append(str(path.resolve()))
    result = {"alignment_sample_count": len(valid), "alignment": {"median_shift_px": float(np.median([r["shift_px"] for r in valid])), "median_scale": float(np.median([r["scale"] for r in valid])), "median_rotation_deg": float(np.median([r["rotation_deg"] for r in valid])), "median_reprojection_px": float(np.median([r["median_reprojection_px"] for r in valid])), "median_inlier_ratio": float(np.median([r["inlier_ratio"] for r in valid])), "aspect_ratios": [{"id": r["id"], "src": r["src_aspect"], "lr": r["lr_aspect"], "hr": r["hr_aspect"]} for r in valid]}, "summary": summary, "worst_cases": worst, "contact_sheets": contact_paths, "peak_memory_note": "CPU peak was not separately tracked in this follow-up; prior full run peaked at about 1.88 GB."}
    (OUT / "alignment_rows.json").write_text(json.dumps(alignment_rows, indent=2))
    (OUT / "per_image.csv").open("w", newline="").write("id,method,psnr,ssim,cpu_seconds\n" + "\n".join(f"{r['id']},{r['method']},{r['psnr']},{r['ssim']},{r['cpu_seconds']}" for r in rows) + "\n")
    (OUT / "results.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
