"""Device choice, one process-wide GPU lock, and lazily loaded model slots.

The demo laptop has an 8 GB RTX 4060 that the desktop and other processes also use (about 2 GB was
taken when this was written), so the local models cannot all stay resident at once. Each model lives in
a `ModelSlot` that loads on first use. Before a slot loads, `ensure_free` unloads the least recently
used other slots until CUDA reports enough free memory for the new model's estimate.

Every forward pass runs under `gpu_lock()`. Tools run in a thread pool, and two concurrent passes on
one small GPU would either run out of memory or thrash, so they are serialised instead.
"""
from __future__ import annotations

import gc
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import torch

from ..config import get_settings

log = logging.getLogger(__name__)

_GPU_LOCK = threading.RLock()
_SLOTS: dict[str, ModelSlot] = {}
_SLOTS_GUARD = threading.Lock()


def get_device() -> torch.device:
    """`settings.torch_device` if set (and available), else cuda, then mps, then cpu."""
    wanted = get_settings().torch_device.strip().lower()
    if wanted:
        if wanted.startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(f"TORCH_DEVICE={wanted!r} but torch.cuda.is_available() is False")
        if wanted == "mps" and not torch.backends.mps.is_available():
            raise RuntimeError("TORCH_DEVICE='mps' but the MPS backend is not available")
        return torch.device(wanted)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def half_dtype(device: torch.device) -> torch.dtype:
    """Reduced precision for models that hold quality in it: bf16 on CUDA with bf16 support, fp16 on
    other CUDA and MPS, fp32 on CPU (CPU half kernels are slow or missing)."""
    if device.type == "cuda":
        return torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    if device.type == "mps":
        return torch.float16
    return torch.float32


def gpu_lock() -> threading.RLock:
    """Hold this around every forward pass and every load/unload. Re-entrant."""
    return _GPU_LOCK


@dataclass
class ModelSlot:
    """A lazily loaded model. `loader()` returns whatever the adapter needs (model, processor, ...)."""

    name: str
    loader: Callable[[], Any]
    est_mb: int  # VRAM the loaded weights plus a typical forward pass need, measured on the 4060
    value: Any = None
    last_used: float = 0.0
    load_ms: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def loaded(self) -> bool:
        return self.value is not None

    def get(self) -> Any:
        with _GPU_LOCK:
            if self.value is None:
                ensure_free(self.est_mb, keep=(self.name,))
                started = time.perf_counter()
                self.value = self.loader()
                self.load_ms = int((time.perf_counter() - started) * 1000)
                log.info("loaded %s in %d ms", self.name, self.load_ms)
            self.last_used = time.perf_counter()
            return self.value

    def unload(self) -> None:
        with _GPU_LOCK:
            if self.value is None:
                return
            self.value = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            log.info("unloaded %s", self.name)


def register(name: str, loader: Callable[[], Any], est_mb: int) -> ModelSlot:
    with _SLOTS_GUARD:
        if name in _SLOTS:
            return _SLOTS[name]
        slot = ModelSlot(name=name, loader=loader, est_mb=est_mb)
        _SLOTS[name] = slot
        return slot


def loaded_slots() -> list[str]:
    return [s.name for s in _SLOTS.values() if s.loaded]


def unload_all(keep: tuple[str, ...] = ()) -> None:
    with _GPU_LOCK:
        for slot in list(_SLOTS.values()):
            if slot.name not in keep:
                slot.unload()


def free_mb() -> float:
    """Free memory on the CUDA device as the driver sees it (includes other processes' usage), plus
    memory our caching allocator holds but is not using. Infinite on non-CUDA devices."""
    if get_device().type != "cuda":
        return float("inf")
    free, _total = torch.cuda.mem_get_info()
    reserved_unused = torch.cuda.memory_reserved() - torch.cuda.memory_allocated()
    return (free + reserved_unused) / 2**20


def ensure_free(mb: int, keep: tuple[str, ...] = ()) -> None:
    """Unload least recently used slots (except `keep`) until `mb` MiB are free on CUDA."""
    with _GPU_LOCK:
        if free_mb() >= mb:
            return
        candidates = sorted((s for s in _SLOTS.values() if s.loaded and s.name not in keep), key=lambda s: s.last_used)
        for slot in candidates:
            slot.unload()
            if free_mb() >= mb:
                return
        if free_mb() < mb:
            log.warning("only %.0f MiB free on CUDA after unloading idle models; %d MiB wanted", free_mb(), mb)


def reset_peak() -> None:
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()


def vram_stats() -> dict[str, float]:
    """MiB: this process's peak and current allocations, and what the driver reports free/total."""
    if not torch.cuda.is_available():
        return {}
    free, total = torch.cuda.mem_get_info()
    return {
        "peak_allocated_mb": round(torch.cuda.max_memory_allocated() / 2**20),
        "peak_reserved_mb": round(torch.cuda.max_memory_reserved() / 2**20),
        "allocated_mb": round(torch.cuda.memory_allocated() / 2**20),
        "device_free_mb": round(free / 2**20),
        "device_total_mb": round(total / 2**20),
    }
