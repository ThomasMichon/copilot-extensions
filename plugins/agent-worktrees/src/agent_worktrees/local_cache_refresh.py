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
import sys
from pathlib import Path

_SIBLING_PLUGIN_NAME = "customizing-copilot"
_SIBLING_RELATIVE_SCRIPT = Path("skills") / "reviewing-customizations" / "scripts" / "instruction_projections.py"
_MODULE_NAME = "_agent_worktrees_instruction_projections"


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
        try:
            spec.loader.exec_module(module)
        except Exception:
            continue
        sys.modules[_MODULE_NAME] = module
        return module
    return None


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
        projections.render_local_cache(
            root,
            lambda: projections.discover_enabled_sources(root, require_trust=False),
        )
    except Exception:
        pass
