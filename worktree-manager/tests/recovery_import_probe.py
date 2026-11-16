"""Finite source-recovery import probe; all execution state belongs to its fixture."""

from __future__ import annotations

from contextlib import AbstractContextManager
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import traceback
from typing import Any, Callable, cast


class ControlledSourceFailure(RuntimeError):
    pass


def fixture_application(*arguments: Any) -> int:
    rows, report = arguments[-2:]
    control = rows["fixture"]
    report["stage"] = "fixture-application"
    if "module" in control:
        report["tool_value"] = importlib.import_module(control["module"]).VALUE
    code = int(control["code"])
    if code < 0:
        raise ControlledSourceFailure("controlled source fixture failure")
    return code


def recovery_child() -> int:
    plan = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    toolkit = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(toolkit / "tools"))
    recovery = importlib.import_module("recover_source")
    recovery.source_modules(toolkit)
    driver = importlib.import_module("update_driver.core")
    # The real protocol still authenticates this private fixture root.
    setattr(driver, "default_root", lambda: Path(plan["workspace"]).parent)
    setattr(recovery, "apply", fixture_application)
    return int(recovery.main(sys.argv[1:]))


def recovery_parent(recovery: Any, toolkit: Path, queue: Path, code: str) -> int:
    driver = importlib.import_module("update_driver.core")
    setattr(driver, "default_root", lambda: queue)
    sources = importlib.import_module("worktree_manager.runtime_source")
    configuration = importlib.import_module("worktree_manager.runtime_source_config")
    selected = configuration.RuntimeSource("exact", repo=str(toolkit), ref="a" * 40)
    resolved = sources.ResolvedSource(selected, toolkit, "a" * 40, "b" * 40, "c" * 40)
    workspace = queue / "inputs"
    workspace.mkdir()
    report: dict[str, Any] = {
        "schema": recovery.RESULT_SCHEMA, "version": 1, "cleanup_authorized": False,
        "selection": {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
        "manager_root": str(workspace / "manager"), "effects": [],
    }
    control: dict[str, Any] = {"code": 0 if code == "tool" else int(code)}
    if code == "tool":
        control["module"] = "recovery_tool_fixture"
    recovery.__file__ = str(Path(__file__))
    caught = None
    caught_stderr = None
    try:
        recovery.admitted_apply(
            {"project": None, "operation": "install"}, resolved, workspace, toolkit,
            {"fixture": control}, report,
        )
    except Exception as error:
        caught = str(error)
        stderr = getattr(error, "stderr", None)
        caught_stderr = (
            stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes)
            else str(stderr) if stderr is not None else None
        )
    requests = driver.status(root=queue)
    print(json.dumps({
        "state": requests[-1].state if requests else "unadmitted",
        "exit_code": requests[-1].exit_code if requests else None,
        "request_count": len(requests), "report": report, "caught": caught,
        "caught_stderr": caught_stderr,
    }), flush=True)
    return 0


def main() -> int:
    if sys.argv[1:2] == ["--execute-plan"]:
        return recovery_child()
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

    if mode == "tooling-only":
        assert importlib.util.find_spec("packaging") is None
        assert importlib.util.find_spec("yaml") is None
        tools = recover_source.prepare_tool_dependencies(queue)
        sys.path.append(str(tools))
        names = (
            "local_install_profile", "local_install_source", "build_python_artifacts",
            "standalone_python_artifacts", "runtime_profile", "runtime_profile_wheels",
            "agent_worktrees.__main__", "worktree_manager.update_bootstrap",
        )
        for name in names:
            importlib.import_module(name)
        print(json.dumps({"loaded": names, "isolated": sys.flags.isolated, "no_site": sys.flags.no_site}))
        return 0
    if mode == "recovery-parent":
        return recovery_parent(recover_source, toolkit, queue, code_text)
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
        if sys.argv[1:2] == ["tooling-only"]:
            traceback.print_exc()
            stderr = getattr(error, "stderr", None)
            if stderr:
                print(stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else stderr,
                      file=sys.stderr)
        detail = (
            "repository process utilities refused"
            if "repository process utilities" in str(error) else "probe failed"
        )
        print(json.dumps({"error_type": type(error).__name__, "error": detail}), flush=True)
        raise SystemExit(17 if isinstance(error, ControlledSourceFailure) else 1)
