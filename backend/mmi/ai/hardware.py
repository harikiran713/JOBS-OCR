"""Hardware detection and model recommendations.

Recommendations are *starting points* chosen for licence (permissive for
commercial redistribution/use), CPU viability and structured-output support.
They have NOT been benchmarked on marine manuals by this application; use the
evaluation harness (``python -m mmi.evaluation``) on your own verified dataset
before relying on a model.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

import psutil

CATALOG: list[dict[str, Any]] = [
    # Chat / extraction models
    {"role": "chat", "name": "qwen3:8b", "approx_download_gb": 5.2, "min_ram_gb": 12, "licence": "Apache-2.0",
     "notes": "Good structured JSON output; usable on CPU (slow) and on 6 GB+ GPUs."},
    {"role": "chat", "name": "qwen3:4b", "approx_download_gb": 2.6, "min_ram_gb": 8, "licence": "Apache-2.0",
     "notes": "Smaller option for 8 GB machines or 4 GB GPUs."},
    {"role": "chat", "name": "phi4-mini", "approx_download_gb": 2.5, "min_ram_gb": 8, "licence": "MIT",
     "notes": "Alternative small model with permissive licence."},
    # Embeddings
    {"role": "embedding", "name": "nomic-embed-text", "approx_download_gb": 0.3, "min_ram_gb": 4, "licence": "Apache-2.0",
     "notes": "English-focused, fast on CPU."},
    {"role": "embedding", "name": "bge-m3", "approx_download_gb": 1.2, "min_ram_gb": 8, "licence": "MIT",
     "notes": "Multilingual (manuals in German, Japanese, Korean, etc.)."},
    # Vision
    {"role": "vision", "name": "qwen2.5vl:7b", "approx_download_gb": 6.0, "min_ram_gb": 16, "licence": "Apache-2.0",
     "notes": "Reads image-based tables and symbols; GPU strongly recommended."},
    {"role": "vision", "name": "qwen2.5vl:3b", "approx_download_gb": 3.2, "min_ram_gb": 8, "licence": "Qwen Research License",
     "notes": "Smaller, but licence restricts commercial use — check before deployment."},
]  # fmt: skip


def detect_gpu() -> list[dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                vram = int(float(parts[1]))
            except ValueError:
                vram = 0
            gpus.append({"vendor": "NVIDIA", "name": parts[0], "vram_mb": vram, "driver": parts[2] if len(parts) > 2 else ""})
    return gpus


def hardware_summary() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    return {
        "cpu_count": psutil.cpu_count(logical=True) or 1,
        "physical_cores": psutil.cpu_count(logical=False) or 1,
        "ram_total_gb": round(vm.total / 1024**3, 1),
        "ram_available_gb": round(vm.available / 1024**3, 1),
        "gpus": detect_gpu(),
    }


def recommendations(hw: dict[str, Any] | None = None) -> dict[str, Any]:
    hw = hw or hardware_summary()
    ram = hw["ram_total_gb"]
    vram_gb = max((g["vram_mb"] for g in hw["gpus"]), default=0) / 1024
    items = []
    for m in CATALOG:
        fits = ram >= m["min_ram_gb"]
        items.append({**m, "fits": fits, "gpu_offload": vram_gb >= m["approx_download_gb"] * 0.9})
    pick = {
        role: next((i["name"] for i in items if i["role"] == role and i["fits"] and i["licence"] != "Qwen Research License"), None)
        for role in ("chat", "embedding", "vision")
    }
    return {
        "hardware": hw,
        "models": items,
        "suggested": pick,
        "disclaimer": "Suggestions are based on licence and hardware fit only. They have not been benchmarked "
        "on your manuals; validate with the evaluation harness before relying on results.",
    }
