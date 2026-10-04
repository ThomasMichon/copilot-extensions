"""Coverage-debt accounting: how stale a resolved baseline is relative to
the commit a diff-scoped selection is being made for.

Realizes the vision's `coverage-debt` Concept: a resolved, attribution-valid
baseline can still be too old to trust blindly -- a tunable amount of
commit-volume and/or wall-clock age since the baseline's own
`measured_commit` crosses a threshold and trips the smoke/fallback tier for
the **whole** selection, independent of (and in addition to) the
per-file/per-line "no_baseline_entry"/"line_not_attributed" triggers
`selection.select_tests` already raises. Either dimension crossing its own
threshold is sufficient; neither threshold is required (a caller may tune
with only one, or neither, configured).

Pure stdlib plus `git`; reads history only, never writes.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from ancestor_resolution import scrubbed_git_env
except ModuleNotFoundError:
    from tools.coverage_guided_selection.ancestor_resolution import scrubbed_git_env


class CoverageDebtError(RuntimeError):
    """Raised when git plumbing needed to assess debt fails unexpectedly."""


def _git(args: list[str], *, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
        env=scrubbed_git_env(),
    )
    if proc.returncode != 0:
        raise CoverageDebtError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def commits_since(repo_root: Path, measured_commit: str, head: str = "HEAD") -> int:
    """Number of commits reachable from `head` but not `measured_commit`
    (``git rev-list --count <measured_commit>..<head>``) -- the
    commit-volume dimension of coverage debt."""
    out = _git(["rev-list", "--count", f"{measured_commit}..{head}"], cwd=repo_root)
    return int(out.strip())


def age_seconds(repo_root: Path, measured_commit: str, *, now: float | None = None) -> float:
    """Wall-clock seconds between `measured_commit`'s own commit time and
    `now` (defaults to the real current time) -- the age dimension of
    coverage debt. Uses the commit's **committer** date (`%ct`), not author
    date: committer time reflects when the commit actually entered history
    (e.g. a rebase), which is what "how long has this baseline been aging"
    means here."""
    out = _git(["show", "-s", "--format=%ct", measured_commit], cwd=repo_root)
    committed_at = float(out.strip())
    current = time.time() if now is None else now
    return max(0.0, current - committed_at)


@dataclass(frozen=True)
class DebtAssessment:
    commit_volume: int
    age_seconds: float
    commit_volume_threshold: int | None
    age_threshold_seconds: float | None
    exceeded: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "commit_volume": self.commit_volume,
            "age_seconds": self.age_seconds,
            "commit_volume_threshold": self.commit_volume_threshold,
            "age_threshold_seconds": self.age_threshold_seconds,
            "exceeded": self.exceeded,
            "reasons": list(self.reasons),
        }


def assess_debt(
    repo_root: Path,
    measured_commit: str,
    *,
    head: str = "HEAD",
    commit_volume_threshold: int | None = None,
    age_threshold_seconds: float | None = None,
    now: float | None = None,
) -> DebtAssessment:
    """Measure both debt dimensions and decide whether either configured
    threshold is exceeded.

    A `None` threshold means that dimension is simply not enforced (always
    measured and reported for auditability, never silently enforced with an
    invented default) -- a caller with no opinion on age, for instance,
    passes `age_threshold_seconds=None` and gets `exceeded` driven purely by
    `commit_volume_threshold`, with `age_seconds` still present in the
    result for observability.
    """
    volume = commits_since(repo_root, measured_commit, head)
    age = age_seconds(repo_root, measured_commit, now=now)

    reasons: list[str] = []
    if commit_volume_threshold is not None and volume > commit_volume_threshold:
        reasons.append(
            f"commit_volume {volume} exceeds threshold {commit_volume_threshold}"
        )
    if age_threshold_seconds is not None and age > age_threshold_seconds:
        reasons.append(
            f"age_seconds {age:.0f} exceeds threshold {age_threshold_seconds:.0f}"
        )

    return DebtAssessment(
        commit_volume=volume,
        age_seconds=age,
        commit_volume_threshold=commit_volume_threshold,
        age_threshold_seconds=age_threshold_seconds,
        exceeded=bool(reasons),
        reasons=tuple(reasons),
    )
