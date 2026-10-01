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
across a process boundary (a bounded-timeout subprocess), exactly as
``claim_providers.py`` resolves a sibling's payload-local binstub for its
own callbacks.

Every entry point here is deliberately best-effort and silent: customizing-
copilot not being installed, the repo not yet being a trusted folder, a
subprocess timeout, or any other failure are all absorbed rather than
raised. This is a convenience refresh at a lifecycle boundary, never a gate
on create/resume/sessionStart succeeding.
"""

from __future__ import annotations

import json
import os
import subprocess
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


def _candidate_plugin_roots(home: Path) -> list[Path]:
    roots = [
        home / ".copilot" / "installed-plugins" / "copilot-extensions" / _SIBLING_PLUGIN_NAME
    ]
    direct_root = home / ".copilot" / "installed-plugins" / "_direct"
    if direct_root.is_dir():
        try:
            children = sorted(direct_root.iterdir())
        except OSError:
            children = []
        for child in children:
            manifest = child / "plugin.json"
            if not manifest.is_file():
                continue
            try:
                declared = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                continue
            if isinstance(declared, dict) and declared.get("name") == _SIBLING_PLUGIN_NAME:
                roots.append(child)
    return roots


def _resolve_cli_script(home: Path) -> Path | None:
    for root in _candidate_plugin_roots(home):
        script = root / _SIBLING_RELATIVE_SCRIPT
        if script.is_file():
            return script
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
    timeout: float = DEFAULT_TIMEOUT_S,
) -> None:
    """Best-effort refresh of every enabled source's gitignored
    ``*.local.instructions.md`` sibling under ``repo_root``.

    Call this at each worktree lifecycle boundary (create, resume,
    ``sessionStart``) -- never conditionally skip it on the caller's own
    error-handling grounds; let this function's own internal absorption
    handle every failure mode. Invokes customizing-copilot's own
    ``render-local-cache`` CLI as a subprocess, bounded by ``timeout``: a
    hard, enforced ceiling on this call's own worst-case cost, which a
    purely in-process call could not give the same guarantee for. A
    timeout (or any other failure) simply means the refresh doesn't
    complete this round -- never worse than not calling it at all.
    """
    home = home or Path.home()
    try:
        script = _resolve_cli_script(home)
        if script is None:
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
        subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            check=False,
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
    attempting.
    """
    try:
        budget = (
            SESSIONSTART_MAX_TIMEOUT_S if deadline is None else deadline - time.time() - 1.0
        )
        if budget < 2.0:
            return
        timeout = min(budget, SESSIONSTART_MAX_TIMEOUT_S)
        refresh_local_cache(cwd, timeout=timeout)
    except Exception:
        pass
