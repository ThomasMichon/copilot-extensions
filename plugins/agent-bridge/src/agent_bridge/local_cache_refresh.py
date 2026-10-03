"""Wire customizing-copilot's ``render-local-cache`` CLI into agent-bridge's
own **local**-spawn-path lifecycle boundary: ``session_host_connection.py``'s
``_connect_via_session_host``, right after ``resolve_local_launch`` resolves
the authoritative local ``work_dir`` and before ``spawner.spawn()`` actually
launches the Copilot CLI process.

This is the ``agent-bridge`` counterpart to
``agent_worktrees.local_cache_refresh`` (see
``docs/patterns/worktree-scoped-dynamic-guidance.md`` and
``efforts/2026/10/03 local-cache-delivery-primacy`` Phase 2): the same
"render the worktree's gitignored ``*.local.instructions.md`` siblings
before the agent reads anything" mechanism, pre-rendering a spawn-path
``agent-worktrees``/``agent-bridge`` itself drive, that `create`/`resume`/
``sessionStart`` already cover for an interactively-launched session.
``agent-bridge`` is the one remaining local-spawn path that could otherwise
hand a freshly launched agent a stale checked-in floor it has no chance to
self-correct before its first turn (unlike an interactive session, which
gets ``sessionStart``'s own backup refresh on its next resume).

Per ``docs/patterns/a-la-carte-independence.md``'s "no cross-plugin
reach-around" rule, this module never imports ``customizing-copilot``'s
Python package or assumes its internal layout beyond locating its own
declared, versioned CLI entry point (``manage-instruction-projections.py``'s
``render-local-cache`` operation) -- it invokes that payload-local script
across a process boundary (a bounded subprocess whose whole tree is killed
on timeout, never a bare ``subprocess.run(timeout=...)``, which only
terminates its direct child).

Every entry point here is deliberately best-effort and silent: customizing-
copilot not being installed, the repo not resembling a projected-instruction
root, a subprocess timeout, or any other failure are all absorbed rather
than raised. This is a convenience refresh at a lifecycle boundary, never a
gate on ``start_session`` succeeding -- a render failure must never fail a
local spawn.

Unlike ``agent_worktrees.local_cache_refresh`` (an ordinary one-shot CLI
command, free to block its own process), this module runs inside
``agent-bridge``'s own long-lived asyncio event loop, shared by every
concurrent session this daemon serves. Every subprocess call here is
therefore natively async (``asyncio.create_subprocess_exec`` +
``asyncio.wait_for``, never a synchronous call or a background thread a
timeout could only abandon, not actually stop) -- including plugin-identity
resolution, which runs in its own throwaway subprocess (this module's own
``__main__`` entry point) rather than in-process, for the same reason
``agent_worktrees.local_cache_refresh`` isolates it: ``resolve_active_
plugins()`` can spawn Git child processes verifying registered projects
with no bound of its own, and calling it in-process here would block every
other concurrent session this daemon is serving, not just this one spawn.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import Any

_SIBLING_PLUGIN_NAME = "customizing-copilot"
_SIBLING_RELATIVE_SCRIPT = (
    Path("skills")
    / "reviewing-customizations"
    / "scripts"
    / "manage-instruction-projections.py"
)

# This sits directly in the local-spawn critical path (gating `start_session`
# for up to this long before the agent process is even launched) -- a short,
# explicit bound, not this plugin's own far more generous `PhasedTimeouts`
# (sized for remote/codespace connect patience, not a local pre-spawn
# refresh). Mirrors `agent_worktrees.local_cache_refresh`'s own
# `SESSIONSTART_MAX_TIMEOUT_S` precedent for the same reason: a hook/spawn
# boundary's own deadline budget is far tighter than create/resume's. This
# is the budget each bounded subprocess's own `wait_for` gets -- NOT
# including tree-kill cleanup on a timeout (see `_CLEANUP_GRACE_S` below
# for the genuine hard ceiling on total wall time).
LOCAL_SPAWN_MAX_TIMEOUT_S = 5.0
# Worst-case wall time `_kill_tree` can add ON TOP OF a subprocess's own
# `wait_for` timeout, once per `refresh_local_cache` call (only one of the
# resolution/render subprocesses can be the one that actually times out
# and pays this in a given call). On Windows this bounds the final
# `proc.wait()` after the Job Object close already requested termination
# (the close itself is not a further wait); on POSIX it bounds `proc.
# wait()` after `os.killpg`'s `SIGKILL`, which is immediate but still
# needs the kernel to finish reaping. The genuine hard ceiling on
# `refresh_local_cache`'s own total wall time is therefore `timeout +
# _CLEANUP_GRACE_S`, never `timeout` alone -- asserted directly in
# `test_descendant_of_a_timed_out_render_does_not_survive`.
_CLEANUP_GRACE_S = 1.5
# Resolution (identity-verified plugin lookup) gets a small, fixed share of
# the overall budget; the remainder goes to the render call itself.
_RESOLUTION_TIMEOUT_S = 2.0
# The scope name resolve_active_plugins() uses for a plugin's machine-wide
# (non-project-specific) activation -- see ActivePlugin.root_for_scope().
_GLOBAL_SCOPE = "global"


def _select_global_root(home: Path) -> Path | None:
    """Return the identity-verified, global-scope-only root for
    ``_SIBLING_PLUGIN_NAME``, or ``None`` when zero or more than one active
    plugin matches -- failing closed on missing or ambiguous provenance,
    never picking one arbitrarily. Runs in-process: this function is only
    ever invoked from this module's own bounded subprocess entry point
    (``__main__`` below), never directly from the asyncio event loop -- see
    the module docstring for why.
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


async def _kill_tree(
    proc: asyncio.subprocess.Process,
    job_handle: Any | None,
    expected_group_identity: str | None,
) -> None:
    """Kill ``proc``'s whole process tree, never just the direct child --
    both the resolution and render subprocesses can spawn descendants (Git
    child processes, an ``agent-worktrees`` lookup) that a bare
    ``proc.kill()`` alone would leave running past a timeout.

    Windows: ``job_handle.close()`` (``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``)
    is the authoritative kill here, not a heuristic -- it terminates every
    process still assigned to the per-invocation kill-on-close Job Object
    (see ``_run_bounded``), independent of what state the direct child is
    in. ``job_handle`` is only ever ``None`` on Windows when Job Object
    setup itself failed (rare); ``_run_bounded`` refuses to use an
    unprotected process in that case (see its own docstring), so this
    function is never actually called with ``job_handle is None`` on
    Windows in practice -- there is deliberately no weaker fallback here
    for that path.

    POSIX: signals the whole process group directly via ``os.killpg``,
    using ``proc.pid`` itself as the pgid -- **not** a live
    ``os.getpgid(pid)`` lookup (as ``procgroup.safe_killpg`` does), which
    this exact scenario breaks: a direct child that already exited quickly
    (e.g. a descendant inherited its stdout/stderr handles and is the only
    thing still running) has often already been reaped by asyncio's own
    SIGCHLD watcher by the time this runs, making ``os.getpgid(proc.pid)``
    fail with ``ProcessLookupError`` even though the *group* (and the
    surviving descendant in it) is still very much alive. ``proc.pid`` is
    a safe stand-in for the pgid here specifically because this process
    was spawned with ``start_new_session=True`` (``_run_bounded``), which
    makes a POSIX child its own session and process-group leader at
    creation -- an invariant true regardless of whether the leader itself
    later exits or is reaped.

    This repository's PID-destruction rule requires identity verification
    before any destructive termination by numeric ID, since an exited and
    reaped PID is eventually reusable by an unrelated process. ``_run_
    bounded`` captures ``expected_group_identity`` (``zdd.diagnostics.
    process_start_time(proc.pid)``) immediately after spawn, before any
    reaping could occur -- a ``None`` *baseline* (no identity backend at
    all for this platform; ``process_start_time`` has a Linux (``/proc``)
    and a Windows backend only, so this is the universal non-Linux-POSIX
    case, not just a transient miss) fails this check closed and the
    group is never signaled. With a baseline established, this function
    re-reads the *current* occupant's start-time token for that same
    numeric ID before signaling: a live, **different** identity there
    means the PID has already been recycled, and the group signal is
    refused outright (never killpg'd). A ``None`` *current* reading (the
    common case this whole fix exists for -- the original leader already
    exited and was reaped, with no new process having claimed that exact
    number yet) is not itself evidence of a mismatch and does not block
    the signal -- only a confirmed, live, differing identity does, and
    only once a real baseline was captured to compare against. Still
    refuses to signal this call's own process group (the same safety
    check ``procgroup.safe_killpg`` makes) before ever calling
    ``os.killpg``."""
    if job_handle is not None:
        with contextlib.suppress(Exception):
            job_handle.close()
    elif sys.platform != "win32":
        with contextlib.suppress(Exception):
            import signal

            from zdd.diagnostics import process_start_time

            # `expected_group_identity is None` covers two cases that
            # must both fail closed: (a) the identity backend captured
            # nothing at spawn time, and (b) a platform with no identity
            # backend at all (`process_start_time` always returns `None`
            # on non-Linux POSIX -- it reads `/proc/<pid>/stat`). Treating
            # a missing *baseline* the same as "no mismatch" would let an
            # unverified numeric pid be signaled on exactly those hosts,
            # which this refuses outright rather than widen the "None is
            # not evidence of a mismatch" allowance from this function's
            # own docstring to cover the baseline too.
            current_identity = process_start_time(proc.pid)
            identity_established = expected_group_identity is not None
            identity_mismatch = (
                identity_established
                and current_identity is not None
                and current_identity != expected_group_identity
            )
            if (
                identity_established
                and not identity_mismatch
                and proc.pid != os.getpgid(0)
            ):
                with contextlib.suppress(
                    ProcessLookupError, PermissionError, OSError
                ):
                    os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=_CLEANUP_GRACE_S)


async def _run_bounded(
    argv: list[str], *, timeout: float, env: dict[str, str] | None = None
) -> str | None:
    """Run ``argv``, bounded by ``timeout``; kill the whole process tree
    (see ``_kill_tree``) rather than just the direct child if it runs over.
    Returns decoded stdout on a clean, in-budget, zero-exit completion --
    ``None`` on any failure, non-zero exit, or timeout.

    Windows: spawned via ``agent_procutil.spawn_in_kill_on_close_job`` --
    a per-invocation kill-on-close Job Object, closed unconditionally in
    this function's own ``finally`` (on success *and* failure, not only on
    a timeout) -- a descendant can outlive the direct child via inherited
    stdio handles even on an otherwise clean completion, and closing the
    job is the one thing that reaches it regardless. ``no_window_flags()``
    is passed alongside so this short-lived captured child never allocates
    a visible console window under agent-bridge's own windowless resident
    daemon (``docs/patterns/windows-background-process-launch.md``). Job
    arming is **mandatory**, not best-effort: if the Job Object itself
    fails to attach (``job_handle is None`` -- ``spawn_in_kill_on_close_
    job`` still resumes the process in that case, by its own contract),
    this function kills that unprotected process immediately and returns
    ``None`` rather than ever calling ``communicate()`` against it --
    never proceeding with a spawn this module's own whole-tree guarantee
    couldn't actually honor. POSIX: spawned with ``start_new_session=
    True`` so the child leads its own process group, reached via a direct,
    identity-verified ``os.killpg`` on a timeout (see ``_kill_tree``'s own
    docstring for the identity check and why not ``procgroup.
    safe_killpg``).

    ``asyncio.CancelledError`` is a ``BaseException``, not an ``Exception``
    -- a plain ``except Exception`` around the ``communicate()`` await
    would let a cancelled ``start_session`` (daemon shutdown, request
    teardown) skip tree cleanup entirely, leaving the just-spawned
    resolver/render process (and its descendants) running. This catches
    cancellation separately, shields the same cleanup so it isn't itself
    cancelled mid-kill (the pattern ``session_host/endpoints.py``'s own SSH
    probe cleanup already uses), then re-raises -- cancellation must still
    propagate, only the leak is closed.
    """
    from agent_procutil import no_window_flags, spawn_in_kill_on_close_job

    try:
        proc, job_handle = await spawn_in_kill_on_close_job(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=(sys.platform != "win32"),
            creationflags=no_window_flags(),
        )
    except Exception:
        return None
    expected_group_identity: str | None = None
    if sys.platform != "win32":
        with contextlib.suppress(Exception):
            from zdd.diagnostics import process_start_time

            # Captured immediately after spawn, before any await that
            # could let the process exit and be reaped -- this is the
            # identity token `_kill_tree` later re-verifies against.
            expected_group_identity = process_start_time(proc.pid)
    if sys.platform == "win32" and job_handle is None:
        # Job Object setup failed; the process is already running
        # unprotected (spawn_in_kill_on_close_job's own contract still
        # resumes it). Refuse to use it rather than fall back to a weaker
        # guarantee this module doesn't actually make.
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=_CLEANUP_GRACE_S)
        return None
    try:
        try:
            stdout, _stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except BaseException as exc:
            with contextlib.suppress(Exception):
                await asyncio.shield(
                    _kill_tree(proc, job_handle, expected_group_identity)
                )
            if isinstance(exc, asyncio.CancelledError):
                raise
            return None
        if proc.returncode != 0:
            return None
        return stdout.decode("utf-8", errors="replace")
    finally:
        if job_handle is not None:
            with contextlib.suppress(Exception):
                job_handle.close()


async def _resolve_cli_script(home: Path, *, timeout: float) -> Path | None:
    """Resolve customizing-copilot's ``render-local-cache`` CLI script
    through identity-verified active-plugin evidence, never a bare directory
    scan (``docs/patterns/marketplace-installation-cells.md`` -- a
    self-declared ``plugin.json`` name alone is not installation identity).
    Runs ``_select_global_root`` in its own bounded subprocess (see the
    module docstring). Returns ``None`` -- failing closed -- when
    customizing-copilot isn't resolved at the global scope (or is
    ambiguous), its script isn't present at the reported root, resolution
    itself doesn't complete within ``timeout``, or the subprocess call
    raises for any other reason."""
    try:
        output = await _run_bounded(
            [sys.executable, "-m", "agent_bridge.local_cache_refresh", str(home)],
            timeout=timeout,
            env=dict(os.environ),
        )
    except Exception:
        return None
    if not output:
        return None
    output = output.strip()
    if not output:
        return None
    script = Path(output) / _SIBLING_RELATIVE_SCRIPT
    return script if script.is_file() else None


def _resolve_same_cell_agent_worktrees_path() -> str | None:
    """When a ``COPILOT_EXTENSIONS_CONTEXT`` receipt is present (every
    namespaced marketplace cell's own runtime gate sets this --
    ``scripts/runtime-gate.sh`` -- not a rare configuration), resolve the
    same-cell ``agent-worktrees`` binstub directly: a single, concrete
    path compatible with ``--agent-worktrees-path``'s single-string
    contract, rather than the multi-token, execution-time-revalidating
    launch prefix ``session_lifecycle_cli._agent_worktrees_launch_
    prefix()`` returns for its own different purpose (invoking an
    arbitrary peer *command*, not naming a path to pass elsewhere).

    Identity is still verified here -- ``_peer_launch.validate_owner``
    performs the same receipt/certificate check the wrapper prefix's own
    first validation step does -- but only at **resolution** time, not
    re-checked again at the moment the downstream CLI actually execs the
    binstub (the wrapper prefix's own extra guarantee). This is a
    deliberate, bounded, documented trade-off: a narrow execution-time
    TOCTOU window is preferred over silently discarding the whole
    same-cell-rendering feature for every namespaced-cell install, which
    is what skipping the render entirely would do.

    ``cellRoot/plugins/agent-worktrees`` is only the peer's **durable**
    installation root (``install.json``, ``versions/``, ``state/`` --
    stable across upgrades); the actual payload (where ``bin/payload/
    agent-worktrees`` lives) is wherever that peer's own receipt
    currently points, which can be a versioned subdirectory. Resolving
    the binstub candidate directly under the durable root -- as an
    earlier revision of this function did -- is always wrong for that
    layout, so the candidate is never found and this silently degraded
    to an always-empty (safe, but non-functional) same-cell path. This
    reads the peer's own ``install.json`` receipt the same way
    ``_peer_launch.launch()`` does to get its real, current
    ``payloadRoot`` before building the candidate. Returns ``None`` on
    any resolution failure or when no receipt is present at all, never
    raising."""
    try:
        from . import _peer_launch

        explicit_context = os.environ.get(_peer_launch.CONTEXT_ENV, "")
        if not explicit_context:
            return None
        from .session_lifecycle_cli import _agent_bridge_owner_root

        own = _peer_launch.validate_owner(
            "agent-bridge", _agent_bridge_owner_root(), explicit_context
        )
        cell = Path(own["cellRoot"])
        durable = cell.parent.parent
        primitive = _peer_launch._load_primitive(
            Path(_peer_launch.__file__).with_name("_installation_context.py"),
            "_owner_installation_context",
        )
        peer_receipt = cell / "plugins" / "agent-worktrees" / "install.json"
        peer = _peer_launch._active_context(
            primitive, peer_receipt, durable, "agent-worktrees", cell, {}
        )
        peer_root = Path(peer["payloadRoot"])
        name = "agent-worktrees.cmd" if sys.platform == "win32" else "agent-worktrees"
        candidate = peer_root / "bin" / "payload" / name
        return str(candidate) if candidate.is_file() else None
    except Exception:
        return None


def _resolve_agent_worktrees_path() -> tuple[str | None, bool]:
    """Resolve the ``--agent-worktrees-path`` argument the sibling CLI
    accepts, preferring same-cell-validated resolution over a bare ambient
    ``PATH``/``~/.local/bin`` lookup (``docs/patterns/marketplace-
    installation-cells.md`` -- a plugin name alone never selects a
    runtime, and an ambient shim could belong to a *different*
    marketplace installation cell than this exact agent-bridge install).

    Returns ``(path, must_skip_render)``. ``session_lifecycle_cli.
    _agent_worktrees_launch_prefix()`` already does this same-cell
    resolution for agent-bridge's own handoff-check CLI: with no
    ``COPILOT_EXTENSIONS_CONTEXT`` receipt (a non-namespaced install) it
    degrades to the exact same ambient ``shutil.which("agent-worktrees")``
    lookup as a single-element list -- the one shape compatible with
    ``--agent-worktrees-path``'s own single-string contract, returned
    directly as ``(path, False)``. With a receipt present, it instead
    returns a multi-token, receipt-revalidating launch prefix that same
    CLI argument was never designed to carry; ``_resolve_same_cell_
    agent_worktrees_path()`` resolves the equivalent single path directly
    in that case instead. Only if *that* also fails to resolve (the
    binstub genuinely isn't at the expected same-cell location) does this
    return ``(None, True)``: skip the render entirely for this round
    rather than ever silently falling through to the downstream CLI's own
    ambient lookup, which would discard the only same-cell evidence this
    call had. A receipt-less, genuinely unresolvable lookup (ambient
    `agent-worktrees` just isn't installed) returns ``(None, False)`` --
    never worse than today's behavior, since there was no same-cell
    evidence to discard in the first place."""
    try:
        from . import _peer_launch
        from .session_lifecycle_cli import _agent_worktrees_launch_prefix

        prefix = _agent_worktrees_launch_prefix()
    except Exception:
        return None, False
    if isinstance(prefix, list) and len(prefix) == 1 and prefix[0]:
        return prefix[0], False
    if os.environ.get(_peer_launch.CONTEXT_ENV, ""):
        same_cell = _resolve_same_cell_agent_worktrees_path()
        if same_cell:
            return same_cell, False
        return None, True
    return None, False


async def refresh_local_cache(
    repo_root: str | Path,
    *,
    home: Path | None = None,
    timeout: float = LOCAL_SPAWN_MAX_TIMEOUT_S,
) -> None:
    """Best-effort refresh of every enabled source's gitignored
    ``*.local.instructions.md`` sibling under ``repo_root``, before a local
    spawn's Copilot CLI process is launched.

    Call this only for a ``target.type == "local"`` spawn whose ``target.
    cwd`` has already been confirmed to be a real, local directory --
    never for ``"command"``/``"ssh"`` targets (Codespaces, containers,
    elevated relays, and other non-local providers; see ``session_start.
    py``). ``timeout`` bounds the subprocess ``wait_for`` budget, split
    between resolving the sibling's CLI script (capped at
    ``_RESOLUTION_TIMEOUT_S``) and invoking it for whatever of ``timeout``
    remains. The genuine hard ceiling on this call's own **total** wall
    time is ``timeout + _CLEANUP_GRACE_S`` -- not ``timeout`` alone --
    since a timed-out subprocess's tree-kill cleanup (see ``_kill_tree``)
    adds that much more on top, at most once per call (only one of the two
    subprocesses can be the one that actually times out and pays it). A
    timeout, or any other failure at either step, simply means the refresh
    doesn't complete this round -- never worse than not calling it at all,
    and never raised to the caller (a render failure must never fail the
    spawn itself) EXCEPT a genuine ``asyncio.CancelledError``, which always
    propagates (see ``_run_bounded``)."""
    home = home or Path.home()
    try:
        resolution_timeout = min(timeout, _RESOLUTION_TIMEOUT_S)
        script = await _resolve_cli_script(home, timeout=resolution_timeout)
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
        agent_worktrees_command, must_skip_render = _resolve_agent_worktrees_path()
        if must_skip_render:
            # A same-cell receipt exists but couldn't be reduced to the
            # single-string shape this CLI argument accepts -- never
            # silently discard that evidence by falling through to the
            # downstream CLI's own ambient lookup (docs/patterns/
            # marketplace-installation-cells.md). Skip this round.
            return
        if agent_worktrees_command:
            argv += ["--agent-worktrees-path", agent_worktrees_command]
        await _run_bounded(argv, timeout=render_timeout, env=dict(os.environ))
    except Exception:
        pass


if __name__ == "__main__":
    # Subprocess entry point for `_resolve_cli_script` (``python -m
    # agent_bridge.local_cache_refresh <home>``): print the resolved root,
    # or an empty line when none (or ambiguously many) resolve. Deliberately
    # minimal and crash-free -- a bare `print('')` on any unexpected argv
    # shape, never a traceback a caller would need to parse out of stderr.
    _home = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home()
    _root = _select_global_root(_home)
    print(str(_root) if _root is not None else "")
