"""Finite source-recovery import probe; all execution state belongs to its fixture."""

from __future__ import annotations

from contextlib import AbstractContextManager
import importlib
import json
from pathlib import Path
import sys
import time
from typing import Callable, cast


class ControlledSourceFailure(RuntimeError):
    pass


def main() -> int:
    mode, toolkit_text, queue_text, code_text = sys.argv[1:]
    toolkit, queue = Path(toolkit_text), Path(queue_text)
    if mode == "parent":
        sys.path[:0] = [str(path / "src") for path in (toolkit / "libs").iterdir()]
        import agent_procutil

        assert Path(agent_procutil.__file__).resolve().is_relative_to(toolkit / "libs")
    elif mode == "preloaded-vendor":
        sys.path.insert(0, str(toolkit / "worktree-manager" / "libs" / "agent-procutil" / "src"))
        import agent_procutil

        assert "worktree-manager" in Path(agent_procutil.__file__).parts
    sys.path.insert(0, str(toolkit / "tools"))
    recover_source = importlib.import_module("recover_source")

    recover_source.source_modules(toolkit)
    source_preparation_process = importlib.import_module("source_preparation_process")

    if mode == "child":
        from update_driver import participant

        enter = cast(Callable[..., AbstractContextManager[object]], participant)
        with enter(root=queue):
            origins = {
                name: source_preparation_process.procutil_module(name).__file__
                for name in (
                    "_daemon_instance_owner", "owned_budget", "owned_process",
                    "python_cache_permissions",
                )
            }
            observed = source_preparation_process.run(
                [sys.executable, "-I", "-S", "-B", "-c", "print('source-child-ready')"],
                deadline=time.monotonic() + 30, text=True,
            )
            assert observed.returncode == 0 and observed.stdout.strip() == "source-child-ready"
            print(json.dumps({"origins": origins, "exit_code": int(code_text)}), flush=True)
            if int(code_text):
                raise ControlledSourceFailure("controlled source fixture failure")
            return 0
    if mode != "parent":
        raise AssertionError("A preloaded application utility copy was accepted")

    from update_driver import core

    request = core.run_explicit(
        core.RequestSpec("recovery-import-probe", cwd=str(queue), source="fixture"),
        runner=core.TrustedRunner(
            (sys.executable, "-I", "-S", "-B", str(Path(__file__)),
             "child", str(toolkit), str(queue), code_text),
            str(queue), timeout=60,
        ),
        root=queue,
    )
    log = request.log_path.read_text(encoding="utf-8")
    lines = [line for line in log.splitlines() if line.startswith('{"origins":')]
    errors = [json.loads(line) for line in log.splitlines() if line.startswith('{"error_type":')]
    print(json.dumps({
        "state": request.state, "exit_code": request.exit_code,
        "origin_failure": "repository process utilities refused" in log,
        "errors": errors,
        "child": json.loads(lines[-1]) if lines else None,
    }), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        detail = (
            "repository process utilities refused"
            if "repository process utilities" in str(error) else "probe failed"
        )
        print(json.dumps({"error_type": type(error).__name__, "error": detail}), flush=True)
        raise SystemExit(17 if isinstance(error, ControlledSourceFailure) else 1)
