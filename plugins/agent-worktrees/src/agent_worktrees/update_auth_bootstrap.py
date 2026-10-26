"""Minimal startup and inherited-custody facts for isolated credential helpers."""

from __future__ import annotations

import os
import sys

_GROUP = "CE_UPDATE_AUTH_PROCESS_GROUP"


def module_argv(module: str, *, python: str = sys.executable) -> list[str]:
    """Load the immutable helper closure without site hooks or checkout imports."""
    from pathlib import Path

    import agent_procutil

    if os.name == "nt" and os.path.normcase(os.path.abspath(python)) == os.path.normcase(
        os.path.abspath(sys.executable)
    ):
        # Explicit code roots do not need the selected venv's redirector.
        python = getattr(sys, "_base_executable", None) or python
    roots = list(dict.fromkeys(
        str(Path(path).resolve().parent.parent)
        for path in (__file__, agent_procutil.__file__)
    ))
    bootstrap = (
        f"import runpy,sys;sys.path.extend({roots!r});"
        "runpy.run_module(sys.argv.pop(1),run_name='__main__',alter_sys=True)"
    )
    return [python, "-I", "-S", "-B", "-X", "utf8", "-c", bootstrap, module]


def publish_group() -> None:
    if os.name != "nt" and os.environ.get("CE_UPDATE_AUTH_OWNED_SESSION") == "1":
        os.environ[_GROUP] = str(os.getpgrp())
