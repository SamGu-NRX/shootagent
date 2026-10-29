"""Evaluate simple guided upsampling methods on the AutoHDR Track 1 data."""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

try:
    import psutil
except ImportError:
    psutil = None

try:
    import torch
except ImportError:
    torch = None

ROOT = Path(r"C:\Users\sgu07\datasets\autohdr\upscale\images")
OUT = Path(r"C:\Users\sgu07\Programming Projects\shootagent-upscale\experiments\upscale\results")
METHODS = ("bicubic", "lanczos", "guided", "guided_residual")


def read_rgb(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"cannot read image: {path}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0


def resize(image: np.ndarray, shape: tuple[int, int], interpolation: int) -> np.ndarray:
    height, width = shape
    return cv2.resize(image, (width, height), interpolation=interpolation)


def affine_coefficients(src_lr: np.ndarray, lr: np.ndarray, grid=(12, 20), ridge=1e-3) -> np.ndarray:
    """Fit one RGB affine map per low-resolution tile."""
    height, width, _ = lr.shape
    rows, cols = grid
    coeff = np.empty((rows, cols, 4, 3), dtype=np.float32)
    for row in range(rows):
        y0, y1 = round(row * height / rows), round((row + 1) * height / rows)
        for col in range(cols):
            x0, x1 = round(col * width / cols), round((col + 1) * width / cols)
            x = src_lr[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64)
            y = lr[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64)
            design = np.concatenate((x, np.ones((len(x), 1))), axis=1)
            normal = design.T @ design
            normal.flat[::5] += ridge
            coeff[row, col] = np.linalg.solve(normal, design.T @ y).astype(np.float32)
    return coeff


def render_guided(src: np.ndarray, lr: np.ndarray, target_shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    src_lr = resize(src, lr.shape[:2], cv2.INTER_AREA)
    coeff = affine_coefficients(src_lr, lr)
    rows, cols = coeff.shape[:2]
    smooth = np.empty_like(coeff)
    for i in range(4):
        smooth[:, :, i, :] = cv2.GaussianBlur(coeff[:, :, i, :], (0, 0), 1.2)
    maps = np.empty((*target_shape, 4, 3), dtype=np.float32)
    for i in range(4):
        maps[:, :, i, :] = cv2.resize(smooth[:, :, i, :], (target_shape[1], target_shape[0]), interpolation=cv2.INTER_CUBIC)
    src_hr = resize(src, target_shape, cv2.INTER_CUBIC)
    out = np.einsum("hwkc,hwk->hwc", maps[:, :, :3, :], src_hr) + maps[:, :, 3, :]
    return np.clip(out, 0.0, 1.0), maps


def residual_map(guided: np.ndarray, lr: np.ndarray) -> np.ndarray:
    guided_lr = resize(guided, lr.shape[:2], cv2.INTER_AREA)
    residual = np.mean(np.abs(guided_lr - lr), axis=2)
    return cv2.GaussianBlur(residual, (0, 0), 2.0)


def residual_from_guided(guided: np.ndarray, lr: np.ndarray, threshold: float, softness: float) -> np.ndarray:
    mask_lr = residual_map(guided, lr)
    mask = resize(mask_lr, guided.shape[:2], cv2.INTER_CUBIC)
    mask = np.clip((mask - threshold) / softness + 0.5, 0.0, 1.0)[..., None]
    lr_up = resize(lr, guided.shape[:2], cv2.INTER_CUBIC)
    return np.clip(guided * (1.0 - mask) + lr_up * mask, 0.0, 1.0)


def residual_method(src: np.ndarray, lr: np.ndarray, target_shape: tuple[int, int], threshold: float, softness: float) -> np.ndarray:
    guided, _ = render_guided(src, lr, target_shape)
    return residual_from_guided(guided, lr, threshold, softness)


def metrics(pred: np.ndarray, target: np.ndarray) -> tuple[float, float]:
    return (
        float(peak_signal_noise_ratio(target, pred, data_range=1.0)),
        float(structural_similarity(target, pred, channel_axis=2, data_range=1.0)),
    )


def load_triplet(ident: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    src = read_rgb(next(ROOT.glob(f"{ident}_src.*")))
    lr = read_rgb(next(ROOT.glob(f"{ident}_lr.*")))
    hr = read_rgb(next(ROOT.glob(f"{ident}_hr.*")))
    return src, lr, hr


def tune(train_ids: list[str]) -> tuple[float, float]:
    candidates = [(t, s) for t in (0.02, 0.04, 0.07) for s in (0.015, 0.04)]
    scores = []
    for threshold, softness in candidates:
        psnrs = []
        for ident in train_ids:
            src, lr, hr = load_triplet(ident)
            small_shape = (max(128, hr.shape[0] // 4), max(128, hr.shape[1] // 4))
            src_small = resize(src, small_shape, cv2.INTER_AREA)
            lr_small = resize(lr, (max(64, lr.shape[0] // 4), max(64, lr.shape[1] // 4)), cv2.INTER_AREA)
            hr_small = resize(hr, small_shape, cv2.INTER_AREA)
            pred = residual_method(src_small, lr_small, hr_small.shape[:2], threshold, softness)
            psnrs.append(metrics(pred, hr_small)[0])
        scores.append((float(np.mean(psnrs)), threshold, softness))
        print(f"tune threshold={threshold:.3f} softness={softness:.3f} train_psnr={scores[-1][0]:.3f}", flush=True)
    best = max(scores)
    return best[1], best[2]


def gpu_render(src: np.ndarray, maps: np.ndarray, lr: np.ndarray, target_shape: tuple[int, int], residual: bool, threshold: float, softness: float) -> np.ndarray:
    if torch is None or not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable")
    device = torch.device("cuda")
    src_t = torch.from_numpy(src).permute(2, 0, 1)[None].to(device)
    maps_t = torch.from_numpy(maps).permute(2, 3, 0, 1)[None].to(device)
    src_t = torch.nn.functional.interpolate(src_t, size=target_shape, mode="bilinear", align_corners=False)
    out = (maps_t[:, :3] * src_t[:, :, None]).sum(dim=1) + maps_t[:, 3]
    out = out.permute(0, 2, 3, 1).clamp(0, 1)
    if residual:
        lr_t = torch.from_numpy(lr).permute(2, 0, 1)[None].to(device)
        lr_up = torch.nn.functional.interpolate(lr_t, size=target_shape, mode="bicubic", align_corners=False)
        low = torch.nn.functional.interpolate(out.permute(0, 3, 1, 2), size=lr.shape[:2], mode="area")
        residual_map_t = (low - lr_t).abs().mean(dim=1, keepdim=True)
        residual_map_t = torch.nn.functional.interpolate(residual_map_t, size=target_shape, mode="bilinear", align_corners=False)
        mask = ((residual_map_t - threshold) / softness + 0.5).clamp(0, 1)
        out = out.permute(0, 3, 1, 2) * (1 - mask) + lr_up * mask
        out = out.permute(0, 2, 3, 1)
    return out[0].detach().cpu().numpy()


def make_contact_sheets(records: list[dict], threshold: float, softness: float) -> list[str]:
    contact_dir = OUT / "contact_sheets"
    contact_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for record in records:
        ident = record["id"]
        src, lr, hr = load_triplet(ident)
        lr_up = resize(lr, hr.shape[:2], cv2.INTER_CUBIC)
        method3 = residual_method(src, lr, hr.shape[:2], threshold, softness)
        h, w = hr.shape[:2]
        crop_h, crop_w = min(700, h), min(1000, w)
        y = min(max(0, int(record["crop_y"])), h - crop_h)
        x = min(max(0, int(record["crop_x"])), w - crop_w)
        panels = [lr_up[y:y + crop_h, x:x + crop_w], method3[y:y + crop_h, x:x + crop_w], hr[y:y + crop_h, x:x + crop_w]]
        labels = ["LR bicubic", "Guided + residual", "HR target"]
        panel_w = 420
        panel_h = round(crop_h * panel_w / crop_w)
        sheet = Image.new("RGB", (panel_w * 3, panel_h + 34), "white")
        draw = ImageDraw.Draw(sheet)
        for i, (panel, label) in enumerate(zip(panels, labels)):
            image = Image.fromarray(np.uint8(np.clip(panel, 0, 1) * 255)).resize((panel_w, panel_h), Image.Resampling.LANCZOS)
            sheet.paste(image, (i * panel_w, 34))
            draw.text((i * panel_w + 8, 8), f"{ident} {label}", fill="black")
        path = contact_dir / f"case_{ident}.jpg"
        sheet.save(path, quality=92)
        paths.append(str(path.resolve()))
    combined = Image.new("RGB", (1260, (panel_h + 34) * len(records)), "white")
    for i, path in enumerate(paths):
        combined.paste(Image.open(path), (0, i * (panel_h + 34)))
    combined_path = contact_dir / "six_cases_contact_sheet.jpg"
    combined.save(combined_path, quality=92)
    paths.append(str(combined_path.resolve()))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--heldout", type=int, default=100)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    ids = sorted({p.name.split("_")[0] for p in ROOT.glob("*_hr.png")})
    if len(ids) < args.heldout + 1:
        raise RuntimeError(f"only found {len(ids)} complete samples")
    train_ids, test_ids = ids[:-args.heldout], ids[-args.heldout:]
    # Tune on a deterministic subset of the training split. The held-out IDs remain untouched.
    tune_ids = train_ids[::40]
    threshold, softness = tune(tune_ids)
    print(f"selected threshold={threshold:.3f} softness={softness:.3f}", flush=True)
    rows = []
    process = psutil.Process(os.getpid()) if psutil else None
    cpu_peak = 0
    for index, ident in enumerate(test_ids, 1):
        src, lr, hr = load_triplet(ident)
        target_shape = hr.shape[:2]
        t0 = time.perf_counter(); bicubic = resize(lr, target_shape, cv2.INTER_CUBIC); bicubic_time = time.perf_counter() - t0
        t0 = time.perf_counter(); lanczos = resize(lr, target_shape, cv2.INTER_LANCZOS4); lanczos_time = time.perf_counter() - t0
        t0 = time.perf_counter(); guided, maps = render_guided(src, lr, target_shape); guided_time = time.perf_counter() - t0
        t0 = time.perf_counter(); guided_residual = residual_from_guided(guided, lr, threshold, softness); residual_time = time.perf_counter() - t0
        if process:
            cpu_peak = max(cpu_peak, process.memory_info().rss)
        for name, pred, elapsed in (("bicubic", bicubic, bicubic_time), ("lanczos", lanczos, lanczos_time), ("guided", guided, guided_time), ("guided_residual", guided_residual, residual_time)):
            psnr, ssim = metrics(pred, hr)
            rows.append({"id": ident, "method": name, "psnr": psnr, "ssim": ssim, "cpu_seconds": elapsed})
        if index % 10 == 0 or index == len(test_ids):
            print(f"evaluated {index}/{len(test_ids)}", flush=True)
    gpu_rows = []
    gpu_available = bool(torch is not None and torch.cuda.is_available())
    if gpu_available:
        for ident in test_ids[: min(20, len(test_ids))]:
            src, lr, hr = load_triplet(ident)
            guided, maps = render_guided(src, lr, hr.shape[:2])
            for name, residual in (("guided", False), ("guided_residual", True)):
                for _ in range(2):
                    gpu_render(src, maps, lr, hr.shape[:2], residual, threshold, softness)
                torch.cuda.synchronize(); torch.cuda.reset_peak_memory_stats()
                t0 = time.perf_counter()
                for _ in range(3):
                    gpu_render(src, maps, lr, hr.shape[:2], residual, threshold, softness)
                torch.cuda.synchronize()
                elapsed = (time.perf_counter() - t0) / 3
                gpu_rows.append({"method": name, "gpu_seconds": elapsed, "gpu_peak_bytes": torch.cuda.max_memory_allocated()})
    with (OUT / "per_image.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["id", "method", "psnr", "ssim", "cpu_seconds"])
        writer.writeheader(); writer.writerows(rows)
    summary = []
    for method in METHODS:
        values = [r for r in rows if r["method"] == method]
        gpu = [r for r in gpu_rows if r["method"] == method]
        summary.append({"method": method, "psnr": float(np.mean([r["psnr"] for r in values])), "ssim": float(np.mean([r["ssim"] for r in values])), "cpu_seconds": float(np.mean([r["cpu_seconds"] for r in values])), "gpu_seconds": float(np.mean([r["gpu_seconds"] for r in gpu])) if gpu else None, "gpu_peak_bytes": int(max([r["gpu_peak_bytes"] for r in gpu], default=0))})
    worst = sorted([r for r in rows if r["method"] == "guided_residual"], key=lambda r: r["psnr"])[:6]
    for rank, record in enumerate(worst):
        record["crop_x"] = int((rank % 3) * 700)
        record["crop_y"] = int((rank // 3) * 500)
    sheets = make_contact_sheets(worst, threshold, softness)
    report = {"train_count": len(train_ids), "heldout_count": len(test_ids), "threshold": threshold, "softness": softness, "cpu_peak_bytes": cpu_peak, "gpu_available": gpu_available, "summary": summary, "worst_cases": worst, "contact_sheets": sheets, "cost_assumptions": {"cpu_usd_per_hour": 0.04, "gpu_usd_per_hour": 0.50, "gpu_timing_sample_count": min(20, len(test_ids))}}
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
