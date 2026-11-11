#!/usr/bin/env python3
"""Protected-controller adapter for one disposable hosted Windows test request."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from windows_qualification_contract import (
    DESKTOP_CASE, DESKTOP_PACKET, MCP_LIFETIME_CASE, MCP_LIFETIME_PACKET,
    NATIVE_PACKETS, NATIVE_VERSIONS, VERSION,
    admit, canonical, request,
    validate_files, validate_result,
)
from windows_qualification_transport import Transport

ROOT = Path(__file__).resolve().parents[1]


def desktop_tests(root: Path, output: Path, request_id: str) -> int:
    """Keep the one visible control inside ordinary caller containment."""
    from plugin_test_containment import Limits, TestSandbox, isolated_environment, run_contained
    from windows_qualification_desktop import require_hosted_owner

    admission = {**os.environ, "WINDOWS_QUALIFICATION_DESKTOP": "1"}
    require_hosted_owner(admission, system=platform.system(), contained=False)
    (output / "junit").mkdir(parents=True, exist_ok=True)
    with TestSandbox(prefix="desktop-capability-", dir=Path(admission["RUNNER_TEMP"])) as directory:
        sandbox = Path(directory)
        env = isolated_environment(admission, sandbox, allow_explicit_tiers=True)
        env.update({
            "PYTHONPATH": str(root / "tools"),
            "WINDOWS_QUALIFICATION_DESKTOP_REQUEST": request_id,
            "WINDOWS_QUALIFICATION_DESKTOP_OUTPUT": str(output / "packet"),
        })
        return run_contained(
            [
                sys.executable, "-m", "pytest", "-q",
                str(root / "tools/test_windows_qualification_desktop_native.py"),
                "-k", DESKTOP_CASE, "-p", "pytest_portfolio_guard",
                "--basetemp=" + str(sandbox / "pytest"),
                "-o", "cache_dir=" + str(sandbox / "pytest-cache"),
                "--junitxml=" + str(output / "junit/desktop.xml"), "--timeout=50",
            ],
            cwd=root, env=env, sandbox=sandbox, limits=Limits(wall_seconds=60),
        )


def mcp_lifetime(root: Path, output: Path) -> int:
    """Use the existing hosted native Job and runner-renewed daemon owner."""
    from plugin_test_containment import Limits, TestSandbox, isolated_environment, run_contained
    from plugin_test_cache import ordinary_cache_root
    from plugin_test_resources import venv_root
    from _admission_protocol import acquire

    started = time.monotonic()
    environment_root = ordinary_cache_root(venv_root(root) / "agent-mcp")
    python = environment_root / "Scripts" / "python.exe"
    if not python.is_file():
        raise ValueError("MCP lifetime packet requires the prepared canonical test environment")
    temporary = os.environ.get("RUNNER_TEMP")
    if not temporary:
        raise ValueError("MCP lifetime packet requires its disposable hosted test owner")
    (output / "junit").mkdir(parents=True, exist_ok=True)
    lease = acquire(0, environment_root)
    try:
        with TestSandbox(prefix="mcp-lifetime-", dir=Path(temporary)) as directory:
            sandbox = Path(directory)
            env = isolated_environment(os.environ, sandbox, allow_explicit_tiers=True)
            env.update({
                "PYTHONPATH": str(root / "tools"),
                "COPILOT_EXTENSIONS_TEST_VENV_ROOT": str(venv_root(root)),
                "AGENT_MCP_NATIVE_LIFETIME": "1",
            })
            result = run_contained(
                [
                    str(python), "-m", "pytest", "-q",
                    str(root / "plugins/agent-mcp/tests/test_custody_native_lifetime.py"),
                    "-k", MCP_LIFETIME_CASE,
                    "--basetemp", str(sandbox / "t"),
                    "-o", f"cache_dir={sandbox / 'pytest-cache'}",
                    "--junitxml=" + str(output / "junit/mcp-lifetime.xml"), "--timeout=600",
                ],
                cwd=root / "plugins/agent-mcp", env=env, sandbox=sandbox,
                limits=Limits(wall_seconds=900), native_application=True,
            )
            if result:
                from qualify_foundation_lifetime import report_fixture_logs

                report_fixture_logs(sandbox)
    finally:
        lease.release()
    if result:
        raise ValueError(f"MCP native lifetime fixture failed ({result})")
    if time.monotonic() - started >= 900:
        raise ValueError("MCP native lifetime phase exceeded 900 seconds including cleanup")
    return result


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


def native_command(
    root: Path, output: Path, packet: str = "native-worktrees-embody",
) -> list[str]:
    """Select exactly one native consumer with its existing functional bounds."""
    plugin, module, case, _ = NATIVE_PACKETS[packet]
    limits = (
        ("300", "420", "300") if module == "test_manager_application_launch"
        else ("900", "1200", "900") if plugin == "agent-worktrees"
        else ("300", "420", "180")
    )
    return [
        sys.executable, str(root / "tools/run-plugin-tests.py"), plugin,
        "--test-file", f"tests/native/{module}.py",
        "--allow-explicit-tiers", "-k", case.removeprefix("test_"),
        "--timeout", limits[0], "--plugin-timeout", limits[1], "--test-timeout", limits[2],
        "--junit-dir", str(output / "junit"),
    ]


def native_prerequisites(owned, *, require_feed: bool = True) -> dict:
    """Require disposable native binaries; only Worktrees needs its governed feed."""
    if (
        os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1"
        or not os.environ.get("RUNNER_TEMP")
    ):
        raise ValueError("native packet requires its disposable hosted Windows owner")
    if require_feed:
        from governed_feed_trust import _validated_trusted_index_url
        selected = _validated_trusted_index_url(dict(os.environ))
        if selected is None:
            raise ValueError("native packet requires the configured governed HTTPS feed")
        index = urllib.parse.urlsplit(selected[0])
        if index.username or index.password or index.query or index.fragment:
            raise ValueError("native packet admits only a credential-free feed URL")
        try:
            with urllib.request.urlopen(selected[0].rstrip("/") + "/packaging/", timeout=20) as response:
                if response.status != 200 or not response.read(256):
                    raise ValueError("package index unavailable")
        except Exception as exc:
            raise ValueError(f"hosted governed feed unavailable ({type(exc).__name__})") from None
    versions = {}
    for name, variable, flag in (
        ("copilot", "COPILOT_NATIVE_TEST_EXECUTABLE", "--version"),
        ("psmux", "PSMUX_TEST_BIN", "-V"),
    ):
        binary = Path(os.environ.get(variable, "")).resolve()
        if not binary.is_file() or not binary.is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve()):
            raise ValueError(f"{name} prerequisite is not a disposable runner binary")
        reported = owned([str(binary), flag], 30).stdout
        version = NATIVE_VERSIONS[name]
        pattern = r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?"
        if name == "psmux":
            pattern = rf"(?m)^psmux[ \t]+({pattern})(?=\s|$)"
        if re.findall(pattern, reported) != [version]:
            raise ValueError(f"{name} native prerequisite version mismatch")
        versions[name] = version
    return versions


def redact_feed(text: str) -> str:
    """Actions masking does not protect uploaded log or JUnit file contents."""
    text = re.sub(r"https?://[^\s<>\"']+", "[redacted-url]", text, flags=re.IGNORECASE)
    index = os.environ.get("UV_DEFAULT_INDEX", "")
    if index:
        host = urllib.parse.urlsplit(index).hostname
        for value in sorted({index, host or ""} - {""}, key=len, reverse=True):
            text = re.sub(re.escape(value), "[configured-feed]", text, flags=re.IGNORECASE)
    return text


def sanitize_native_files(output: Path) -> bool:
    """Sanitize retained text even when the execution worker was interrupted."""
    omitted = False
    for path in output.rglob("*"):
        if path.suffix not in {".xml", ".json", ".log"}:
            continue
        if path.is_symlink() or (path.is_file() and path.stat().st_size > 16 * 1024 * 1024):
            path.unlink()
            omitted = True
        elif path.is_file():
            path.write_text(redact_feed(path.read_text("utf-8", errors="replace")), encoding="utf-8")
    return omitted


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
        env["PYTHONPATH"] = str(root / "tools")
        argv.extend((
            "-p", "pytest_portfolio_guard", "--basetemp=" + str(sandbox / "pytest"),
            "-o", "cache_dir=" + str(sandbox / "pytest-cache"),
        ))
        return run_contained(argv, cwd=root, env=env, sandbox=sandbox, limits=Limits(wall_seconds=600))


def execute(root: Path, value: dict, output: Path) -> dict:
    """Run selected source, retaining a truthful manifest even after failure."""
    request(value)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    native = NATIVE_PACKETS.get(value["selection"].get("name"))
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
        text = canonical(result) + "\n"
        (output / "result.json").write_text(redact_feed(text) if native else text, encoding="utf-8")

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

    def owned(argv, timeout, *, allow_empty=False):
        from agent_procutil.owned_process import run_caller_owned
        index = len(result["phases"])
        try:
            completed = run_caller_owned(
                argv, cwd=root, timeout=timeout,
                max_stdout_bytes=8 * 1024 * 1024, max_stderr_bytes=2 * 1024 * 1024,
                env={
                    **os.environ,
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONPATH": os.pathsep.join((
                        str(root / "libs/agent-procutil/src"), str(root / "tools"),
                    )),
                },
            )
        except subprocess.TimeoutExpired as exc:
            for name, content in (("stdout", exc.output), ("stderr", exc.stderr)):
                text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content or ""
                if native:
                    text = redact_feed(text)
                (output / f"phase-{index}.{name}.log").write_text(text, encoding="utf-8")
            result["failure_kind"] = "timeout"
            raise
        stdout = redact_feed(completed.stdout) if native else completed.stdout
        stderr = redact_feed(completed.stderr) if native else completed.stderr
        (output / f"phase-{index}.stdout.log").write_text(stdout, encoding="utf-8")
        (output / f"phase-{index}.stderr.log").write_text(stderr, encoding="utf-8")
        print(stdout, end="", flush=True)
        print(stderr, end="", file=sys.stderr, flush=True)
        if (
            allow_empty and completed.returncode == 5
            and any((output / "junit").rglob("*.xml"))
            and not junit_cases(output / "junit")
        ):
            result["incomplete_reason"] = "empty-selection"
            return completed
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
            ], 720, allow_empty=True))
        elif selected["name"] == DESKTOP_PACKET:
            phase(DESKTOP_PACKET, lambda: owned([
                sys.executable, str(Path(__file__)), "desktop",
                "--candidate", str(root), "--output", str(output),
            ], 90, allow_empty=True))
            evidence = output / "packet/desktop-capability.json"
            if evidence.is_file():
                if evidence.is_symlink() or evidence.stat().st_size > 16 * 1024 * 1024:
                    raise ValueError("desktop capability evidence exceeds its regular-file bound")
                result["desktop"] = json.loads(evidence.read_text("utf-8"))
        elif native:
            def prepare_native():
                preparation = os.environ.get("WINDOWS_QUALIFICATION_NATIVE_PREPARATION", "success")
                if preparation != "success":
                    status = preparation if preparation in {"failure", "cancelled", "skipped"} else "unknown"
                    raise ValueError(f"Pinned native prerequisite acquisition did not succeed ({status})")
                return native_prerequisites(
                    owned, require_feed=native[0] == "agent-worktrees",
                )
            result["native_prerequisites"] = phase(
                "native-prerequisites", prepare_native,
            )
            if selected["name"] == "native-worktrees-caller-jobs":
                from windows_qualification_lifetime import worktrees_lifetime

                phase(native[3], lambda: worktrees_lifetime(root, output, prepare=owned))
            else:
                phase(native[3], lambda: owned(native_command(root, output, selected["name"]), 1800))
        elif selected["name"] == MCP_LIFETIME_PACKET:
            phase("prepare-mcp-test-runtime", lambda: owned([
                sys.executable, str(root / "tools/run-plugin-tests.py"), "agent-mcp",
                "--prepare-only",
            ], 420))
            phase(MCP_LIFETIME_PACKET, lambda: mcp_lifetime(root, output))
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
                target = selected["plugin"] if selected["kind"] == "plugin" else (
                    native[0] if native else (
                        "agent-mcp" if selected.get("name") == MCP_LIFETIME_PACKET else "agent-bridge"
                    )
                )
                python = (
                    Path(sys.executable) if selected.get("name") == DESKTOP_PACKET else
                    venv_root(root) / target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                )
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
            "qualified" if (
                selected["kind"] == "packet" and not native
                and selected["name"] not in {MCP_LIFETIME_PACKET, DESKTOP_PACKET}
            ) or (
                result["tests"] and all(case["outcome"] == "passed" for case in result["tests"])
            ) else "incomplete"
        )
        if native or selected.get("name") in {MCP_LIFETIME_PACKET, DESKTOP_PACKET}:
            validate_result(
                result, value, run_id=result["run_id"], control_sha=result["control_sha"],
                run_attempt=result["run_attempt"],
            )
    except Exception as exc:
        result["outcome"] = "failed"
        result["error"] = str(exc)[:2000]
        result["tests"] = junit_cases(output / "junit")
    finally:
        if native:
            if "error" in result:
                result["error"] = redact_feed(result["error"])
            if sanitize_native_files(output):
                result["outcome"] = "failed"
                result["error"] = "Unsafe or oversized evidence omitted"
            result["tests"] = [
                {key: redact_feed(item) if isinstance(item, str) else item for key, item in case.items()}
                for case in result["tests"]
            ]
        result["elapsed_seconds"] = time.monotonic() - started
        save()
    return result


def main() -> int:
    """Admit before candidate checkout, or run one already-admitted request."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("admit", "execute", "tools", "desktop", "sanitize"))
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
            stream.write("packet=" + value["selection"].get("name", "") + "\n")
            stream.write("native=" + str(value["selection"].get("name") in NATIVE_PACKETS).lower() + "\n")
        return 0
    if args.operation == "tools":
        candidate = args.candidate.resolve()
        sys.path.insert(0, str(candidate / "tools"))
        validate_files(candidate, value["selection"])
        if value["selection"]["kind"] != "tools":
            raise ValueError("tool worker requires a tools request")
        return tool_tests(candidate, value["selection"], args.output.resolve())
    if args.operation == "desktop":
        if value["selection"] != {"kind": "packet", "name": DESKTOP_PACKET}:
            raise ValueError("desktop worker requires its explicit named packet")
        return desktop_tests(args.candidate.resolve(), args.output.resolve(), value["request_id"])
    if args.operation == "sanitize":
        if value["selection"].get("name") not in NATIVE_PACKETS:
            raise ValueError("native sanitization requires its named packet")
        output = args.output.resolve()
        omitted = sanitize_native_files(output)
        manifest = output / "result.json"
        if omitted and manifest.is_file():
            result = json.loads(manifest.read_text("utf-8"))
            result.update(outcome="failed", error="Unsafe or oversized evidence omitted")
            manifest.write_text(canonical(result) + "\n", encoding="utf-8")
        return 0
    result = execute(args.candidate.resolve(), value, args.output.resolve())
    return 0 if result["outcome"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
