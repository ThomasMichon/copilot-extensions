"""Qualify whole-suite replacement on a disposable hosted Windows machine."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

from plugin_test_containment import no_window_kwargs
from agent_procutil import contained_test_mode
from agent_procutil.owned_process import run_installation_owned

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "worktree-manager" / "tests" / "native" / "test_manager_suite_replacement.py"


def _run(argv: list[str], *, timeout: float) -> int:
    result = run_installation_owned(
        argv, timeout=timeout, max_stdout_bytes=4 * 1024 * 1024,
        max_stderr_bytes=4 * 1024 * 1024,
    )
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", file=sys.stderr, flush=True)
    return result.returncode


@contextmanager
def _observe_processes(root: Path, mode: str, script):
    ready = root / f"{mode}-process-trace.ready"
    stop = root / f"{mode}-process-trace.stop"
    report = root / f"{mode}-process-trace.json"
    command = [
        shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command",
        script(root / mode / "h", ready, stop, report),
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


def qualify(root: Path, mode: str, python: Path) -> int:
    """Keep cleanup independent of success, assertion failure and stage timeout."""
    report: dict = {"mode": mode, "stages": {}, "errors": []}
    previous_data = os.environ.get("MANAGER_SUITE_ACCEPTANCE_DATA")
    try:
        for stage in ("prepare", "exercise"):
            print(f"Manager suite {mode}: {stage}", flush=True)
            code = _run(
                [str(python), "-I", "-X", "utf8", "-B", str(FIXTURE), "--" + stage, str(root), "--mode", mode],
                timeout=1800 if stage == "prepare" else 900,
            )
            report["stages"][stage] = code
            if code:
                break
        if all(report["stages"].get(stage) == 0 for stage in ("prepare", "exercise")):
            os.environ["MANAGER_SUITE_ACCEPTANCE_DATA"] = str(root)
            report["stages"]["assertions"] = _run([
                sys.executable, str(REPO / "tools" / "run-plugin-tests.py"), "worktree-manager",
                "--test-file", "tests/native/test_manager_suite_replacement.py", "-k", mode,
            ], timeout=300)
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        if previous_data is None:
            os.environ.pop("MANAGER_SUITE_ACCEPTANCE_DATA", None)
        else:
            os.environ["MANAGER_SUITE_ACCEPTANCE_DATA"] = previous_data
        try:
            report["stages"]["settle"] = _run([
                str(python), "-I", "-X", "utf8", "-B", str(FIXTURE), "--settle", str(root), "--mode", mode,
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=("normal", "repair"), required=True)
    args = parser.parse_args()
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
    python = REPO / ".test-venvs" / sys.platform / "worktree-manager" / "Scripts" / "python.exe"
    root.mkdir(parents=True, exist_ok=True)
    with _observe_processes(root, args.mode, fixture["_runtime_process_trace_script"]):
        return qualify(root, args.mode, python)


if __name__ == "__main__":
    raise SystemExit(main())
