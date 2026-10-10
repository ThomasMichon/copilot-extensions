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
plugin's **global** activation scope is ever trusted -- never a project-
scoped override, which the resolver aggregates from every agent-worktrees-
registered project and could otherwise supply an unrelated project's
locally-overridden copy of customizing-copilot to a session in a
different repo entirely. Missing or ambiguous provenance (zero, or more
than one, matching active plugin) fails closed: no script is resolved,
and the refresh reports unavailable for that round.

That resolution call itself runs in its own bounded subprocess (``python
-m agent_worktrees.local_cache_refresh <home>``, this module's own
entry point below) -- never in-process, and never on a bare Python
thread -- because identity discovery performs filesystem I/O; only a real process-tree kill
(``push_timeout.run_bounded``, the same mechanism the render step uses)
can guarantee those descendants don't outlive a timeout. A thread's own
``join(timeout)`` cannot cancel work already in flight inside it, so it
can only abandon the wait, never the underlying Git children.

Refreshes return attributable outcomes and log degradation without blocking
session startup. Renderer budget warnings are successful delivery, not a
reason to discard guidance. Global-only discovery avoids unrelated registered
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
RefreshStatus = Literal["ready", "partial", "unavailable", "failed", "timeout", "skipped"]


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
DEFAULT_TIMEOUT_S = 30.0
# sessionStart's backup runs inside the resident hook server's own request
# handling; its decision deadline is far shorter than this refresh's own
# worst case, so this bound must leave headroom for every other diagnostic
# sharing that same budget. Callers compute a tighter, deadline-derived
# timeout and pass it explicitly; this is only the floor/ceiling.
SESSIONSTART_MAX_TIMEOUT_S = 5.0
# push_timeout.run_bounded's own timeout path kills the whole process tree,
# then waits up to this much longer for the pipes to drain before giving up
# (see push_timeout.py) -- wall-clock on top of the subprocess timeout
# itself that a deadline-derived budget must also reserve. refresh_local_
# cache runs two such bounded subprocesses in sequence (resolution, then
# render), but only ever one of them can be the one that actually times
# out and pays this grace in a given call, so a deadline-derived budget
# reserves it once, not per subprocess.
_RUN_BOUNDED_CLEANUP_GRACE_S = 5.0
# Bound global identity discovery so rendering retains part of the budget.
_RESOLUTION_TIMEOUT_S = 2.0
# The scope name resolve_active_plugins() uses for a plugin's machine-wide
# (non-project-specific) activation -- see ActivePlugin.root_for_scope().
_GLOBAL_SCOPE = "global"


def _select_global_root(home: Path) -> Path | None:
    """Return the identity-verified, global-scope-only root for
    ``_SIBLING_PLUGIN_NAME``, or ``None`` when zero or more than one
    active plugin matches. Global-only resolution preserves provenance without
    inspecting unrelated registered-project scopes or overrides. This function
    runs only inside the bounded resolution subprocess.
    """
    try:
        import inspect

        from plugin_activation import resolve_active_plugins

        kwargs = {"home": home}
        if "include_projects" in inspect.signature(resolve_active_plugins).parameters:
            kwargs["include_projects"] = False
        report = resolve_active_plugins(**kwargs)
    except Exception as exc:
        _LOG.warning("Global projection renderer discovery failed: %s", exc)
        return None
    candidate_roots = [
        root
        for plugin in report.active.values()
        if plugin.name == _SIBLING_PLUGIN_NAME
        for root in [plugin.root_for_scope(_GLOBAL_SCOPE)]
        if root is not None
    ]
    return candidate_roots[0] if len(candidate_roots) == 1 else None


def _resolve_cli_script(
    home: Path, *, timeout: float, outcomes: list[RefreshResult] | None = None
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
    at the global scope (or is ambiguous -- see ``_select_global_root``),
    its script isn't present at the reported root, or resolution itself
    doesn't complete within ``timeout``.
    """
    try:
        from . import push_timeout

        result = push_timeout.run_bounded(
            [sys.executable, "-m", "agent_worktrees.local_cache_refresh", str(home)],
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
    """Best-effort refresh of every enabled source's gitignored
    ``*.local.instructions.md`` sibling under ``repo_root``.

    Call at create, resume, JSON launch planning and sessionStart. Results report
    actual installation; nonzero exits, invalid output and timeouts cannot
    silently become success. ``timeout`` is the hard, enforced ceiling
    on this call's **own** cost (not counting ``push_timeout.run_
    bounded``'s own cleanup grace -- see ``_RUN_BOUNDED_CLEANUP_GRACE_S``),
    split between resolving the sibling's CLI (capped at
    ``_RESOLUTION_TIMEOUT_S``) and rendering with the actual remaining time.
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
        resolution_timeout = min(timeout, _RESOLUTION_TIMEOUT_S)
        outcomes: list[RefreshResult] = []
        script = _resolve_cli_script(home, timeout=resolution_timeout, outcomes=outcomes)
        if script is None:
            return _report(
                outcomes[-1] if outcomes else RefreshResult(
                    "unavailable", detail="global renderer missing or ambiguous; using checked-in fallback"
                )
            )
        render_timeout = deadline - time.monotonic()
        if render_timeout <= 0:
            return _report(RefreshResult("timeout", detail="identity lookup exhausted refresh budget"))
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


def sessionstart_diagnostic(cwd: str, *, deadline: float | None) -> str:
    """``sessionStart``'s backup worktree-scoped-dynamic-guidance refresh
    (docs/patterns/worktree-scoped-dynamic-guidance.md §4) -- catches
    payload drift accrued between this worktree's own create/resume and the
    current session's start. Creation and launch planning run the primary
    refresh; this backup returns an attributable diagnostic without gating
    startup.

    Runs synchronously and in-order with the hook's other diagnostics --
    never dispatched to a background thread -- because the pattern this
    backs depends on the render having genuinely completed by the time
    this hook call returns (the catch-all instruction's own first-turn
    read is only safe *because* sessionStart has already finished). A
    background thread would race that read instead of guaranteeing it.
    Bounded by a timeout derived from the hook's own remaining budget
    (capped at ``SESSIONSTART_MAX_TIMEOUT_S``) rather than this refresh's
    own unbounded worst case, so it can never itself cause the whole
    lifecycle response to miss the resident hook server's deadline --
    skipped entirely once too little budget remains to be worth
    attempting. The reserved margin also covers
    ``_RUN_BOUNDED_CLEANUP_GRACE_S`` once: ``refresh_local_cache`` runs
    two sequential bounded subprocesses (resolution, then render), but
    only ever ONE of them can be the one that actually times out and
    pays that grace in a given call -- a timed-out resolution returns
    before the render subprocess is ever attempted, and a resolution
    that succeeds within its own share of ``timeout`` never pays the
    grace itself. The combined worst case is therefore ``timeout +
    _RUN_BOUNDED_CLEANUP_GRACE_S``, never ``timeout +
    2 * _RUN_BOUNDED_CLEANUP_GRACE_S``.
    """
    try:
        if deadline is None:
            timeout = SESSIONSTART_MAX_TIMEOUT_S
        else:
            budget = deadline - time.time() - _RUN_BOUNDED_CLEANUP_GRACE_S - 1.0
            if budget < 2.0:
                return RefreshResult("skipped", detail="backup refresh deadline too short").diagnostic + "\n"
            timeout = min(budget, SESSIONSTART_MAX_TIMEOUT_S)
        result = refresh_local_cache(cwd, timeout=timeout)
        return result.diagnostic + "\n" if result is not None else ""
    except Exception as exc:
        return _report(RefreshResult("failed", detail=f"backup refresh failed: {exc}")).diagnostic + "\n"


if __name__ == "__main__":
    # Subprocess entry point for `_resolve_cli_script` (``python -m
    # agent_worktrees.local_cache_refresh <home>``): print the resolved
    # root, or an empty line when none (or ambiguously many) resolve.
    # Deliberately minimal and crash-free -- a bare `print('')` on any
    # unexpected argv shape, never a traceback a caller would need to
    # parse out of stderr.
    _home = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home()
    _root = _select_global_root(_home)
    print(str(_root) if _root is not None else "")
