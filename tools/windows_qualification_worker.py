#!/usr/bin/env python3
"""Protected-controller adapter for one disposable hosted Windows test request."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from windows_qualification_contract import (
    VERSION, admit, canonical, request, validate_files,
)
from windows_qualification_transport import Transport

ROOT = Path(__file__).resolve().parents[1]


def junit_cases(directory: Path) -> list[dict]:
    """Keep actual test identities, including failure and prerequisite skips."""
    cases = []
    for path in sorted(directory.rglob("*.xml")):
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("JUnit exceeds evidence bound")
        for case in ET.parse(path).getroot().iter("testcase"):
            outcome = next((
                name for tag, name in
                (("error", "error"), ("failure", "failed"), ("skipped", "skipped"))
                if case.find(tag) is not None
            ), "passed")
            cases.append({
                "class": case.get("classname", ""), "name": case.get("name", ""),
                "outcome": outcome, "seconds": case.get("time", ""),
                "reason": next((
                    case.find(tag).get("message", "")[:1000]
                    for tag in ("error", "failure", "skipped") if case.find(tag) is not None
                ), ""),
            })
    return cases


def plugin_command(root: Path, selected: dict, output: Path) -> list[str]:
    """Use the canonical plugin runner without changing its acceptance bounds."""
    argv = [
        sys.executable, str(root / "tools/run-plugin-tests.py"), selected["plugin"],
        "--junit-dir", str(output / "junit"),
    ]
    for file in selected["files"]:
        argv.extend(("--test-file", file))
    if selected["k"]:
        argv.extend(("-k", selected["k"]))
    if selected["explicit_tiers"]:
        argv.append("--allow-explicit-tiers")
    return argv


def tool_tests(root: Path, selected: dict, output: Path) -> int:
    """Run tool files through the existing sandbox/process containment owner."""
    from plugin_test_containment import Limits, TestSandbox, isolated_environment, run_contained
    files = selected["files"] or ["tools"]
    (output / "junit").mkdir()
    argv = [
        sys.executable, "-m", "pytest", "-q", *files,
        "--junitxml=" + str(output / "junit/tools.xml"), "--timeout=30",
    ]
    if selected["k"]:
        argv.extend(("-k", selected["k"]))
    with TestSandbox(prefix="windows-qualification-tools-") as directory:
        sandbox = Path(directory)
        env = isolated_environment(os.environ, sandbox, allow_explicit_tiers=selected["explicit_tiers"])
        return run_contained(argv, cwd=root, env=env, sandbox=sandbox, limits=Limits(wall_seconds=600))


def execute(root: Path, value: dict, output: Path) -> dict:
    """Run selected source, retaining a truthful manifest even after failure."""
    request(value)
    output.mkdir(parents=True, exist_ok=True)
    result = {
        "version": VERSION, "request": value,
        "run_id": int(os.environ.get("GITHUB_RUN_ID", "0")),
        "run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")),
        "control_sha": os.environ.get("GITHUB_SHA", ""),
        "actual_source_sha": None,
        "platform": {
            "system": platform.system(), "release": platform.release(),
            "python": platform.python_version(), "machine": platform.machine(),
            "image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion"),
        },
        "outcome": "incomplete", "tests": [], "phases": [],
    }
    started = time.monotonic()

    def save():
        (output / "result.json").write_text(canonical(result) + "\n", encoding="utf-8")

    def phase(name: str, action):
        row = {"name": name, "status": "started", "started_seconds": time.monotonic() - started}
        result["phases"].append(row)
        save()
        print(f"[winqual] {name}: started", flush=True)
        try:
            answer = action()
            row["status"] = "passed"
            return answer
        except Exception:
            row["status"] = "failed"
            raise
        finally:
            row["ended_seconds"] = time.monotonic() - started
            save()
            print(f"[winqual] {name}: {row['status']}", flush=True)

    def owned(argv, timeout):
        from agent_procutil.owned_process import run_caller_owned
        index = len(result["phases"])
        try:
            completed = run_caller_owned(
                argv, cwd=root, timeout=timeout,
                max_stdout_bytes=8 * 1024 * 1024, max_stderr_bytes=2 * 1024 * 1024,
                env={
                    **os.environ,
                    "PYTHONPATH": os.pathsep.join((
                        str(root / "libs/agent-procutil/src"), str(root / "tools"),
                    )),
                },
            )
        except subprocess.TimeoutExpired as exc:
            for name, content in (("stdout", exc.output), ("stderr", exc.stderr)):
                text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content or ""
                (output / f"phase-{index}.{name}.log").write_text(text, encoding="utf-8")
            result["failure_kind"] = "timeout"
            raise
        (output / f"phase-{index}.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (output / f"phase-{index}.stderr.log").write_text(completed.stderr, encoding="utf-8")
        print(completed.stdout, end="", flush=True)
        print(completed.stderr, end="", file=sys.stderr, flush=True)
        if completed.returncode:
            raise ValueError(f"selected command failed ({completed.returncode})")
        return completed

    try:
        def verify_source():
            transport = Transport(root, "origin")
            actual = transport.git("rev-parse", "HEAD").stdout.strip()
            result["actual_source_sha"] = actual
            if actual != value["source_sha"]:
                raise ValueError("candidate checkout identity mismatch")
            if value["envelope_sha"]:
                transport.git(
                    "fetch", "--no-tags", "--no-write-fetch-head", "origin", value["envelope_sha"],
                )
                transport.validate_tip(value["envelope_sha"])
                transport.git("merge-base", "--is-ancestor", actual, value["envelope_sha"])
            validate_files(root, value["selection"])
        phase("source-and-selection", verify_source)
        selected = value["selection"]
        # Source-only import paths, not an installed host runtime or prewarmed
        # candidate test venv. Packet preparation remains inside its own clock.
        sys.path.insert(0, str(ROOT / "libs/agent-procutil/src"))
        if selected["kind"] == "plugin":
            phase("canonical-plugin-tests", lambda: owned(plugin_command(root, selected, output), 1800))
        elif selected["kind"] == "tools":
            phase("contained-tool-tests", lambda: owned([
                sys.executable, str(Path(__file__)), "tools",
                "--candidate", str(root), "--output", str(output),
            ], 720))
        else:
            phase("bridge-http52-canonical-capture", lambda: owned([
                sys.executable, str(root / "tools/capture_bridge_http52.py"),
                "--source-commit", value["source_sha"], "--write",
            ], 600))
            phase("bridge-http52-independent-no-write", lambda: owned([
                sys.executable, str(root / "tools/capture_bridge_http52.py"),
                "--source-commit", value["source_sha"],
            ], 600))
            phase("bridge-contract-independent-verification", lambda: owned([
                sys.executable, str(root / "tools/check-agent-bridge-contracts.py"),
            ], 120))
            phase("bridge-http52-artifact-export", lambda: owned([
                sys.executable, str(root / "tools/bridge_http52_artifact.py"), "export",
                "--source-commit", value["source_sha"], "--artifact-dir", str(output / "packet"),
            ], 120))
            result["packet"] = json.loads((output / "packet/manifest.json").read_text("utf-8"))
        if selected["kind"] in {"plugin", "packet"}:
            def test_runtime():
                from plugin_test_resources import venv_root
                target = selected["plugin"] if selected["kind"] == "plugin" else "agent-bridge"
                python = venv_root(root) / target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                metadata = owned([
                    str(python), "-c",
                    "import json,platform,sys; print(json.dumps({"
                    "'python':platform.python_version(),'executable':sys.executable,"
                    "'system':platform.system()}))",
                ], 60)
                result["test_runtime"] = json.loads(metadata.stdout)
            phase("test-runtime", test_runtime)
        else:
            result["test_runtime"] = {
                "python": platform.python_version(), "executable": sys.executable,
                "system": platform.system(),
            }
        result["tests"] = junit_cases(output / "junit")
        result["outcome"] = (
            "qualified" if selected["kind"] == "packet" or (
                result["tests"] and all(case["outcome"] == "passed" for case in result["tests"])
            ) else "incomplete"
        )
    except Exception as exc:
        result["outcome"] = "failed"
        result["error"] = str(exc)[:2000]
        result["tests"] = junit_cases(output / "junit")
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        save()
    return result


def main() -> int:
    """Admit before candidate checkout, or run one already-admitted request."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("admit", "execute", "tools"))
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text("utf-8"))
    value = admit(
        event, actor=os.environ["GITHUB_ACTOR"],
        triggering_actor=os.environ["GITHUB_TRIGGERING_ACTOR"], ref=os.environ["GITHUB_REF"],
        ref_protected=os.environ.get("GITHUB_REF_PROTECTED") == "true",
    )
    if args.operation == "admit":
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write("source_sha=" + value["source_sha"] + "\n")
        return 0
    if args.operation == "tools":
        validate_files(args.candidate.resolve(), value["selection"])
        if value["selection"]["kind"] != "tools":
            raise ValueError("tool worker requires a tools request")
        return tool_tests(args.candidate.resolve(), value["selection"], args.output.resolve())
    result = execute(args.candidate.resolve(), value, args.output.resolve())
    return 0 if result["outcome"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
