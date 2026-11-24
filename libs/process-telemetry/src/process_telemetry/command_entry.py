"""Same-interpreter module entry; no stream replacement or child process."""
from __future__ import annotations

import json
import os
import runpy
import sys
from collections.abc import Callable
from pathlib import Path

from .command_errors import CommandErrorRecorder, recent_errors


def run_module(module: str, recorder: CommandErrorRecorder) -> None:
    """Preserve runpy/-m results; exclude unshaped exception values and locals."""
    _run(lambda: runpy.run_module(module, run_name="__main__", alter_sys=True), recorder)


def run_script(script: str, recorder: CommandErrorRecorder) -> None:
    """Execute a script without persisting its pathname or changing cwd."""
    _run(lambda: runpy.run_path(script, run_name="__main__"), recorder)


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
    # Match -m's search path, without leaving our archive ahead of app packages.
    if sys.path and sys.path[0].endswith(".pyz"):
        sys.path.pop(0)
    if not sys.flags.isolated and not getattr(sys.flags, "safe_path", False):
        sys.path.insert(0, os.getcwd() if mode == "run" else str(Path(target).absolute().parent))
    if mode == "run":
        run_module(target, recorder)
    else:
        run_script(target, recorder)
