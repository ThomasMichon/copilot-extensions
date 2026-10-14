"""Prepare declared static instructions in process, with a bounded hook backup."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import time
from typing import TYPE_CHECKING, Literal

from .launch_progress import step

if TYPE_CHECKING:
    from .config import Config
    from .state_root import StatePair

_LOG = logging.getLogger(__name__)
RefreshStatus = Literal["ready", "partial", "unavailable", "failed", "timeout", "skipped", "queued"]
DEFAULT_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class RefreshResult:
    status: RefreshStatus
    changed: int = 0
    unchanged: int = 0
    warnings: int = 0
    blocking: int = 0
    detail: str = ""
    removed: int = 0

    @property
    def diagnostic(self) -> str:
        counts = (
            f"{self.changed} installed, {self.unchanged} unchanged, "
            f"{self.removed} removed, {self.warnings} warnings, {self.blocking} failures"
        )
        return f"[local-guidance] {self.status}: {counts}" + (f"; {self.detail}" if self.detail else "")


def _report(result: RefreshResult) -> RefreshResult:
    if result.status not in ("ready", "skipped") or result.warnings or result.detail:
        _LOG.warning(result.diagnostic)
    return result


def _checkout_root(path: str | Path) -> Path:
    candidate = Path(path).absolute()
    for root in (candidate, *candidate.parents):
        if (root / ".git").exists():
            return root
    return candidate


def mark_pair_pending(repo_root: str | Path) -> None:
    from .static_instructions import pair_pending_path, publication_lock

    root = Path(repo_root).resolve()
    try:
        try:
            (root / ".git").lstat()
        except FileNotFoundError:
            return
        with publication_lock(root):
            marker = pair_pending_path(root)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
    except (OSError, ValueError, RuntimeError) as exc:
        from .knowledge_plugins import KnowledgePluginError

        raise KnowledgePluginError(f"cannot retain incomplete paired preparation: {exc}") from exc


def mark_failed_pair_composition(resolution: StatePair) -> None:
    if not resolution.paired:
        return
    harness = next(
        (entry for entry in (resolution.current, resolution.sibling) if entry and entry.role == "harness"),
        None,
    )
    if harness is None:
        return
    from .knowledge_plugins import _overlay_transaction

    root = Path(harness.path).resolve()
    with _overlay_transaction(root / ".github" / "copilot" / "settings.local.json"):
        mark_pair_pending(root)


def complete_pair_composition(repo_root: str | Path, summary: dict) -> None:
    if not summary.get("paired") or summary.get("action") not in ("composed", "disabled"):
        return
    from .static_instructions import pair_pending_path, publication_lock

    try:
        root = Path(repo_root).resolve()
        try:
            (root / ".git").lstat()
            marker = pair_pending_path(root)
            marker.lstat()
        except FileNotFoundError:
            return
        with publication_lock(root):
            marker.unlink(missing_ok=True)
    except (OSError, ValueError, RuntimeError) as exc:
        from .knowledge_plugins import KnowledgePluginError

        raise KnowledgePluginError(f"cannot complete paired preparation: {exc}") from exc


def prepare_for_launch(
    repo_root: str | Path, *, dry_run: bool = False,
    compose_pair: bool = False, config: Config | None = None,
) -> RefreshResult:
    if dry_run:
        return RefreshResult("skipped", detail="dry-run launch")
    with step("Preparing instruction projections"):
        if compose_pair:
            from . import knowledge_plugins

            try:
                knowledge_plugins.compose_from_pair(cwd=repo_root, config=config)
            except (knowledge_plugins.KnowledgePluginError, OSError, RuntimeError, ValueError) as exc:
                result = RefreshResult("failed", blocking=1, detail=f"plugin composition failed: {exc}")
                print(result.diagnostic, file=sys.stderr, flush=True)
                return _report(result)
        return refresh_local_cache(repo_root, timeout=None)


def refresh_local_cache(
    repo_root: str | Path, *, home: Path | None = None,
    timeout: float | None = DEFAULT_TIMEOUT_S,
) -> RefreshResult:
    from .static_instructions import publish

    deadline = time.monotonic() + timeout if timeout is not None else None
    try:
        with step("Copying declared instructions", enabled=timeout is None):
            changed, unchanged, removed = publish(
                _checkout_root(repo_root).resolve(), home or Path.home(), deadline=deadline,
            )
        result = RefreshResult("ready", changed=changed, unchanged=unchanged, removed=removed)
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        result = RefreshResult("timeout", blocking=1, detail=f"instruction copy timed out: {exc}")
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        result = RefreshResult("failed", blocking=1, detail=f"instruction copy failed: {exc}")
    if timeout is None:
        print(result.diagnostic, file=sys.stderr, flush=True)
    return _report(result)


def _worker_directory(root: Path) -> Path:
    from . import local_cache_worker

    directory = local_cache_worker.metadata_directory(root)
    if directory is None:
        raise ValueError("cannot resolve this checkout's Git administrative directory")
    return directory


def _queue_refresh(cwd: str) -> RefreshResult:
    from . import local_cache_worker

    root = _checkout_root(cwd).resolve()
    return local_cache_worker.queue(root, _worker_directory(root))


def _run_refresh_worker(cwd: str) -> int:
    from . import local_cache_worker

    root = _checkout_root(cwd).resolve()
    return local_cache_worker.run(root, _worker_directory(root))


def sessionstart_diagnostic(cwd: str, *, deadline: float | None) -> str:
    """Queue the bounded backup without waiting inside SessionStart."""
    try:
        if deadline is not None and deadline - time.time() < 1.5:
            return RefreshResult("skipped", detail="backup refresh deadline too short").diagnostic + "\n"
        return _queue_refresh(cwd).diagnostic + "\n"
    except Exception as exc:
        return _report(RefreshResult("failed", detail=f"backup refresh failed: {exc}")).diagnostic + "\n"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the queued local instruction backup.")
    parser.add_argument("--refresh", required=True)
    raise SystemExit(_run_refresh_worker(parser.parse_args().refresh))
