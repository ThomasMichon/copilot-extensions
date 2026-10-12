"""Portable watchdog-compatible signature parsing and injected provider reads."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

REPORTABLE_CONCLUSIONS = frozenset({"failure", "timed_out"})
SKIP_JOB_NAMES = frozenset(
    {
        "gate (confirm this is a dev commit)",
        "Promote dev -> main",
        "report failure (Phase 1 watchdog; detection + dedup only, no fix attempt)",
    }
)
_FAILED = re.compile(r"^FAILED (\S+::[^\[\s]+(?:\[[^\]]*\])?)(?: - .*)?$", re.MULTILINE)
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z ?", re.MULTILINE)
_ANSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


class LookupFailed(RuntimeError):
    """A provider read failed rather than reporting an empty result."""


@dataclass(frozen=True)
class Signature:
    key: str


def signature_key(job_name: str, test_id: str | None, fallback_text: str = "") -> str:
    """Return the established watchdog's stable 12-hex correlation key."""
    basis = f"{job_name}:{test_id}" if test_id else f"{job_name}:{fallback_text[:500]}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def build_signatures(job_name: str, log_text: str) -> list[Signature]:
    """Parse distinct pytest failures or the timestamp-independent job tail."""
    ids = dict.fromkeys(_FAILED.findall(_TIMESTAMP.sub("", log_text)))
    if ids:
        return [Signature(signature_key(job_name, node)) for node in ids]
    return [
        Signature(
            signature_key(
                job_name, None, _TIMESTAMP.sub("", "\n".join(log_text.splitlines()[-40:]))
            )
        )
    ]


def _fetch_run_jobs(repo: str, run_id: str, *, run_gh) -> list[dict]:
    """Read jobs through the caller's explicit repository-scoped adapter."""
    response = run_gh(["api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"])
    if response.returncode:
        raise LookupFailed(response.stderr)
    try:
        return json.loads(response.stdout)["jobs"]
    except (ValueError, KeyError, TypeError) as exc:
        raise LookupFailed("malformed jobs response") from exc


def _fetch_job_log(repo: str, job_id: int, *, run_gh) -> str:
    """Read sanitized log bytes through the same scoped provider."""
    argv = ["api", f"repos/{repo}/actions/jobs/{job_id}/logs"]
    response = run_gh([*argv, "--allow-escape-sequences"])
    if response.returncode and "unknown flag" in (response.stderr or "").lower():
        response = run_gh(argv)
    if response.returncode:
        raise LookupFailed(response.stderr)
    return _ANSI.sub("", response.stdout)
