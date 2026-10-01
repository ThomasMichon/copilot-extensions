"""Wire customizing-copilot's ``render-local-cache`` CLI into the worktree
lifecycle boundaries this pattern depends on: create, resume, and
``sessionStart`` (a backup for drift accrued since).

See ``docs/patterns/worktree-scoped-dynamic-guidance.md`` and
``efforts/active/ambient-guidance-navigability`` Phase 7. This is the
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
it contains a ``plugin.json`` self-declaring the expected name. Missing or
ambiguous provenance fails closed: no script is resolved, and the refresh
is silently skipped for that round.

Every entry point here is deliberately best-effort and silent: customizing-
copilot not being installed, the repo not yet being a trusted folder, a
subprocess timeout, or any other failure are all absorbed rather than
raised. This is a convenience refresh at a lifecycle boundary, never a gate
on create/resume/sessionStart succeeding.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

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
# itself that a deadline-derived budget must also reserve.
_RUN_BOUNDED_CLEANUP_GRACE_S = 5.0
# resolve_active_plugins() verifies every agent-worktrees-registered
# project with its own pair of Git calls (each up to a 10s timeout) before
# returning -- bound how long this module waits for that verification so a
# single slow or unreachable registered project can't itself consume the
# whole refresh's budget before the render subprocess even starts.
_RESOLUTION_TIMEOUT_S = 2.0
# The scope name resolve_active_plugins() uses for a plugin's machine-wide
# (non-project-specific) activation -- see ActivePlugin.root_for_scope().
_GLOBAL_SCOPE = "global"


def _resolve_cli_script(home: Path, *, timeout: float | None = None) -> Path | None:
    """Resolve customizing-copilot's ``render-local-cache`` CLI script
    through identity-verified active-plugin evidence, never a bare
    directory scan: a self-declared ``plugin.json`` name alone is not
    installation identity (``docs/patterns/marketplace-installation-
    cells.md``), so a stale or unrelated directory must never be trusted
    to supply code this module goes on to execute.

    Only the plugin's **global** activation scope is ever trusted here --
    never a project-scoped override, which `resolve_active_plugins()`
    aggregates from every agent-worktrees-registered project and can
    otherwise supply an unrelated project's locally-overridden copy of
    customizing-copilot (a real cross-repo contamination risk: this
    refresh must only ever run the one machine-wide install, regardless
    of which repo it's invoked for). Returns ``None`` -- failing closed --
    when customizing-copilot isn't resolved as an active plugin at that
    scope, when more than one active plugin claims the name (an ambiguous
    identity resolution picking one would be unsafe to trust), its script
    isn't present at the reported root, or resolution itself doesn't
    complete within ``timeout`` (when given).
    """
    try:
        from plugin_activation import resolve_active_plugins
    except Exception:
        return None

    if timeout is None:
        try:
            report = resolve_active_plugins(home=home)
        except Exception:
            return None
    else:
        import threading

        outcome: list = [None, None]  # [report, exception]

        def _resolve() -> None:
            try:
                outcome[0] = resolve_active_plugins(home=home)
            except Exception as exc:  # noqa: BLE001 -- captured, re-raised never
                outcome[1] = exc

        thread = threading.Thread(target=_resolve, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive() or outcome[1] is not None or outcome[0] is None:
            return None
        report = outcome[0]

    candidate_roots: list[Path] = []
    for plugin in report.active.values():
        if plugin.name != _SIBLING_PLUGIN_NAME:
            continue
        root = plugin.root_for_scope(_GLOBAL_SCOPE)
        if root is not None:
            candidate_roots.append(root)
    if len(candidate_roots) != 1:
        return None
    script = candidate_roots[0] / _SIBLING_RELATIVE_SCRIPT
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
) -> None:
    """Best-effort refresh of every enabled source's gitignored
    ``*.local.instructions.md`` sibling under ``repo_root``.

    Call this at each worktree lifecycle boundary (create, resume,
    ``sessionStart``) -- never conditionally skip it on the caller's own
    error-handling grounds; let this function's own internal absorption
    handle every failure mode. ``timeout`` is the hard, enforced ceiling
    on this call's **entire** cost, split between two bounded steps:
    resolving the sibling's CLI script (capped at
    ``_RESOLUTION_TIMEOUT_S``, since ``resolve_active_plugins()`` can
    itself block on registered-project Git verification) and invoking it
    as a subprocess for whatever of ``timeout`` remains. Run via
    ``push_timeout.run_bounded`` rather than a plain ``subprocess.run(
    timeout=...)``, which only terminates its direct child -- the CLI can
    itself spawn descendants (an ``agent-worktrees`` lookup, git probes),
    which a bare ``timeout=`` would leave running past a stall.
    ``run_bounded`` kills the whole process tree instead. A timeout (or
    any other failure) at either step simply means the refresh doesn't
    complete this round -- never worse than not calling it at all.
    """
    home = home or Path.home()
    try:
        resolution_timeout = min(timeout, _RESOLUTION_TIMEOUT_S)
        script = _resolve_cli_script(home, timeout=resolution_timeout)
        if script is None:
            return
        render_timeout = timeout - resolution_timeout
        if render_timeout <= 0:
            return
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

        push_timeout.run_bounded(
            argv, cwd=None, env=dict(os.environ), timeout=render_timeout
        )
    except Exception:
        pass


def sessionstart_diagnostic(cwd: str, *, deadline: float | None) -> None:
    """``sessionStart``'s backup worktree-scoped-dynamic-guidance refresh
    (docs/patterns/worktree-scoped-dynamic-guidance.md §4) -- catches
    payload drift accrued between this worktree's own create/resume and the
    current session's start. ``create``/``resume`` already run the primary
    refresh; this is deliberately silent (no diagnostics string) since
    ``refresh_local_cache`` is itself fully best-effort and this call is a
    pure backup, not a user-facing event.

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
    ``_RUN_BOUNDED_CLEANUP_GRACE_S``, the extra wall-clock
    ``push_timeout.run_bounded`` itself can spend past its own ``timeout``
    draining a killed process's pipes -- not just the subprocess timeout
    passed to it.
    """
    try:
        if deadline is None:
            timeout = SESSIONSTART_MAX_TIMEOUT_S
        else:
            budget = deadline - time.time() - _RUN_BOUNDED_CLEANUP_GRACE_S - 1.0
            if budget < 2.0:
                return
            timeout = min(budget, SESSIONSTART_MAX_TIMEOUT_S)
        refresh_local_cache(cwd, timeout=timeout)
    except Exception:
        pass
