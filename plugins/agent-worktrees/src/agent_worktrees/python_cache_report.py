"""Compatibility prune reports, including held and unmeasured byte counts."""
from dataclasses import asdict, dataclass, field
import os
from pathlib import Path
import re
import shlex
from typing import Any


@dataclass
class Candidate:
    path: str
    status: str
    bytes: int = 0
    files: int = 0
    reason: str = ""
    measured: bool = False


@dataclass
class PruneReport:
    cache_root: str
    apply: bool
    min_age_seconds: float
    liveness: str = "unavailable"
    age_only_allowed: bool = False
    candidates: list[Candidate] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.candidates:
            counts[item.status] = counts.get(item.status, 0) + 1
        counts["bytes"] = sum(c.bytes for c in self.candidates if c.status in ("removed", "eligible"))
        counts["measured_bytes"] = sum(c.bytes for c in self.candidates if c.measured)
        counts["held_bytes"] = sum(c.bytes for c in self.candidates if c.measured and c.status == "skipped")
        counts["unmeasured"] = sum(not c.measured for c in self.candidates)
        return counts

    def as_dict(self) -> dict[str, Any]:
        reasons: dict[str, dict[str, int]] = {}
        for item in self.candidates:
            if not item.reason:
                continue
            group = reasons.setdefault(item.reason, {"count": 0, "measured_bytes": 0, "unmeasured": 0})
            group["count"] += 1
            group["measured_bytes"] += item.bytes if item.measured else 0
            group["unmeasured"] += not item.measured
        return {
            "cache_root": self.cache_root, "apply": self.apply,
            "min_age_seconds": self.min_age_seconds, "liveness": self.liveness,
            "age_only_allowed": self.age_only_allowed, "summary": self.summary(),
            "bytes_by_reason": reasons, "inspection_command": self.inspection_command(),
            "candidates": [asdict(c) for c in self.candidates],
        }

    def inspection_command(self) -> str:
        args = [
            "agent-worktrees", "python-cache", "prune", "--root",
            str(Path(self.cache_root).parent.parent), "--min-age-hours",
            format(self.min_age_seconds / 3600, ".12g"), "--json",
        ]
        if os.name == "nt":
            return " ".join(
                arg if re.fullmatch(r"[A-Za-z0-9_.-]+", arg) else "'" + arg.replace("'", "''") + "'"
                for arg in args
            )
        return shlex.join(args)
