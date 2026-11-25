"""Qualify whole-suite replacement on a disposable hosted Windows machine."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
from typing import TypedDict

# This owner bootstraps the repository's process library on a fresh machine.
importlib.import_module("plugin_test_containment")
from agent_procutil import contained_test_mode, no_window_kwargs
from agent_procutil.owned_process import run_installation_owned

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "worktree-manager" / "tests" / "native" / "test_manager_suite_replacement.py"
FRESH_FIXTURE = FIXTURE.with_name("manager_installed_suite_fixture.py")


class _SuiteReport(TypedDict):
    mode: str
    stages: dict[str, int]
    errors: list[str]


def _run(argv: list[str], *, timeout: float) -> int:
    result = run_installation_owned(
        argv, timeout=timeout, max_stdout_bytes=4 * 1024 * 1024,
        max_stderr_bytes=4 * 1024 * 1024,
    )
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", file=sys.stderr, flush=True)
    return result.returncode


@contextmanager
def _observe_processes(
    root: Path, mode: str, script: Callable[[Path, Path, Path, Path], str],
    *, home: Path | None = None,
) -> Iterator[None]:
    ready = root / f"{mode}-process-trace.ready"
    stop = root / f"{mode}-process-trace.stop"
    report = root / f"{mode}-process-trace.json"
    executable = shutil.which("pwsh")
    if executable is None:
        raise FileNotFoundError("The native process observer requires PowerShell 7")
    command = [
        executable, "-NoProfile", "-NonInteractive", "-Command",
        script(home or root / mode / "h", ready, stop, report),
    ]
    with ThreadPoolExecutor(max_workers=1) as observer:
        future = observer.submit(_run, command, timeout=3320)
        try:
            deadline = time.monotonic() + 15
            while not ready.exists():
                if future.done():
                    raise RuntimeError(f"Native process observer exited before readiness: {future.result()}")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Native process observer did not become ready")
                time.sleep(0.05)
            yield
        finally:
            stop.write_text("stop", encoding="utf-8")
            code = future.result()
            if code:
                raise RuntimeError(f"Native process observer failed: {code}")
            if not report.is_file():
                raise RuntimeError("Native process observer did not retain its report")


def qualify(
    root: Path, mode: str, python: Path, *, main_commit: str | None = None,
    dev_commit: str | None = None, execution_root: Path | None = None,
) -> int:
    """Keep cleanup independent of success, assertion failure and stage timeout."""
    report: _SuiteReport = {"mode": mode, "stages": {}, "errors": []}
    previous_data = os.environ.get("MANAGER_SUITE_ACCEPTANCE_DATA")
    fixture = (
        FIXTURE.with_name("test_manager_source_replacement.py") if mode == "source"
        else FRESH_FIXTURE if mode == "fresh" else FIXTURE
    )
    workspace = execution_root or root
    try:
        for stage in ("prepare", "exercise"):
            print(f"Manager suite {mode}: {stage}", flush=True)
            source = (
                ["--main-commit", str(main_commit), "--dev-commit", str(dev_commit)]
                if mode == "source" and stage == "prepare" else []
            )
            code = _run(
                [str(python), "-I", "-X", "utf8", "-B", str(fixture), "--" + stage, str(workspace),
                 "--mode", mode, *source],
                timeout=1800 if stage == "prepare" else 900,
            )
            report["stages"][stage] = code
            if code:
                break
        if all(report["stages"].get(stage) == 0 for stage in ("prepare", "exercise")):
            os.environ["MANAGER_SUITE_ACCEPTANCE_DATA"] = str(workspace)
            assertions = [
                sys.executable, str(REPO / "tools" / "run-plugin-tests.py"), "worktree-manager",
                *(["--junit-dir", str(root / "junit")] if mode == "source" else []),
                "--test-file", "tests/native/" + fixture.name, "-k",
                "test_installed_manager_updates_frozen_source" if mode == "source" else mode,
            ]
            if mode == "fresh":
                assertions = [
                    str(python), "-I", "-X", "utf8", "-B", str(fixture),
                    "--assertions", str(root), "--mode", mode,
                ]
            report["stages"]["assertions"] = _run(assertions, timeout=300)
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        if previous_data is None:
            os.environ.pop("MANAGER_SUITE_ACCEPTANCE_DATA", None)
        else:
            os.environ["MANAGER_SUITE_ACCEPTANCE_DATA"] = previous_data
        try:
            report["stages"]["settle"] = _run([
                str(python), "-I", "-X", "utf8", "-B", str(fixture), "--settle", str(workspace), "--mode", mode,
            ], timeout=90)
        except Exception as error:
            report["errors"].append(f"settlement: {type(error).__name__}: {error}")
        (root / f"{mode}-runner.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return int(
        bool(report["errors"])
        or set(report["stages"]) != {"prepare", "exercise", "assertions", "settle"}
        or any(report["stages"].values())
    )


def _qualify_source(
    root: Path, python: Path, main: str, dev: str,
    script: Callable[[Path, Path, Path, Path], str],
) -> int:
    workspace = Path(tempfile.mkdtemp(prefix="ce-ms-", dir=Path.home()))
    try:
        with _observe_processes(root, "source", script, home=workspace / "source" / "h"):
            return qualify(
                root, "source", python, main_commit=main, dev_commit=dev, execution_root=workspace,
            )
    finally:
        source = workspace / "source"
        retained = root / "source"
        retained.mkdir(exist_ok=True)
        report_path = root / "source-runner.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        try:
            for name in (
                "setup.json", "result.json", "preparation-error.json", "settlement.json",
                "commands.jsonl", "task-inventory.json", "driver-diagnostics.json",
            ):
                if (source / name).is_file():
                    shutil.copy2(source / name, retained / name)
            for name in ("driver-logs", "resident-logs", "frontdoor-traces"):
                if (source / name).is_dir():
                    shutil.copytree(source / name, retained / name)
            if report.get("stages", {}).get("settle") == 0:
                temporary = importlib.import_module("native_test_paths").NativeTemporaryDirectory
                temporary._rmtree(str(workspace))
            else:
                report["retained_private_root"] = str(workspace)
                report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except Exception as error:
            report["retained_private_root"] = str(workspace)
            report.setdefault("errors", []).append(f"source evidence/cleanup: {type(error).__name__}: {error}")
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=("normal", "repair", "fresh", "source"), required=True)
    parser.add_argument("--main-commit")
    parser.add_argument("--dev-commit")
    args = parser.parse_args()
    if args.mode == "source":
        if not args.main_commit or not args.dev_commit:
            parser.error("source mode requires --main-commit and --dev-commit")
    elif args.main_commit is not None or args.dev_commit is not None:
        parser.error("source commit inputs require source mode")
    fixture = runpy.run_path(str(FIXTURE.with_name("manager_suite_fixture.py")))
    fixture["require_venue"]()
    if contained_test_mode():
        raise RuntimeError("The disposable VM must own native task and daemon lifetime")
    root = args.root.resolve()
    if root == REPO or not root.is_relative_to(REPO) or (root / args.mode).exists():
        raise ValueError("Acceptance needs a fresh explicit directory below this checkout")
    prepared = subprocess.run(
        [sys.executable, str(REPO / "tools" / "run-plugin-tests.py"), "worktree-manager", "--prepare-only"],
        cwd=REPO, check=False, timeout=300, **no_window_kwargs(),
    )
    if prepared.returncode:
        return prepared.returncode
    venv_root: Callable[[Path], Path] = importlib.import_module("plugin_test_resources").venv_root
    python = venv_root(REPO) / "worktree-manager" / "Scripts" / "python.exe"
    root.mkdir(parents=True, exist_ok=True)
    if args.mode == "source":
        main_commit, dev_commit = args.main_commit, args.dev_commit
        assert main_commit is not None and dev_commit is not None
        return _qualify_source(
            root, python, main_commit, dev_commit, fixture["_runtime_process_trace_script"],
        )
    with _observe_processes(root, args.mode, fixture["_runtime_process_trace_script"]):
        return qualify(root, args.mode, python, main_commit=args.main_commit, dev_commit=args.dev_commit)


if __name__ == "__main__":
    raise SystemExit(main())
