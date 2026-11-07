"""Carry the runner's verified native mux scope into fresh fixture interpreters."""

import json
import os
import subprocess
from pathlib import Path

from plugin_test_command_safety import (
    SANDBOX_ENV, install_command_guard, private_mux_args, validate_private_mux_args,
)

_NAMESPACE = "RELOCATION_FIXTURE_MUX_ARGS"


def _scoped_run(namespace):
    original = subprocess.run
    def run(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and argv and Path(argv[0]).stem.casefold() in {"tmux", "psmux"}:
            argv = [argv[0], *namespace, *argv[1:]]
        return original(argv, *args, **kwargs)
    return run


def scope_fixture(root: Path, monkeypatch) -> None:
    namespace = private_mux_args(root)
    monkeypatch.setattr(subprocess, "run", _scoped_run(namespace))
    monkeypatch.setenv(_NAMESPACE, json.dumps(namespace))
    bootstrap = root / "child-scope"
    bootstrap.mkdir()
    (bootstrap / "sitecustomize.py").write_text(
        "import os, sys\n"
        "try:\n"
        "    from checkout_fixture_process import install_child_scope\n"
        "    install_child_scope()\n"
        "except BaseException as exc:\n"
        "    print('fixture child scope refused: ' + str(exc), file=sys.stderr, flush=True)\n"
        "    os._exit(97)\n",
        encoding="utf-8",
    )
    existing = os.environ.get("PYTHONPATH", "")
    paths = [str(bootstrap), str(Path(__file__).parent), existing]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(value for value in paths if value))


def install_child_scope() -> None:
    sandbox = Path(os.environ[SANDBOX_ENV])
    namespace = validate_private_mux_args(
        json.loads(os.environ[_NAMESPACE]), sandbox=sandbox, cwd=Path.cwd(),
    )
    install_command_guard(sandbox)
    subprocess.run = _scoped_run(namespace)
