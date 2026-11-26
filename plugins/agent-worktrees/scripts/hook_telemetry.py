"""Optional `hook.lifecycle.v1` journal for this plugin's sessionStart hook.

Without the owner-local admission file `<runtime>/consumer-telemetry.json` the
hook reads nothing else and writes nothing.  When it is present, the hook
records one content-free start marker before its product work and one
completion record afterwards in `<runtime>/consumer-telemetry-hooks`, through
the shared `process_telemetry.hook_journal`.  The hook never launches a
receiver: the next instrumented finite command drains the journal through its
own admitted route (consumer-telemetry-command, `hook.lifecycle.v1`).

Every function fails open; journal trouble never changes the hook's result.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

PLUGIN = "agent-worktrees"
ENTRY = "sessionStart"
POLICY_FILE = "consumer-telemetry.json"
JOURNAL_DIRECTORY = "consumer-telemetry-hooks"
_PACKAGE = "_agent_worktrees_hook_process_telemetry"


def journal_directory(runtime: Path) -> Path:
    return runtime / JOURNAL_DIRECTORY


def _payload_packages(scripts: Path) -> list[Path]:
    """The library shipped with this payload (release layout, then repository layout)."""
    plugin = scripts.parent
    return [
        plugin / "libs" / "process-telemetry" / "src" / "process_telemetry",
        plugin.parent.parent / "libs" / "process-telemetry" / "src" / "process_telemetry",
    ]


def _load_journal(packages: Iterable[Path]) -> ModuleType | None:
    loaded = sys.modules.get(f"{_PACKAGE}.hook_journal")
    if loaded is not None:
        return loaded
    for package in packages:
        if not (package / "__init__.py").is_file() or not (package / "hook_journal.py").is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                _PACKAGE, package / "__init__.py", submodule_search_locations=[str(package)],
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[_PACKAGE] = module
            spec.loader.exec_module(module)
            return importlib.import_module(f"{_PACKAGE}.hook_journal")
        except Exception:
            for name in [name for name in sys.modules if name == _PACKAGE or name.startswith(f"{_PACKAGE}.")]:
                sys.modules.pop(name, None)
    return None


def _runtime_packages(root: Path, runtime: ModuleType | None) -> list[Path]:
    """The library installed into the selected runtime's environment."""
    if runtime is None:
        return []
    python = runtime.runtime_python(root)
    if python is None:
        return []
    return [directory / "process_telemetry" for directory in runtime._site_packages(python)]


def begin(
    root: Path | None, *, runtime: ModuleType | None = None, scripts: Path | None = None,
) -> Any:
    """Start one observed run when the owner admitted the route; else None.

    `root` is the selected runtime root and `runtime` the hook's runtime
    helper, used only to find the library when the payload does not ship it.
    """
    try:
        if root is None or not (root / POLICY_FILE).is_file():
            return None
        journal = _load_journal(_payload_packages(scripts or Path(__file__).resolve().parent))
        if journal is None:
            journal = _load_journal(_runtime_packages(root, runtime))
        if journal is None:
            return None
        return journal.HookRun.begin(journal_directory(root), PLUGIN, ENTRY)
    except Exception:
        return None


def finish(run: Any, error: BaseException | None) -> None:
    """Report the run's completion; an escaped exception is `failure/exception`."""
    if run is None:
        return
    try:
        if error is None:
            run.finish(outcome="success")
        else:
            run.finish(outcome="failure", failure_category="exception")
    except Exception:
        return
