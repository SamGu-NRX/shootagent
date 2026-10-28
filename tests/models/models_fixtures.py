from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

CUSTOMER_CASES = Path.home() / "datasets" / "autohdr" / "customer" / "cases"


def _cuda() -> bool:
    try:
        import torch
    except ImportError:
        return False
    return torch.cuda.is_available()


gpu = pytest.mark.skipif(not _cuda(), reason="needs a CUDA GPU and the local model weights")


def load_case(case: str) -> np.ndarray:
    from PIL import Image

    path = CUSTOMER_CASES / case / "input" / "current.png"
    if not path.exists():
        pytest.skip(f"AutoHDR customer dataset not at {CUSTOMER_CASES}")
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(1234)
