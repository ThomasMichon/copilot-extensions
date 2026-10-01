"""Wire customizing-copilot's ``render_local_cache()`` into the worktree
lifecycle boundaries this pattern depends on: create, resume, and
``sessionStart`` (a backup for drift accrued since).

See ``docs/patterns/worktree-scoped-dynamic-guidance.md`` and
``efforts/active/ambient-guidance-navigability`` Phase 7. This is the
*consumer* side of a mechanism `customizing-copilot` owns entirely --
``render_local_cache()`` itself, and the sibling-resolution/declaration
schema it depends on, all live in that plugin's own
``skills/reviewing-customizations/scripts/instruction_projections.py``.
This module only locates that module at runtime (marketplace install, with
a ``_direct``-install fallback mirroring ``update_stage.discover_plugin_dir``'s
own agent-worktrees-specific resolution, generalized to a named sibling
plugin) and calls into it.

Every entry point here is deliberately best-effort and silent: customizing-
copilot not being installed, the repo not yet being a trusted folder, or any
render failure are all absorbed rather than raised. This is a convenience
refresh at a lifecycle boundary, never a gate on create/resume/sessionStart
succeeding.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

_SIBLING_PLUGIN_NAME = "customizing-copilot"
_SIBLING_RELATIVE_SCRIPT = Path("skills") / "reviewing-customizations" / "scripts" / "instruction_projections.py"
_MODULE_NAME = "_agent_worktrees_instruction_projections"
_LOAD_LOCK = threading.Lock()


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


def _load_instruction_projections(home: Path):
    # Serialized: the sessionStart resident server handles concurrent hook
    # requests on its own threads, so two callers can reach this at once. A
    # racing second caller must never observe the module mid-``exec_module``
    # -- the pre-registration below (required so the module's own
    # postponed-annotation dataclasses can resolve `sys.modules[cls.__module__]`
    # while their class bodies run) would otherwise let it see a partially
    # initialized object and silently skip the refresh (`render_local_cache`
    # not yet defined on it).
    with _LOAD_LOCK:
        cached = sys.modules.get(_MODULE_NAME)
        if cached is not None:
            return cached
        for root in _candidate_plugin_roots(home):
            script = root / _SIBLING_RELATIVE_SCRIPT
            if not script.is_file():
                continue
            spec = importlib.util.spec_from_file_location(_MODULE_NAME, script)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            # Register in sys.modules BEFORE executing: the real shipped
            # instruction_projections.py declares postponed-annotation
            # dataclasses, whose machinery looks up
            # `sys.modules[cls.__module__]` while the class body runs --
            # executing first would leave that lookup unresolved. Remove
            # the partial entry on failure so a broken module is never left
            # cached as if it had loaded.
            sys.modules[_MODULE_NAME] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(_MODULE_NAME, None)
                continue
            return module
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
    deployed, or the payload root itself can't be resolved --
    ``discover_enabled_sources`` then falls back to its own ambient
    resolution, unchanged from before this existed.
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


def refresh_local_cache(repo_root: str | Path, *, home: Path | None = None) -> None:
    """Best-effort refresh of every enabled source's gitignored
    ``*.local.instructions.md`` sibling under ``repo_root``.

    Call this at each worktree lifecycle boundary (create, resume,
    ``sessionStart``) -- never conditionally skip it on the caller's own
    error-handling grounds; let this function's own internal absorption
    handle every failure mode.
    """
    home = home or Path.home()
    try:
        projections = _load_instruction_projections(home)
        if projections is None:
            return
        root = Path(repo_root)
        agent_worktrees_command = _resolve_own_agent_worktrees_command()
        projections.render_local_cache(
            root,
            lambda: projections.discover_enabled_sources(
                root,
                require_trust=False,
                agent_worktrees_command=agent_worktrees_command,
            ),
        )
    except Exception:
        pass
