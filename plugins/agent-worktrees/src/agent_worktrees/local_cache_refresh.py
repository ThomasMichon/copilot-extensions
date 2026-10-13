"""Wire customizing-copilot's ``render-local-cache`` CLI into the worktree
lifecycle boundaries this pattern depends on: create, resume, and
``sessionStart`` (a backup for drift accrued since).

See ``docs/patterns/worktree-scoped-dynamic-guidance.md`` and
``efforts/2026/10/02 ambient-guidance-navigability`` Phase 7. This is the
*consumer* side of a mechanism ``customizing-copilot`` owns entirely:
``instruction_projections.render_local_cache()`` and the sibling-resolution/
declaration schema it depends on all live in that plugin's own
``skills/reviewing-customizations/scripts/``. Per
``docs/patterns/a-la-carte-independence.md``'s "no cross-plugin
reach-around" rule, this module never imports that plugin's Python package
or assumes its internal layout beyond locating its own declared,
versioned CLI entry point (``manage-instruction-projections.py``'s
``render-local-cache`` operation) -- it invokes that payload-local script
across a process boundary (a bounded-timeout subprocess).

Per ``docs/patterns/marketplace-installation-cells.md``'s "plugin name
alone never selects a runtime" invariant, the sibling's root is resolved
through ``plugin_activation.resolve_active_plugins()`` -- the same
identity-verified active-plugin evidence ``claim_providers.py`` uses for
its own sibling callbacks -- never by trusting a directory merely because
it contains a ``plugin.json`` self-declaring the expected name. Only the
plugin's **explicit current-checkout** activation scope is preferred, falling
back to global activation. Unrelated registered-project scopes are never
scanned and cannot supply another project's locally-overridden renderer.
Repository-only enablement therefore works without user-global promotion.
Missing or ambiguous provenance (zero, or more
than one, matching active plugin) fails closed: no script is resolved,
and the refresh reports unavailable for that round.

That resolution call itself runs in its own bounded subprocess (``python
-m agent_worktrees.local_cache_refresh <home> [repo_root]``, this module's own
entry point below) -- never in-process, and never on a bare Python
thread -- because identity discovery performs filesystem I/O; only a real process-tree kill
(``push_timeout.run_bounded``, the same mechanism the render step uses)
can guarantee those descendants don't outlive a timeout. A thread's own
``join(timeout)`` cannot cancel work already in flight inside it, so it
can only abandon the wait, never the underlying Git children.

Refreshes return attributable outcomes and log degradation without blocking
session startup. Renderer budget warnings are successful delivery, not a
reason to discard guidance. Explicit-checkout discovery avoids unrelated registered
project scans; both process boundaries remain timeout-contained.
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
    return refresh_local_cache(repo_root)

_SIBLING_PLUGIN_NAME = "customizing-copilot"
_SIBLING_RELATIVE_SCRIPT = (
    Path("skills")
    / "reviewing-customizations"
    / "scripts"
    / "manage-instruction-projections.py"
)

# create/resume run as ordinary one-shot CLI commands, not inside the
# long-lived resident status-monitor daemon -- a generous bound is fine.
DEFAULT_TIMEOUT_S = 120.0
# The scope name resolve_active_plugins() uses for a plugin's machine-wide
# (non-project-specific) activation -- see ActivePlugin.root_for_scope().
_GLOBAL_SCOPE = "global"


def _select_global_root(home: Path, repo_root: Path | None = None) -> Path | None:
    """Return the identity-verified current-project or global root for
    ``_SIBLING_PLUGIN_NAME``, or ``None`` when zero or more than one
    active plugin matches. Explicit checkout resolution preserves provenance
    without inspecting unrelated registered-project scopes or overrides. This function
    runs only inside the bounded resolution subprocess.
    """
    try:
        from plugin_activation import resolve_active_plugins

        report = resolve_active_plugins(
            home=home, include_projects=False, plugin_names=(_SIBLING_PLUGIN_NAME,),
            project_root=repo_root,
        )
    except Exception as exc:
        _LOG.warning("Projection renderer discovery failed: %s", exc)
        return None
    scopes = ("project:current", _GLOBAL_SCOPE) if repo_root else (_GLOBAL_SCOPE,)
    matching = [
        plugin for plugin in report.active.values()
        if plugin.name == _SIBLING_PLUGIN_NAME
        and any(plugin.root_for_scope(scope) is not None for scope in scopes)
    ]
    if len(matching) != 1:
        return None
    plugin = matching[0]
    for scope in scopes:
        root = plugin.root_for_scope(scope)
        if root is not None:
            if scope == "project:current":
                from plugin_activation import PluginStateError, inspect_plugin_state

                if any(selected.root == root and selected.kind == "directory" for selected in plugin.live_roots):
                    try:
                        proof = inspect_plugin_state(plugin.source, home=home / ".copilot", repo=repo_root)
                    except PluginStateError as exc:
                        _LOG.warning("Current renderer trust lookup failed: %s", exc)
                        return None
                    if proof.get("repositoryTrusted") is not True:
                        _LOG.warning("Current directory renderer requires explicit repository trust")
                        return None
            return root
    return None


def _resolve_cli_script(
    home: Path, *, timeout: float, outcomes: list[RefreshResult] | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    """Resolve customizing-copilot's ``render-local-cache`` CLI script
    through identity-verified active-plugin evidence, never a bare
    directory scan: a self-declared ``plugin.json`` name alone is not
    installation identity (``docs/patterns/marketplace-installation-
    cells.md``), so a stale or unrelated directory must never be trusted
    to supply code this module goes on to execute.

    Runs ``_select_global_root`` in its own bounded subprocess (see the
    module docstring for why) rather than calling it in-process. Returns
    ``None`` -- failing closed -- when customizing-copilot isn't resolved
    at the current-checkout or global scope (or is ambiguous -- see ``_select_global_root``),
    its script isn't present at the reported root, or resolution itself
    doesn't complete within ``timeout``.
    """
    try:
        from . import push_timeout

        result = push_timeout.run_bounded(
            [
                sys.executable, "-m", "agent_worktrees.local_cache_refresh", str(home),
                *([str(repo_root)] if repo_root is not None else []),
            ],
            cwd=None, env=dict(os.environ), timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        if outcomes is not None:
            outcomes.append(RefreshResult("timeout", detail="renderer identity lookup timed out"))
        return None
    except Exception as exc:
        if outcomes is not None:
            outcomes.append(RefreshResult("failed", detail=f"renderer identity lookup failed: {exc}"))
        return None
    stderr = (getattr(result, "stderr", "") or "").strip()[:1000]
    if getattr(result, "returncode", 0):
        if outcomes is not None:
            outcomes.append(RefreshResult("failed", detail=f"renderer identity lookup exited unsuccessfully: {stderr}"))
        return None
    output = (result.stdout or "").strip()
    if not output:
        if stderr and outcomes is not None:
            outcomes.append(RefreshResult("failed", detail=f"renderer identity lookup: {stderr}"))
        return None
    script = Path(output) / _SIBLING_RELATIVE_SCRIPT
    return script if script.is_file() else None


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
    timeout: float = DEFAULT_TIMEOUT_S,
) -> RefreshResult:
    """Refresh enabled sources' gitignored, auto-discovered local instructions.

    Call at create, resume, JSON launch planning and sessionStart. Results report
    actual installation; nonzero exits, invalid output and timeouts cannot
    silently become success. ``timeout`` is the hard, enforced ceiling
    on this call's **own** cost (not counting ``push_timeout.run_
    bounded``'s own five-second cleanup grace),
    shared between resolving the sibling's CLI and rendering with the actual
    remaining time. Discovery has no shorter fixed cap: a healthy cold Python
    startup must not discard guidance while the caller still has budget.
    Both run via
    ``push_timeout.run_bounded`` rather than a plain ``subprocess.run(
    timeout=...)``, which only terminates its direct child -- both the
    resolver and the CLI can spawn descendants (Git child processes, an
    ``agent-worktrees`` lookup), which a bare ``timeout=`` would leave
    running past a stall. ``run_bounded`` kills each whole process tree
    instead. A timeout (or any other failure) at either step simply means
    the refresh doesn't complete this round -- never worse than not
    calling it at all.
    """
    home = home or Path.home()
    deadline = time.monotonic() + timeout
    try:
        if timeout <= 0:
            return _report(RefreshResult("timeout", detail="no refresh budget remains"))
        repo_root = _checkout_root(repo_root)
        resolution_timeout = deadline - time.monotonic()
        if resolution_timeout <= 0:
            return _report(RefreshResult("timeout", detail="no refresh budget remains"))
        outcomes: list[RefreshResult] = []
        script = _resolve_cli_script(
            home, timeout=resolution_timeout, outcomes=outcomes, repo_root=repo_root,
        )
        if script is None:
            return _report(
                outcomes[-1] if outcomes else RefreshResult(
                    "unavailable", detail="project/global renderer missing or ambiguous; using checked-in fallback"
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
        render_timeout = deadline - time.monotonic()
        if render_timeout <= 0:
            return _report(RefreshResult("timeout", detail="pre-render lookup exhausted refresh budget"))
        from . import push_timeout

        result = push_timeout.run_bounded(
            argv, cwd=None, env=dict(os.environ), timeout=render_timeout
        )
        stderr = (getattr(result, "stderr", "") or "").strip()[:1000]
        try:
            parsed = _render_result(result.stdout or "", result.returncode)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"renderer exited {result.returncode}: {stderr}; {exc}") from exc
        if stderr:
            parsed = replace(parsed, detail=f"{parsed.detail}; stderr: {stderr}".strip("; ")[:2000])
        return _report(parsed)
    except subprocess.TimeoutExpired:
        return _report(RefreshResult("timeout", detail="local projection render timed out"))
    except Exception as exc:
        return _report(RefreshResult("failed", detail=f"local projection refresh failed: {exc}"))


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
    # Subprocess entry point for `_resolve_cli_script` (``python -m
    # agent_worktrees.local_cache_refresh <home> [repo_root]``): print the resolved
    # root, or an empty line when none (or ambiguously many) resolve.
    # Deliberately minimal and crash-free -- a bare `print('')` on any
    # unexpected argv shape, never a traceback a caller would need to
    # parse out of stderr.
    if len(sys.argv) == 3 and sys.argv[1] == "--refresh":
        raise SystemExit(_run_refresh_worker(sys.argv[2]))
    _home = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home()
    _repo_root = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    _root = _select_global_root(_home, _repo_root)
    print(str(_root) if _root is not None else "")
