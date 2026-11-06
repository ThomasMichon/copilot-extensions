"""Cold Manager acceptance under independently contained native group boundaries.

Prepare real installed candidates before pytest's unchanged 30-second item
checks, then execute those checks through the canonical plugin runner.
The application-adapter contract instead times application preparation and
every consumer as separate 30-second items inside one 300-second group.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from pathlib import Path

from plugin_test_containment import Limits, TestSandbox, isolated_environment, no_window_kwargs, run_contained
from _admission_protocol import acquire
from plugin_test_resources import record_owner, run_prefix, runs_root, venv_root

REPO = Path(__file__).resolve().parents[1]
APPLICATION_ADAPTER_VARIANTS = ("plugin", "standalone")
APPLICATION_ADAPTER_PHASES = (
    "inputs", "prepare", "selection", "install", "repeat", "refusal", "fail-start", "retry", "command",
)


def _application_adapter_nodes() -> list[str]:
    return [
        "tools/test_application_adapter_journey.py::TestApplicationAdapterJourney::"
        f"test_{phase.replace('-', '_')}[{variant}]"
        for variant in APPLICATION_ADAPTER_VARIANTS
        for phase in APPLICATION_ADAPTER_PHASES
    ]


def _application_adapter_complete(report: Path) -> bool:
    if not report.is_file() or report.stat().st_size > 4 << 20:
        return False
    expected = Counter(node.split("::")[-1] for node in _application_adapter_nodes())
    try:
        cases = list(ET.parse(report).getroot().iter("testcase"))
    except (OSError, ET.ParseError):
        return False
    return (
        Counter(case.get("name") for case in cases) == expected
        and all(
            case.get("classname", "").split(".")[-1] == "TestApplicationAdapterJourney"
            and all(case.find(outcome) is None for outcome in ("failure", "error", "skipped"))
            for case in cases
        )
    )


def _application_adapter_acceptance(root: Path, venv_root: Path) -> int:
    """One contained 300-second group; every application phase remains a 30-second item."""
    started = time.monotonic()
    if any((root / name).exists() or (root / name).is_symlink() for name in (
        "application-adapter.xml", "application-adapter-result.json",
    )):
        raise ValueError("application-adapter requires fresh acceptance reports")
    python = venv_root / "agent-worktrees" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    if not python.is_file():
        raise ValueError("application-adapter requires the already-prepared agent-worktrees test environment")
    result = 1
    evidence = {"complete": False, "allocation": "phase-30/group-300", "required_nodes": _application_adapter_nodes()}
    try:
        with ExitStack() as ownership:
            lease = acquire(0, python.parent.parent)
            ownership.callback(lease.release)
            # Report-directory nesting must not lengthen native repair-slot cwd.
            sandbox = Path(ownership.enter_context(TestSandbox(prefix="adapter-", dir=REPO)))
            env = isolated_environment(os.environ, sandbox, allow_explicit_tiers=True)
            env["PYTHONPATH"] = str(REPO / "tools")
            env.pop("PYTEST_ADDOPTS", None)
            report = sandbox / "application-adapter.xml"
            remaining = 300 - (time.monotonic() - started)
            if remaining <= 0:
                result = 124
            else:
                result = run_contained(
                    [
                        str(python), "-m", "pytest", "-q", "-x", "-p", "pytest_portfolio_guard",
                        "--timeout=30", "-o", "timeout_func_only=false", "-o", "addopts=",
                        f"--basetemp={sandbox / 't'}", f"--junitxml={report}",
                        *_application_adapter_nodes(),
                    ],
                    cwd=REPO, env=env, sandbox=sandbox, limits=Limits(wall_seconds=remaining),
                )
            full_packet = result == 0 and _application_adapter_complete(report)
            if report.is_file():
                shutil.copyfile(report, root / "application-adapter.xml")
        # A failed cleanup never reaches this assignment.
        evidence["complete"] = full_packet
        if result == 0 and not full_packet:
            result = 1
    except Exception as error:
        evidence["error"] = f"{type(error).__name__}: {error}"
        result = 1
    finally:
        evidence["elapsed_seconds"] = time.monotonic() - started
        if evidence["elapsed_seconds"] > 300:
            result, evidence["complete"] = 124, False
        evidence["returncode"] = result
        (root / "application-adapter-result.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8",
        )
    return result


def _engine_routing(root: Path, venv_root: Path, *, prepare_only: bool = False) -> int:
    """Keep installed-artifact preparation outside unchanged routing timers."""
    namespace = runpy.run_path(str(REPO / "tools" / "run-plugin-tests.py"))
    runner = namespace["main"]
    runner.__globals__["VENV_ROOT"] = venv_root
    install = runner.__globals__["run_uv_install"]

    def diagnostics(*args, **kwargs):
        try:
            return install(*args, **kwargs)
        except subprocess.CalledProcessError as error:
            print("Preparation stdout:", error.stdout or "", flush=True)
            print("Preparation stderr:", error.stderr or "", flush=True)
            raise

    runner.__globals__["run_uv_install"] = diagnostics
    result = runner(["agent-worktrees", "--prepare-only"])
    if result:
        return result
    python = venv_root / "agent-worktrees" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    fixture = REPO / "plugins" / "agent-worktrees" / "tests" / "fixtures" / "update_driver_context.py"
    with TestSandbox(prefix="routing-acceptance-", dir=root) as raw:
        sandbox = Path(raw)
        env = isolated_environment(os.environ, sandbox)
        env["UPDATE_DRIVER_TEST_PHASE_LOG"] = str(root / "routing-preparation-phases.jsonl")
        env["UPDATE_DRIVER_TEST_PREPARATION_LOG"] = str(root / "routing-preparation")
        if prepare_only:
            env["UPDATE_DRIVER_TEST_INSTALLED_PROBE_DIAGNOSTICS"] = str(root / "installed-child-probe.log")
        lease = acquire(0, python.parent.parent)
        try:
            # The existing native group supervisor owns the preparation bound.
            result = run_contained(
                [str(python), "-I", "-B", str(fixture), "prepare", str(sandbox)],
                cwd=REPO, env=env, sandbox=sandbox, limits=Limits(wall_seconds=300),
            )
        finally:
            lease.release()
        if result:
            for name in ("stdout", "stderr"):
                print((root / f"routing-preparation.{name}.log").read_text(
                    encoding="utf-8", errors="replace",
                ))
            return result
        prepared = sandbox / "prepared-runtime.json"
        (root / "routing-prepared-identities.json").write_text(
            prepared.read_text(encoding="utf-8"), encoding="utf-8",
        )
        if prepare_only:
            print("Healthy native candidates prepared; timed routing was not started.", flush=True)
            return 0
        # The canonical runner acquires its environment lease for timed execution.
        with ExitStack() as restored:
            for key, value in {
                "UPDATE_DRIVER_TEST_RUNTIME_MODE": "prepared",
                "UPDATE_DRIVER_TEST_PREPARED_RUNTIME": str(prepared),
            }.items():
                previous = os.environ.get(key)
                restored.callback(
                    lambda key=key, previous=previous: (
                        os.environ.pop(key, None) if previous is None
                        else os.environ.__setitem__(key, previous)
                    ),
                )
                os.environ[key] = value
            result = runner([
                "agent-worktrees", "--test-file", "tests/test_update_driver.py",
                "-k", "actual_queued_engine_keeps", "--timeout", "600",
                "--plugin-timeout", "900", "--test-timeout", "240",
            ])
        (root / "routing-result.json").write_text(json.dumps({
            "prepared": True, "timed_runner_exit": result,
            "worker_timeout": 20, "watchdog_timeout": 60,
            "cold_first_touch_proved": False,
        }, indent=2), encoding="utf-8")
        return result


def _provider_acceptance(root: Path) -> int:
    namespace = runpy.run_path(str(REPO / "tools" / "run-plugin-tests.py"))
    requirements = namespace["declared_test_tooling_requirements"]("worktree-manager")
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("Manager provider acceptance requires uv")
    fixture = REPO / "worktree-manager" / "tests" / "native" / "test_manager_input_provider.py"
    started = time.monotonic()
    with TestSandbox(prefix="mi-") as raw:
        sandbox = Path(raw)
        tooling = sandbox / "tooling"
        python = tooling / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = isolated_environment(os.environ, sandbox, allow_explicit_tiers=True)
        env["MANAGER_INPUT_PROVIDER_DATA"] = str(sandbox / "fixtures.json")
        env["PYTHONPATH"] = str(REPO / "tools")
        commands = [
            [uv, "venv", "--python", sys.executable, str(tooling)],
            [sys.executable, "-I", "-B", str(REPO / "tools" / "seasoned_uv.py"),
             "--uv", uv, "--registry-only", "--", "--python", str(python), *requirements],
            [str(python), "-I", "-B", str(fixture), "--prepare", str(sandbox)],
            [str(python), "-m", "pytest", "-q", "-p", "pytest_portfolio_guard",
             "--timeout=30", f"--basetemp={sandbox / 'assertions'}", str(fixture)],
        ]
        for command in commands:
            remaining = 300 - (time.monotonic() - started)
            if remaining <= 0:
                return 124
            result = run_contained(
                command, cwd=REPO, env=env, sandbox=sandbox,
                limits=Limits(wall_seconds=remaining),
            )
            if result:
                return result
        (root / "provider-fixtures.json").write_text(
            (sandbox / "fixtures.json").read_text(encoding="utf-8"), encoding="utf-8",
        )
        data = json.loads((sandbox / "fixtures.json").read_text(encoding="utf-8"))
        artifacts = root / "artifacts"
        artifacts.mkdir()
        shutil.copyfile(
            Path(data["provider_wheel"]), artifacts / Path(data["provider_wheel"]).name,
        )
        shutil.copytree(
            Path(data["complete_snapshot"]), artifacts / "complete-snapshot", symlinks=True,
        )
    return 0 if time.monotonic() - started <= 300 else 124


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--main-seed", help="exact official Main commit for composed fixture inputs")
    parser.add_argument(
        "--contract", choices=("runtime", "callbacks", "updater", "source", "engine-source", "uninstall", "engine-routing", "provider", "application-adapter"),
        default="runtime",
    )
    parser.add_argument("--venv-root", type=Path, default=REPO / ".test-venvs" / sys.platform)
    parser.add_argument("--prepare-only", action="store_true", help="engine-routing preparation diagnostic only")
    args = parser.parse_args()
    if args.prepare_only and args.contract != "engine-routing":
        parser.error("--prepare-only requires --contract engine-routing")
    if args.main_seed is not None and (
        args.contract not in {"runtime", "callbacks", "source"}
        or re.fullmatch(r"[0-9a-f]{40}", args.main_seed) is None
    ):
        parser.error("--main-seed requires a full commit and a source-composing contract")
    root = args.root.resolve()
    if root == REPO or not root.is_relative_to(REPO):
        raise ValueError("Acceptance storage must be an explicit directory below this checkout")
    root.mkdir(parents=True, exist_ok=True)
    if args.contract == "engine-routing":
        return _engine_routing(root, args.venv_root.resolve(), prepare_only=args.prepare_only)
    if args.contract == "provider":
        return _provider_acceptance(root)
    if args.contract == "application-adapter":
        return _application_adapter_acceptance(root, args.venv_root.resolve())
    prepared = subprocess.run(
        [sys.executable, str(REPO / "tools" / "run-plugin-tests.py"), "worktree-manager", "--prepare-only"],
        cwd=REPO, check=False, **no_window_kwargs(),
    )
    if prepared.returncode:
        return prepared.returncode
    python = venv_root(REPO) / "worktree-manager" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    filename = {
        "runtime": "test_manager_runtime_install.py",
        "callbacks": "test_manager_mux_callbacks.py",
        "updater": "test_manager_updater_recovery.py",
        "source": "test_manager_source_install.py",
        "engine-source": "test_manager_engine_source.py",
        "uninstall": "test_manager_runtime_uninstall.py",
    }[args.contract]
    native = REPO / "worktree-manager" / "tests" / "native" / filename
    with ExitStack() as ownership:
        lease = acquire(0, python.parent.parent)
        ownership.callback(lease.release)
        raw = ownership.enter_context(TestSandbox(
            prefix=run_prefix("manager"), dir=runs_root(REPO),
        ))
        sandbox = Path(raw)
        record_owner(sandbox, REPO, retention="disposable after verified process cleanup")
        env = isolated_environment(os.environ, sandbox)
        if args.main_seed is not None:
            env["MANAGER_RUNTIME_ACCEPTANCE_MAIN_SEED"] = args.main_seed
        env["MANAGER_RUNTIME_ACCEPTANCE_DATA"] = str(sandbox / "fixtures.json")
        prepare_command = [str(python), "-I", "-B", str(native), "--prepare", str(sandbox)]
        assertions = [sys.executable, str(REPO / "tools" / "run-plugin-tests.py"), "worktree-manager",
                      "--test-file", "tests/native/" + filename]
        groups = (
            [("source constructions and assertions", [
                *[[*prepare_command, "--prepare-step", step]
                  for step in ("release", "qualified", "source", "repair")],
                assertions,
            ])]
            if args.contract == "source" else
            [("archived-fixture preparation", [prepare_command]),
             ("normal-update consumer", [
                 [str(python), "-I", "-B", str(native), "--exercise", str(sandbox)],
             ]),
             ("recovery admission and concurrent caller", [
                 [str(python), "-I", "-B", str(native), "--safety", str(sandbox)], assertions,
             ])]
            if args.contract == "updater" else
            [("installed Manager preparation", [prepare_command]),
             ("removal, reinstall and assertions", [
                 [str(python), "-I", "-B", str(native), "--exercise", str(sandbox)], assertions,
             ])]
            if args.contract == "uninstall" else
            [("construction and assertions", [prepare_command, assertions])]
        )
        group_seconds = 1800 if args.contract == "source" else 300
        for label, commands in groups:
            started = time.monotonic()
            for command in commands:
                remaining = group_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    return 124
                print(f"Manager native acceptance ({label}): running", flush=True)
                result = run_contained(
                    command, cwd=REPO, env=env, sandbox=sandbox,
                    limits=Limits(wall_seconds=remaining),
                )
                if result:
                    return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
