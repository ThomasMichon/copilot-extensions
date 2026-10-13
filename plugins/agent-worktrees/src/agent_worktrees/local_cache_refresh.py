"""Prepare local instruction projections using this payload's own engine.

The canonical implementation is shared with customizing-copilot on dev;
promotion vendors it into agent-worktrees, so a shipped standalone runtime
never discovers or executes a sibling plugin. Source resolution inside the
engine is scoped to the target repository's enabled instruction providers.

Foreground create/resume waits for completion with progress. SessionStart
queues a separate bounded backup worker rather than waiting inside the hook.
Outcomes distinguish delivery, warnings and failure; no failed render is
reported as successfully installed guidance.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from .launch_progress import step

_LOG = logging.getLogger(__name__)
RefreshStatus = Literal["ready", "partial", "unavailable", "failed", "timeout", "skipped", "queued"]


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
        suffix = f"; {self.detail}" if self.detail else ""
        return f"[local-guidance] {self.status}: {counts}{suffix}"


def _report(result: RefreshResult) -> RefreshResult:
    if result.status not in ("ready", "skipped") or result.warnings or result.detail:
        _LOG.warning(result.diagnostic)
    return result


def _render_result(stdout: str, returncode: int) -> RefreshResult:
    data = json.loads(stdout)
    if not isinstance(data, dict) or data.get("operation") != "render-local-cache":
        raise ValueError("renderer returned an invalid result")
    for key in ("changed", "written", "removed", "unchanged"):
        value = data.get(key)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"renderer returned invalid {key} paths")
    for key in ("warnings", "blocking"):
        if type(data.get(key)) is not int or data[key] < 0:
            raise ValueError(f"renderer returned an invalid {key} count")
    if not isinstance(data.get("findings"), list) or any(
        not isinstance(item, dict) for item in data["findings"]
    ):
        raise ValueError("renderer returned invalid findings")
    changed, unchanged = len(data["written"]), len(data["unchanged"])
    blocking = data["blocking"]
    status: RefreshStatus = "ready"
    if blocking:
        status = "partial" if changed + unchanged else "failed"
    elif returncode:
        status = "failed"
    detail = f"renderer exited with code {returncode}" if returncode and not blocking else ""
    findings = "; ".join(
        f"{item.get('check')}: {item.get('path')}: {item.get('message')}"
        for item in sorted(data["findings"], key=lambda item: item.get("severity") != "blocking")[:3]
    )[:1000]
    if findings:
        detail = f"{detail}; {findings}" if detail else findings
    return RefreshResult(status, changed, unchanged, data["warnings"], blocking, detail, len(data["removed"]))


def _checkout_root(path: str | Path) -> Path:
    candidate = Path(path).absolute()
    for root in (candidate, *candidate.parents):
        if (root / ".git").exists():
            return root
    return candidate


def prepare_for_launch(repo_root: str | Path, *, dry_run: bool = False) -> RefreshResult:
    """Install local guidance before instruction loading; dry runs never write."""
    if dry_run:
        return RefreshResult("skipped", detail="dry-run launch")
    with step("Preparing instruction projections"):
        return refresh_local_cache(repo_root, timeout=None)

# The asynchronous backup remains bounded; foreground preparation waits.
DEFAULT_TIMEOUT_S = 120.0


def _resolve_cli_script(
    home: Path, *, timeout: float | None, outcomes: list[RefreshResult] | None = None
) -> Path | None:
    """Resolve the packaged engine from this installation's own payload."""
    try:
        from .projection_runtime_assets import projection_cli_script

        return projection_cli_script()
    except (OSError, ValueError) as exc:
        if outcomes is not None:
            outcomes.append(RefreshResult("failed", detail=f"packaged projection engine unavailable: {exc}"))
        return None


def _resolve_own_agent_worktrees_command() -> str | None:
    """Resolve this exact installation cell's own ``agent-worktrees``
    binstub path, per ``reviewing-customizations/SKILL.md``'s own
    ``agent-worktrees-repo`` marketplace-source contract: a caller-supplied,
    catalog-resolved command, never ambient ``PATH`` (which could select a
    different installation cell's command, or none). The global
    ``~/.local/bin/agent-worktrees`` shim is not cell-pinned; the
    cell-pinned command lives under the owning payload's own
    ``bin/payload/`` (the same path every project binstub execs into --
    see ``installer._project_binstub_specs``), resolved via
    ``installer._payload_root()``. ``None`` when that payload command isn't
    deployed, or the payload root itself can't be resolved -- the CLI then
    falls back to its own ambient resolution, unchanged from before this
    existed.
    """
    try:
        from . import installer
    except Exception:
        return None
    try:
        payload = installer._payload_root()
    except Exception:
        return None
    name = "agent-worktrees.cmd" if os.name == "nt" else "agent-worktrees"
    candidate = payload / "bin" / "payload" / name
    return str(candidate) if candidate.is_file() else None


def refresh_local_cache(
    repo_root: str | Path,
    *,
    home: Path | None = None,
    timeout: float | None = DEFAULT_TIMEOUT_S,
) -> RefreshResult:
    """Refresh enabled sources' gitignored, auto-discovered local instructions.

    Call at create, resume, JSON launch planning and sessionStart. Results report
    actual installation; nonzero exits, invalid output and timeouts cannot
    silently become success. ``timeout`` is the hard, enforced ceiling
    on this call's **own** cost (not counting ``push_timeout.run_
    bounded``'s own five-second cleanup grace),
    shared between locating the owned engine and rendering with the actual
    remaining time; no separate lookup cutoff is imposed.
    ``None`` waits for foreground preparation to finish, reporting renderer
    discovery/render stages and elapsed-time heartbeats. Asynchronous backup
    workers never select that mode.
    Both run via
    ``push_timeout.run_bounded`` rather than a plain ``subprocess.run(
    timeout=...)``, which only terminates its direct child -- the
    CLI can spawn descendants (Git child processes, an
    ``agent-worktrees`` lookup), which a bare ``timeout=`` would leave
    running past a stall. ``run_bounded`` kills each whole process tree
    instead. A timeout (or any other failure) at either step simply means
    the refresh doesn't complete this round -- never worse than not
    calling it at all.
    """
    home = home or Path.home()
    deadline = time.monotonic() + timeout if timeout is not None else None

    def report(result: RefreshResult) -> RefreshResult:
        if timeout is None:
            print(result.diagnostic, file=sys.stderr, flush=True)
        return _report(result)

    try:
        if timeout is not None and timeout <= 0:
            return report(RefreshResult("timeout", detail="no refresh budget remains"))
        repo_root = _checkout_root(repo_root)
        resolution_timeout = deadline - time.monotonic() if deadline is not None else None
        if resolution_timeout is not None and resolution_timeout <= 0:
            return report(RefreshResult("timeout", detail="no refresh budget remains"))
        outcomes: list[RefreshResult] = []
        with step("Resolving projection renderer", enabled=timeout is None):
            script = _resolve_cli_script(home, timeout=resolution_timeout, outcomes=outcomes)
        if script is None:
            return report(
                outcomes[-1] if outcomes else RefreshResult(
                    "failed", detail="packaged projection engine unavailable; using checked-in fallback"
                )
            )
        argv = [
            sys.executable,
            str(script),
            "render-local-cache",
            str(repo_root),
            "--json",
            "--installed-root",
            str(home / ".copilot" / "installed-plugins"),
        ]
        agent_worktrees_command = _resolve_own_agent_worktrees_command()
        if agent_worktrees_command:
            argv += ["--agent-worktrees-path", agent_worktrees_command]
        render_timeout = deadline - time.monotonic() if deadline is not None else None
        if render_timeout is not None and render_timeout <= 0:
            return report(RefreshResult("timeout", detail="pre-render lookup exhausted refresh budget"))
        from . import push_timeout

        with step("Rendering instruction projections", enabled=timeout is None):
            inherited_group = (
                timeout is None and os.name != "nt"
                and os.environ.get("WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP") == "1"
            )
            result = push_timeout.run_bounded(
                argv, cwd=None, env=dict(os.environ), timeout=render_timeout,
                **({"inherit_process_group": True} if inherited_group else {}),
            )
        stderr = (getattr(result, "stderr", "") or "").strip()[:1000]
        try:
            parsed = _render_result(result.stdout or "", result.returncode)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"renderer exited {result.returncode}: {stderr}; {exc}") from exc
        if stderr:
            parsed = replace(parsed, detail=f"{parsed.detail}; stderr: {stderr}".strip("; ")[:2000])
        return report(parsed)
    except subprocess.TimeoutExpired:
        return report(RefreshResult("timeout", detail="local projection render timed out"))
    except Exception as exc:
        return report(RefreshResult("failed", detail=f"local projection refresh failed: {exc}"))


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
    """Queue a bounded, survivable backup without waiting inside the hook.

    Primary creation/launch preparation still refreshes synchronously. A queued
    backup is not evidence of delivery or model admission: selectors keep using
    authenticated existing caches or reviewed fallbacks until publication.
    """
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
