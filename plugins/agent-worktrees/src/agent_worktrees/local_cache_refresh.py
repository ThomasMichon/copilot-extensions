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
it contains a ``plugin.json`` self-declaring the expected name. Only the
plugin's **global** activation scope is ever trusted -- never a project-
scoped override, which the resolver aggregates from every agent-worktrees-
registered project and could otherwise supply an unrelated project's
locally-overridden copy of customizing-copilot to a session in a
different repo entirely. Missing or ambiguous provenance (zero, or more
than one, matching active plugin) fails closed: no script is resolved,
and the refresh is silently skipped for that round.

That resolution call itself runs in its own bounded subprocess (``python
-m agent_worktrees.local_cache_refresh <home>``, this module's own
entry point below) -- never in-process, and never on a bare Python
thread -- because ``resolve_active_plugins()`` can spawn Git child
processes verifying registered projects; only a real process-tree kill
(``push_timeout.run_bounded``, the same mechanism the render step uses)
can guarantee those descendants don't outlive a timeout. A thread's own
``join(timeout)`` cannot cancel work already in flight inside it, so it
can only abandon the wait, never the underlying Git children.

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
# itself that a deadline-derived budget must also reserve. refresh_local_
# cache runs TWO such bounded subprocesses in sequence (resolution, then
# render), so a deadline-derived budget must reserve this grace TWICE.
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


def _select_global_root(home: Path) -> Path | None:
    """Return the identity-verified, global-scope-only root for
    ``_SIBLING_PLUGIN_NAME``, or ``None`` when zero or more than one
    active plugin matches -- failing closed on missing or ambiguous
    provenance, never picking one arbitrarily. Runs in-process: this
    function is only ever invoked from this module's own bounded
    subprocess entry point (``__main__`` below), never directly from a
    deadline-bound caller, since ``resolve_active_plugins()`` can itself
    block on registered-project Git verification with no bound of its
    own.
    """
    try:
        from plugin_activation import resolve_active_plugins

        report = resolve_active_plugins(home=home)
    except Exception:
        return None
    candidate_roots = [
        root
        for plugin in report.active.values()
        if plugin.name == _SIBLING_PLUGIN_NAME
        for root in [plugin.root_for_scope(_GLOBAL_SCOPE)]
        if root is not None
    ]
    return candidate_roots[0] if len(candidate_roots) == 1 else None


def _resolve_cli_script(home: Path, *, timeout: float) -> Path | None:
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
    except Exception:
        return None
    output = (result.stdout or "").strip()
    if not output:
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
) -> None:
    """Best-effort refresh of every enabled source's gitignored
    ``*.local.instructions.md`` sibling under ``repo_root``.

    Call this at each worktree lifecycle boundary (create, resume,
    ``sessionStart``) -- never conditionally skip it on the caller's own
    error-handling grounds; let this function's own internal absorption
    handle every failure mode. ``timeout`` is the hard, enforced ceiling
    on this call's **own** cost (not counting ``push_timeout.run_
    bounded``'s own cleanup grace -- see ``_RUN_BOUNDED_CLEANUP_GRACE_S``),
    split between two sequential bounded subprocesses: resolving the
    sibling's CLI script (capped at ``_RESOLUTION_TIMEOUT_S``) and
    invoking it for whatever of ``timeout`` remains. Both run via
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
    ``_RUN_BOUNDED_CLEANUP_GRACE_S`` **twice** -- once for each of the two
    sequential bounded subprocesses ``refresh_local_cache`` runs
    (resolution, then render), since either one can independently spend
    that much extra wall-clock past its own ``timeout`` draining a killed
    process's pipes.
    """
    try:
        if deadline is None:
            timeout = SESSIONSTART_MAX_TIMEOUT_S
        else:
            budget = (
                deadline - time.time() - (2 * _RUN_BOUNDED_CLEANUP_GRACE_S) - 1.0
            )
            if budget < 2.0:
                return
            timeout = min(budget, SESSIONSTART_MAX_TIMEOUT_S)
        refresh_local_cache(cwd, timeout=timeout)
    except Exception:
        pass


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
