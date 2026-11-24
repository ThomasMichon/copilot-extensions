"""Same-interpreter module entry; no stream replacement or child process."""
from __future__ import annotations

import json
import os
import runpy
import sys
from collections.abc import Callable
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any, cast

from .command_errors import CommandErrorRecorder, recent_errors


def run_module(module: str, recorder: CommandErrorRecorder) -> None:
    """Preserve runpy/-m results; exclude unshaped exception values and locals."""
    # CPython's -m owner retains the real __main__ through atexit and threads.
    # Public run_module/run_path restore a temporary module on return instead.
    entry = cast(Callable[[str, bool], dict[str, Any]], getattr(runpy, "_run_module_as_main"))
    _run(lambda: entry(module, True), recorder)


def run_script(script: str, recorder: CommandErrorRecorder) -> None:
    """Execute a script without persisting its pathname or changing cwd."""
    def execute() -> None:
        filename = str(Path(script).absolute())
        namespace = sys.modules["__main__"].__dict__
        namespace.pop("main", None)  # Only the archive's imported entry alias.
        namespace.update(
            __file__=filename, __name__="__main__", __package__=None,
            __spec__=None, __cached__=None,
            __loader__=SourceFileLoader("__main__", filename),
        )
        try:
            with open(script, "rb") as source:
                data = source.read()
        except OSError as error:
            recorder.record(
                "script-open-failed", f"Script open failed (errno {error.errno})",
                phase="entry", exit_code=2,
            )
            sys.stderr.write(
                f"{sys.executable}: can't open file {filename!r}: "
                f"[Errno {error.errno}] {error.strerror}\n",
            )
            raise SystemExit(2) from None
        code = compile(data, filename, "exec", dont_inherit=True)
        exec(code, namespace)

    _run(execute, recorder)


def _run(action: Callable[[], object], recorder: CommandErrorRecorder) -> None:
    with recorder.activate():
        try:
            action()
        except SystemExit as error:
            code = error.code if type(error.code) is int else 0 if error.code is None else 1
            if code:
                recorder.record(
                    "nonzero-exit", "Application reported a nonzero result",
                    phase="completion", exit_code=code, completeness="metadata-only",
                )
            raise
        except KeyboardInterrupt:
            recorder.record(
                "interrupted", "Invocation interrupted", phase="completion",
                exit_code=130, completeness="incomplete",
            )
            raise
        except BaseException as error:
            recorder.record(
                "uncaught-exception", f"Uncaught {type(error).__name__}",
                exit_code=1, completeness="metadata-only",
            )
            raise


def main() -> None:
    """Dispatch only the packaged entry protocol; application argv stays intact."""
    if sys.argv[1:2] == ["recent"]:
        # This reader's parser never receives application arguments.
        import argparse

        parser = argparse.ArgumentParser(description="Read recent local command errors")
        parser.add_argument("--limit", type=int, default=20)
        args = parser.parse_args(sys.argv[2:])
        print(json.dumps(recent_errors(limit=args.limit), ensure_ascii=False))
        return
    if len(sys.argv) < 6 or sys.argv[1] not in {"run", "run-script"} or sys.argv[5] != "--":
        raise SystemExit("Expected: run|run-script <fixed-command> <fixed-source> <target> -- <arguments>")
    _, mode, command, source, target, _, *arguments = sys.argv
    recorder = CommandErrorRecorder(command, source)
    recorder.maintain()
    sys.argv = [target, *arguments]
    sys.modules["__main__"].__dict__.pop("main", None)
    # Match -m's search path, without leaving our archive ahead of app packages.
    if sys.path and sys.path[0].endswith(".pyz"):
        sys.path.pop(0)
    if not sys.flags.isolated and not getattr(sys.flags, "safe_path", False):
        sys.path.insert(0, os.getcwd() if mode == "run" else str(Path(target).absolute().parent))
    if mode == "run":
        run_module(target, recorder)
    else:
        run_script(target, recorder)
