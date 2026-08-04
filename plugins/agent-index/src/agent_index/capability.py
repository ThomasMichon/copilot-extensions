"""Machine capability detection + engine device selection.

Adoption matches the indexer's engine to the host's real capabilities rather than
assuming an accelerator (effort agent-index-engine-daemon, Phase 7;
vision §capability-matched-engine-runtime). It detects CUDA compatibility and
machine specs (cores, memory) and picks the embedding **device**: a compatible GPU
when present, CPU only when the host clears a capability floor, otherwise the host
is flagged as an underpowered indexer candidate (a hard block at designation time).

CUDA detection is authoritative when torch is importable (the engine venv) and a
best-effort ``nvidia-smi`` heuristic in the torch-free service/setup context; the
engine additionally falls back CUDA->CPU at model load so it never wedges on a
host whose driver is too old for the installed torch.
"""

from __future__ import annotations

import os
import shutil
import subprocess

# CPU-only indexer floor (a GPU host bypasses it). Overridable for unusual hosts.
MIN_CORES = int(os.environ.get("AGENT_INDEX_MIN_CORES", "4"))
MIN_RAM_GB = float(os.environ.get("AGENT_INDEX_MIN_RAM_GB", "8"))


def cpu_cores() -> int:
    """Logical CPU count (a practical proxy for indexing throughput)."""
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def total_ram_gb() -> float:
    """Total physical RAM in GiB, best-effort cross-platform (0.0 if unknown)."""
    try:
        names = getattr(os, "sysconf_names", {})
        if "SC_PAGE_SIZE" in names and "SC_PHYS_PAGES" in names:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024**3)
    except (ValueError, OSError, AttributeError):
        pass
    try:  # Windows
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MemStatus()
        stat.dwLength = ctypes.sizeof(_MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
            return stat.ullTotalPhys / (1024**3)
    except Exception:
        pass
    return 0.0


def cuda_available() -> bool:
    """True if a usable CUDA accelerator is present.

    Authoritative via ``torch.cuda.is_available()`` when torch is importable (the
    engine venv); otherwise a best-effort ``nvidia-smi`` presence heuristic for the
    torch-free service/setup context.
    """
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        pass
    smi = shutil.which("nvidia-smi")
    if not smi:
        return False
    try:
        out = subprocess.run(  # noqa: S603
            [smi, "-L"], capture_output=True, text=True, timeout=8
        )
        return out.returncode == 0 and "GPU" in out.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def detect() -> dict:
    """Snapshot of this host's relevant capabilities."""
    return {
        "cores": cpu_cores(),
        "ram_gb": round(total_ram_gb(), 1),
        "cuda": cuda_available(),
    }


def decide_device(
    caps: dict | None = None,
    *,
    min_cores: int = MIN_CORES,
    min_ram_gb: float = MIN_RAM_GB,
) -> dict:
    """Pick the engine device and whether this host is an acceptable indexer.

    Returns a dict with ``device`` (``cuda``/``cpu``), ``ok`` (False => underpowered
    CPU-only candidate, a hard block at designation), ``reason``, and the caps.
    """
    caps = caps or detect()
    result = {**caps, "min_cores": min_cores, "min_ram_gb": min_ram_gb}
    if caps["cuda"]:
        return {**result, "device": "cuda", "ok": True, "reason": "compatible GPU detected"}
    cores, ram = caps["cores"], caps["ram_gb"]
    if cores >= min_cores and ram >= min_ram_gb:
        return {
            **result,
            "device": "cpu",
            "ok": True,
            "reason": f"CPU meets floor ({cores} cores / {ram} GB)",
        }
    return {
        **result,
        "device": "cpu",
        "ok": False,
        "reason": (
            f"underpowered CPU-only host ({cores} cores / {ram} GB "
            f"< floor {min_cores} cores / {min_ram_gb} GB)"
        ),
    }


def effective_device(configured: str) -> str:
    """Resolve the device the engine will actually load on.

    Honors a *configured* ``cpu`` verbatim; downgrades a configured ``cuda`` (or
    ``auto``) to ``cpu`` when CUDA is not actually available, so the engine never
    wedges on a host whose driver is too old for the installed torch.
    """
    want = (configured or "cuda").strip().lower()
    if want == "cpu":
        return "cpu"
    return "cuda" if cuda_available() else "cpu"
