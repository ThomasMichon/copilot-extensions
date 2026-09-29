"""Scrub this plugin's own Python-interpreter-selection env vars (#4552).

PYTHONHOME/PYTHONPATH/PYTHONEXECUTABLE/VIRTUAL_ENV/UV_INTERNAL__PYTHONHOME/
__PYVENV_LAUNCHER__ select *this plugin's own* Python runtime -- the same
six-variable set already scrubbed for its own launch/version probes (see
``bin/agent-worktrees.ps1`` and ``worktree_manager.engine_client``). Left
set, they leak into a spawned child (e.g. a repo's pre-push hook exec'ing
its own python3) and redirect it onto this plugin's stdlib/venv --
version-mismatched against that interpreter's native extensions (``_sre``),
crashing ``re``'s import machinery.
"""

from __future__ import annotations

_PYTHON_RUNTIME_ENV = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONEXECUTABLE",
    "VIRTUAL_ENV",
    "UV_INTERNAL__PYTHONHOME",
    "__PYVENV_LAUNCHER__",
)


def scrub_python_runtime_env(env: dict[str, str]) -> dict[str, str]:
    """Remove this plugin's own interpreter-selection vars from *env*.

    Mutates and returns *env*. Every subprocess spawn that may itself
    resolve/exec another interpreter needs this scrub.
    """
    for name in _PYTHON_RUNTIME_ENV:
        env.pop(name, None)
    return env
