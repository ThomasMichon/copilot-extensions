"""Failure-visible kind probes for related/config source acquisition only."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Literal

from dropin_registry import Finding, ScanAuthority

from .acquisition_report import SourceObservation, failed, observe


def probe_source(
    path: str | Path, kind: Literal["exists", "file", "directory"],
    observations: list[SourceObservation], stage: str,
) -> bool:
    """Follow links once; record absence, wrong kind or failure without a replay.

    Callers record positive selection when they actually consume the source.
    Legacy boolean probes remain with their value-only owners.
    """
    try:
        mode = os.stat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        observe(observations, str(path), stage, ScanAuthority.ABSENT)
        return False
    except (OSError, ValueError) as exc:
        failed(observations, str(path), stage, "unreadable-source", type(exc).__name__)
        return False
    matches = kind == "exists" or (stat.S_ISREG(mode) if kind == "file" else stat.S_ISDIR(mode))
    if not matches:
        observe(observations, str(path), stage, ScanAuthority.ABSENT, findings=(Finding(
            registry=stage, entry=str(path), status="inactive", reason="incorrect-kind",
        ),))
    return matches
