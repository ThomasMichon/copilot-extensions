"""Resilient filesystem drop-in registry primitives."""

from .model import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    ScanSnapshot,
)
from .scan import atomic_write_text, scan_directory
from .warnings import WarningBatch, WarningTracker

__all__ = [
    "EntryDecision",
    "EntryStatus",
    "Finding",
    "ScanAuthority",
    "ScanSnapshot",
    "WarningBatch",
    "WarningTracker",
    "atomic_write_text",
    "scan_directory",
]
