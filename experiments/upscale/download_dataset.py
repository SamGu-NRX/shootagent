"""Download the public AutoHDR Track 1 image manifest."""
from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
from pathlib import Path

MANIFEST = Path(r"C:\Users\sgu07\datasets\autohdr\upscale\manifest.json")
OUT = Path(r"C:\Users\sgu07\datasets\autohdr\upscale\images")


def download(item: tuple[str, str]) -> tuple[str, str]:
    name, url = item
    target = OUT / name
    if target.exists() and target.stat().st_size > 0:
        return name, "exists"
    partial = target.with_suffix(target.suffix + ".part")
    command = ["curl.exe", "-L", "--fail", "--retry", "4", "--retry-delay", "2", "-sS", url, "-o", str(partial)]
    subprocess.run(command, check=True)
    os.replace(partial, target)
    return name, "downloaded"


def main() -> None:
    data = json.loads(MANIFEST.read_text())
    items = []
    for triplet in data["triplets"]:
        for role in ("src", "lr", "hr"):
            entry = triplet[role]
            items.append((Path(entry["key"]).name, entry["url"]))
    OUT.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for index, result in enumerate(pool.map(download, items), 1):
            if index % 25 == 0 or index == len(items):
                print(f"{index}/{len(items)} {result[0]} {result[1]}", flush=True)


if __name__ == "__main__":
    main()
