"""Host-driven Tier-P proof; all actual task operations run in fresh containers."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import runpy
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MANIFEST = Path(__file__).with_name("manifest.json")
TEST_FILE = "tests/test_restricted_supervisor_docker.py"
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
CLEANUP_RESERVE_SECONDS = 30


def resource_names(suffix: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not re.fullmatch(r"[0-9a-f]{12}", suffix):
        raise ValueError("restricted resource suffix must be twelve lowercase hexadecimal digits")
    return (
        tuple(f"restricted-proof-{part}-{suffix}" for part in ("worker", "central")),
        tuple(f"restricted-proof-{part}-{suffix}" for part in ("net", "management")),
    )


def _cleanup_docker(containers, networks, *, deadline: float | None = None):
    failures = []
    for kind, names in (("container", containers), ("network", networks)):
        for name in names:
            argv = ["docker", "rm", "-f", "-v", name] if kind == "container" else [
                "docker", "network", "rm", name,
            ]

            def call(command):
                remaining = 30 if deadline is None else min(30, deadline - time.monotonic())
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, 0)
                return subprocess.run(
                    command, capture_output=True, text=True, timeout=remaining, **NO_WINDOW,
                )

            try:
                removed = call(argv)
                inventory = ["docker", kind, "ls", "--filter", f"name=^/?{re.escape(name)}$"]
                if kind == "container":
                    inventory.append("--all")
                inventory.extend(["--format", "{{json .Names}}" if kind == "container" else "{{json .Name}}"])
                inspected = call(inventory)
                if inspected.returncode:
                    failures.append(
                        f"{kind} inventory unavailable: {name}: exit={inspected.returncode}; "
                        f"stdout={inspected.stdout!r}; stderr={inspected.stderr!r}"
                    )
                else:
                    present = [json.loads(line) for line in inspected.stdout.splitlines()]
                    if any(not isinstance(value, str) for value in present):
                        failures.append(f"{kind} inventory malformed: {name}")
                    elif name in present:
                        failures.append(
                            f"{kind} still exists: {name}: exit={removed.returncode}; "
                            f"stdout={removed.stdout!r}; stderr={removed.stderr!r}"
                        )
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                failures.append(f"{kind} cleanup failed: {name}: {exc}")
    assert not failures, "\n".join(failures)


def _run_case(argv: list[str], *, env: dict[str, str], log, timeout: float) -> int:
    execution_seconds = timeout - CLEANUP_RESERVE_SECONDS
    if execution_seconds <= 0:
        raise ValueError("case budget must include execution and reserved cleanup")
    if os.name == "nt":
        return subprocess.run(
            argv, cwd=REPO, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            timeout=execution_seconds, **NO_WINDOW,
        ).returncode
    if signal.getitimer(signal.ITIMER_REAL)[0]:
        raise ValueError("case owner cannot replace an existing deadline timer")
    expired = False

    def interrupt_owner(_signal, _frame):
        nonlocal expired
        expired = True
        raise KeyboardInterrupt

    old_handler = signal.signal(signal.SIGALRM, interrupt_owner)
    old_env, old_argv, old_path, old_cwd = dict(os.environ), sys.argv, sys.path[:], Path.cwd()
    sys.stdout.flush()
    sys.stderr.flush()
    stdout_fd, stderr_fd = os.dup(1), os.dup(2)
    try:
        os.environ.clear()
        os.environ.update(env)
        sys.argv = argv[1:]
        sys.path.insert(0, str(Path(argv[1]).parent))
        os.chdir(REPO)
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        signal.setitimer(signal.ITIMER_REAL, execution_seconds)
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            try:
                runpy.run_path(argv[1], run_name="__main__")
                result = 0
            except SystemExit as exc:
                result = 0 if exc.code is None else int(exc.code)
            except KeyboardInterrupt:
                if not expired:
                    raise
                result = 130
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        log.flush()
        os.dup2(stdout_fd, 1)
        os.dup2(stderr_fd, 2)
        os.close(stdout_fd)
        os.close(stderr_fd)
        os.environ.clear()
        os.environ.update(old_env)
        sys.argv, sys.path[:] = old_argv, old_path
        os.chdir(old_cwd)
    if expired:
        raise subprocess.TimeoutExpired(argv, execution_seconds)
    return result


def verify_case(root: Path, outcome: str) -> dict:
    proof = json.loads((root / "proof.json").read_text(encoding="utf-8"))
    stages = [
        json.loads(line) for line in (root / "stages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    expected = "completed" if outcome == "complete" else "abandoned"
    checks = [
        ("terminal outcome differs", proof["outcome"] == outcome and proof["status"] == expected),
        ("native identity changed", bool(proof["native"]) and (
            proof["native"] == proof["owner_native"] == proof["bridge_native"]
        )),
        ("expected one prompt", proof["prompts"] == 1),
        ("credential did not rotate", proof["rotated"] is True),
        ("terminal credential was not refused", proof["terminal_credential_status"] == 403),
        ("expected five refused routes", len(proof["refused"]) == 5),
        ("route was not refused", all(item["status"] == 403 for item in proof["refused"])),
        ("acceptance did not complete exactly once", sum(
            row["stage"] == "acceptance" and row["event"] == "complete" for row in stages
        ) == 1),
        ("cleanup did not complete", bool(stages) and (
            stages[-1]["stage"] == "cleanup" and stages[-1]["event"] == "complete"
        )),
    ]
    for message, passed in checks:
        if not passed:
            raise ValueError(message)
    return proof


def _finish_report(root: Path, report: dict) -> dict:
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (root / "cr-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def run(root: Path, *, central_image: str, bridge_python: Path, provider_python: Path) -> dict:
    report = {
        "scenario": "restricted-dispatch", "tier": "P", "family": "F1",
        "result": "fail", "stages": [], "jams": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "env": {"host_os": sys.platform, "central_image": central_image},
    }
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or any(
            manifest.get(key) != value
            for key, value in {"name": "restricted-dispatch", "tier": "P", "family": "F1"}.items()
        ):
            raise ValueError("manifest identity does not match the restricted-dispatch lane")
        if manifest.get("cases") != ["complete", "abandon"]:
            raise ValueError("manifest must retain both independent completion and abandonment cases")
        limits = manifest["deadlines_seconds"]
        if limits != {"pytest_item": 300, "contained_case": 360}:
            raise ValueError("manifest must retain the original 300/360-second budgets")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report["jams"].append({
            "category": "manifest-invalid", "evidence": str(MANIFEST),
            "error_type": type(exc).__name__, "error": str(exc),
            "hint": "Restore the declared lane identity, both cases and original deadlines before running.",
        })
        return _finish_report(root, report)
    for outcome in manifest["cases"]:
        case = root / outcome
        # The contained fixture creates its evidence directory exclusively.
        evidence = case / "evidence"
        env = dict(os.environ)
        suffix = uuid.uuid4().hex[:12]
        containers, networks = resource_names(suffix)
        env.update({
            "AGENT_DISPATCH_RESTRICTED_E2E": "1",
            "AGENT_DISPATCH_E2E_CENTRAL_IMAGE": central_image,
            "AGENT_DISPATCH_E2E_BRIDGE_PYTHON": str(bridge_python),
            "AGENT_DISPATCH_E2E_PROVIDER_PYTHON": str(provider_python),
            "AGENT_DISPATCH_E2E_EVIDENCE_DIR": str(evidence),
            "AGENT_DISPATCH_E2E_RESOURCE_SUFFIX": suffix,
        })
        started = time.monotonic()
        stage = {"case": outcome, "status": "fail", "log": f"{outcome}/runner.log"}
        phase = "runner"
        runner_failed = False
        launched = False
        try:
            case.mkdir()
            with (case / "runner.log").open("w", encoding="utf-8") as log:
                launched = True
                returncode = _run_case(
                    [sys.executable, str(REPO / "tools" / "run-plugin-tests.py"),
                     "agent-dispatch", "--test-file", TEST_FILE, "-k", outcome,
                     "--test-timeout", str(limits["pytest_item"]),
                     "--timeout", str(limits["contained_case"]),
                     "--plugin-timeout", str(limits["contained_case"])],
                    env=env, log=log, timeout=limits["contained_case"],
                )
            if returncode:
                runner_failed = True
                raise ValueError(f"Contained case exited {returncode}")
            phase = "proof"
            verify_case(evidence, outcome)
            stage["status"] = "pass"
        except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired) as exc:
            stage["error_type"] = type(exc).__name__
            stage["error"] = str(exc)
            category = (
                "contained-timeout" if isinstance(exc, subprocess.TimeoutExpired)
                else "proof-invalid" if phase == "proof"
                else "runner-exit" if runner_failed
                else "runner-io"
            )
            hints = {
                "contained-timeout": "Inspect the contained deadline failure; do not increase the budget or resample.",
                "proof-invalid": "Inspect proof.json and stages.jsonl; exit zero is not complete authority evidence.",
                "runner-exit": "Inspect the contained runner log and fix its actual failing contract.",
                "runner-io": "Check prepared runtime paths and the caller-owned evidence directory permissions.",
            }
            report["jams"].append({
                "category": category, "evidence": stage["log"], "hint": hints[category],
                "error_type": stage["error_type"], "error": stage["error"],
            })
        finally:
            if launched:
                try:
                    _cleanup_docker(containers, networks, deadline=started + limits["contained_case"])
                    stage["owned_resource_cleanup"] = "complete"
                except AssertionError as exc:
                    stage["status"] = "fail"
                    stage["owned_resource_cleanup"] = "failed"
                    report["jams"].append({
                        "category": "cleanup-incomplete", "evidence": str(case),
                        "error_type": type(exc).__name__, "error": str(exc),
                        "hint": "Reconcile the exact owned resources; failed cleanup never proves absence.",
                    })
        stage["duration_seconds"] = time.monotonic() - started
        if stage["duration_seconds"] > limits["contained_case"]:
            stage["status"] = "fail"
            report["jams"].append({
                "category": "contained-timeout", "evidence": stage["log"],
                "error_type": "TimeoutExpired",
                "error": "Case execution and cleanup exceeded the original contained-case deadline",
                "hint": "Account for execution, owner unwinding and cleanup within the same original budget.",
            })
        report["stages"].append(stage)
        if stage["status"] != "pass":
            break
    if len(report["stages"]) == len(manifest["cases"]) and all(
        stage["status"] == "pass" for stage in report["stages"]
    ):
        report["result"] = "pass"
    return _finish_report(root, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--central-image", required=True, help="Candidate services/dispatch image")
    parser.add_argument("--bridge-python", required=True, type=Path)
    parser.add_argument("--provider-python", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path, help="Existing private scratch root")
    args = parser.parse_args()
    supplied = args.results_root.absolute()
    if any(
        path.is_symlink()
        or (os.name == "nt" and (
            getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ))
        for path in (supplied, *supplied.parents)
    ):
        parser.error("--results-root must not contain link indirection")
    root = supplied.resolve()
    if not root.is_dir() or root == REPO or REPO in root.parents:
        parser.error("--results-root must be an existing directory outside the repository")
    for python in (args.bridge_python, args.provider_python):
        if not python.is_absolute() or not python.is_file():
            parser.error("Prepared runtime interpreters must be existing absolute files")
    run_dir = Path(tempfile.mkdtemp(
        prefix="restricted-dispatch-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-"),
        dir=root,
    ))
    report = run(
        run_dir, central_image=args.central_image,
        bridge_python=args.bridge_python, provider_python=args.provider_python,
    )
    print(json.dumps({"results": str(run_dir), **report}))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
