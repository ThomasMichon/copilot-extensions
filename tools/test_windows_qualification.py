"""Focused controls for typed selection, owner admission and request resumption."""

from __future__ import annotations

import copy
import base64
from contextlib import nullcontext
from email.utils import formatdate
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import windows_qualification_contract as contract
import windows_qualification_worker as worker

SPEC = importlib.util.spec_from_file_location(
    "windows_qualification_client", Path(__file__).with_name("windows-qualification.py"),
)
client_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client_module)


def focused():
    return {
        "kind": "plugin", "plugin": "sample", "files": ["tests/test_sample.py"],
        "k": "", "full": False, "explicit_tiers": False,
    }


def request():
    return {
        "version": 1, "request_id": "a" * 32, "source_sha": "b" * 40,
        "selection": focused(), "envelope_sha": None,
    }


def event():
    return {
        "repository": {"owner": {"type": "User", "login": "owner"}, "default_branch": "main"},
        "inputs": {"request_id": "a" * 32, "request": contract.canonical(request())},
    }


@pytest.mark.parametrize("actor,rerun,ref", [
    ("collaborator", "owner", "refs/heads/main"),
    ("owner", "collaborator", "refs/heads/main"),
    ("owner", "owner", "refs/heads/dev"),
    ("", "owner", "refs/heads/main"),
])
def test_original_rerun_and_protected_ref_are_independent_gates(actor, rerun, ref):
    with pytest.raises(ValueError, match="admission denied"):
        contract.admit(event(), actor=actor, triggering_actor=rerun, ref=ref, ref_protected=True)


def test_individual_owner_only_and_request_title_binding():
    value = event()
    assert contract.admit(
        value, actor="OWNER", triggering_actor="owner", ref="refs/heads/main", ref_protected=True,
    ) == request()
    value["repository"]["owner"]["type"] = "Organization"
    with pytest.raises(ValueError):
        contract.admit(value, actor="owner", triggering_actor="owner", ref="refs/heads/main", ref_protected=True)
    value = event()
    value["inputs"]["request_id"] = "c" * 32
    with pytest.raises(ValueError, match="title"):
        contract.admit(value, actor="owner", triggering_actor="owner", ref="refs/heads/main", ref_protected=True)
    with pytest.raises(ValueError, match="admission denied"):
        contract.admit(
            event(), actor="owner", triggering_actor="owner",
            ref="refs/heads/main", ref_protected=False,
        )


@pytest.mark.parametrize("file", [
    "../test_secret.py", "tests/../test_secret.py", "/tests/test_file.py",
    "tests\\test_file.py", "tests/test_file.py::test_case",
    "tests/test_file.py\n--full", "tests/test_file.py;echo", "tests//test_file.py",
])
def test_typed_files_reject_escape_shell_and_pytest_option_inputs(file):
    value = focused()
    value["files"] = [file]
    with pytest.raises(ValueError):
        contract.selection(value)


def test_focused_is_default_and_full_is_an_explicit_distinct_choice():
    value = focused()
    assert contract.selection(value) == value
    value["files"] = []
    with pytest.raises(ValueError, match="explicit full"):
        contract.selection(value)
    value["full"] = True
    assert contract.selection(value) == value
    value["files"] = ["tests/test_sample.py"]
    with pytest.raises(ValueError):
        contract.selection(value)


@pytest.mark.parametrize("mutation", [
    {"version": 2}, {"version": True}, {"source_sha": "main"},
    {"envelope_sha": "dev"}, {"request_id": "name"},
    {"command": "python anything.py"},
])
def test_request_contract_is_strict(mutation):
    value = request()
    value.update(mutation)
    with pytest.raises(ValueError):
        contract.request(value)


def test_plugin_adapter_preserves_typed_argv_and_default_budget(tmp_path):
    value = focused()
    value["k"] = "first or second"
    value["explicit_tiers"] = True
    argv = worker.plugin_command(tmp_path, value, tmp_path / "out")
    assert "--timeout" not in argv
    assert argv[argv.index("-k") + 1] == "first or second"
    assert "--test-file" in argv and "--allow-explicit-tiers" in argv
    assert "--all" not in argv and "--allow-host-state" not in argv


@pytest.mark.parametrize("caller", ["embody", "copilot"])
def test_native_packet_preserves_public_caller_selection_and_functional_bounds(tmp_path, caller):
    argv = worker.native_command(tmp_path, tmp_path / "out", "native-worktrees-" + caller)
    assert argv[argv.index("--test-file") + 1] == "tests/native/test_native_seed_application.py"
    assert argv[argv.index("-k") + 1] == "public_" + caller
    assert [argv[argv.index(flag) + 1] for flag in (
        "--timeout", "--plugin-timeout", "--test-timeout",
    )] == ["900", "1200", "900"]
    assert "--allow-host-state" not in argv


@pytest.mark.parametrize("mode,case", [("no-mux", "manager_no_mux_decision"), ("mux", "manager_mux_decision")])
def test_manager_packet_uses_its_own_case_and_original_manager_bounds(tmp_path, mode, case):
    argv = worker.native_command(tmp_path, tmp_path / "out", "native-worktrees-manager-" + mode)
    assert argv[2] == "agent-worktrees"
    assert argv[argv.index("--test-file") + 1] == "tests/native/test_manager_application_launch.py"
    assert argv[argv.index("-k") + 1] == case
    assert [argv[argv.index(flag) + 1] for flag in (
        "--timeout", "--plugin-timeout", "--test-timeout",
    )] == ["300", "420", "300"]
    assert "--allow-explicit-tiers" in argv and "--allow-host-state" not in argv


@pytest.mark.parametrize("packet,module,case", [
    ("native-driver-auth-wait", "test_native_headed_auth_wait", "opt_in_receiver_auth_wait_and_same_session_resume"),
    ("native-driver-inflight", "test_native_headed_auth_wait", "opt_in_receiver_inflight_expiry_preserves_hold"),
    ("native-driver-observation-loss", "test_native_headed_auth_wait", "opt_in_receiver_sdk_observation_loss_preserves_hold"),
    ("native-driver-startup-seed", "test_headed_extension", "native_headed_startup_seed_observation_precedes_controlled_send"),
    ("native-driver-startup-no-seed", "test_headed_extension", "native_headed_startup_no_seed_is_not_an_idle_claim"),
    ("native-driver-startup-cold-resume", "test_headed_extension", "native_headed_startup_cold_resume_preserves_history"),
])
def test_receiver_packet_selects_only_its_native_fixture_and_existing_deadlines(tmp_path, packet, module, case):
    argv = worker.native_command(tmp_path, tmp_path / "out", packet)
    assert argv[2] == "agent-remote-driver"
    assert argv[argv.index("--test-file") + 1] == f"tests/native/{module}.py"
    assert argv[argv.index("-k") + 1] == case
    assert [argv[argv.index(flag) + 1] for flag in (
        "--timeout", "--plugin-timeout", "--test-timeout",
    )] == ["300", "420", "180"]
    assert "--allow-explicit-tiers" in argv and "--allow-host-state" not in argv


@pytest.mark.parametrize("packet,native", [
    (None, False), ("bridge-http52", False), ("mcp-caller-lifetime", False),
    ("desktop-capability", False),
    ("native-worktrees-embody", True), ("native-worktrees-copilot", True),
    ("native-worktrees-caller-jobs", True),
    ("native-worktrees-manager-no-mux", True),
    ("native-worktrees-manager-mux", True),
    ("native-driver-auth-wait", True), ("native-driver-inflight", True),
    ("native-driver-observation-loss", True),
    ("native-driver-startup-seed", True), ("native-driver-startup-no-seed", True),
    ("native-driver-startup-cold-resume", True),
    ("native-unrecognized", None),
])
def test_admission_reports_native_preparation_only_for_known_packets(
    tmp_path, monkeypatch, packet, native,
):
    value = request()
    if packet:
        value["selection"] = {"kind": "packet", "name": packet}
    payload = event()
    payload["inputs"]["request"] = contract.canonical(value)
    event_path, output = tmp_path / "event.json", tmp_path / "output"
    event_path.write_text(contract.canonical(payload), encoding="utf-8")
    for key, content in {
        "GITHUB_EVENT_PATH": str(event_path), "GITHUB_OUTPUT": str(output),
        "GITHUB_ACTOR": "owner", "GITHUB_TRIGGERING_ACTOR": "owner",
        "GITHUB_REF": "refs/heads/main", "GITHUB_REF_PROTECTED": "true",
    }.items():
        monkeypatch.setenv(key, content)
    monkeypatch.setattr(sys, "argv", ["worker", "admit"])
    if native is None:
        with pytest.raises(ValueError, match="unsupported named packet"):
            worker.main()
        assert not output.exists()
    else:
        assert worker.main() == 0
        assert dict(line.split("=", 1) for line in output.read_text("utf-8").splitlines()) == {
            "source_sha": "b" * 40, "packet": packet or "", "native": str(native).lower(),
        }


@pytest.mark.parametrize("require_feed", [True, False])
@pytest.mark.parametrize("mismatch,mux_output,failure", [
    (False, "tmux 3.3.8\r\npsmux 3.3.8 (66cf613 2026-08-18)\r\n", None),
    (True, "psmux 3.3.8", "copilot"),
    (False, "tmux 3.3.8\npsmux 3.3.9", "psmux"),
    (False, "tmux 3.3.8", "psmux"),
    (False, "psmux 3.3.8\npsmux 3.3.8", "psmux"),
    (False, "psmux 3.3.8-1", "psmux"),
])
def test_native_prerequisites_check_disposable_binaries_and_actual_version(
    tmp_path, monkeypatch, mismatch, mux_output, failure, require_feed,
):
    runner = tmp_path / "runner"
    runner.mkdir()
    copilot, mux = runner / "copilot.exe", runner / "psmux.exe"
    copilot.write_bytes(b"fixture")
    mux.write_bytes(b"fixture")
    environment = {
        "GITHUB_ACTIONS": "true", "COPILOT_DRIVER_HEADED_MUX_E2E": "1",
        "RUNNER_TEMP": str(runner), "UV_DEFAULT_INDEX": "https://feed.example.invalid/simple/",
        "COPILOT_NATIVE_TEST_EXECUTABLE": str(copilot), "PSMUX_TEST_BIN": str(mux),
    }
    if not require_feed:
        environment.pop("UV_DEFAULT_INDEX")
    monkeypatch.setattr(worker, "os", SimpleNamespace(name="nt", environ=environment))
    probes = []

    def probe(*args, **kwargs):
        probes.append(args)
        assert require_feed, "Driver must not probe the private feed"
        return nullcontext(SimpleNamespace(status=200, read=lambda count: b"package index"))

    monkeypatch.setattr(worker.urllib.request, "urlopen", probe)
    calls = []

    def owned(argv, timeout):
        calls.append((argv, timeout))
        return SimpleNamespace(stdout=(
            "GitHub Copilot CLI 1.0.105-1." if mismatch else "GitHub Copilot CLI 1.0.105."
        ) if argv[0] == str(copilot) else mux_output)
    if failure:
        with pytest.raises(ValueError, match=f"{failure} native prerequisite version mismatch"):
            worker.native_prerequisites(owned, require_feed=require_feed)
        assert len(calls) == (1 if failure == "copilot" else 2)
    else:
        assert worker.native_prerequisites(owned, require_feed=require_feed) == contract.NATIVE_VERSIONS
        assert calls == [([str(copilot), "--version"], 30), ([str(mux), "-V"], 30)]
    assert bool(probes) is require_feed
    foreign = tmp_path / "foreign.exe"
    foreign.write_bytes(b"fixture")
    environment["PSMUX_TEST_BIN"] = str(foreign)
    with pytest.raises(ValueError):
        worker.native_prerequisites(owned, require_feed=require_feed)


def test_junit_retains_skips_failures_and_exact_case_identities(tmp_path):
    (tmp_path / "result.xml").write_text(
        '<testsuite><testcase classname="mod" name="pass[a]" time="1"/>'
        '<testcase classname="mod" name="skip"><skipped message="missing native prerequisite"/></testcase>'
        '<testcase classname="mod" name="failure"><failure message="real failure"/></testcase></testsuite>',
        encoding="utf-8",
    )
    cases = worker.junit_cases(tmp_path)
    assert [(case["name"], case["outcome"]) for case in cases] == [
        ("pass[a]", "passed"), ("skip", "skipped"), ("failure", "failed"),
    ]
    assert cases[1]["reason"] == "missing native prerequisite"


@pytest.mark.parametrize("code,junit,outcome", [
    (0, "", "incomplete"),
    (0, '<testsuite><testcase name="real"><skipped/></testcase></testsuite>', "incomplete"),
    (1, '<testsuite><testcase name="real"><failure/></testcase></testsuite>', "failed"),
    (0, '<testsuite><testcase name="real"/></testsuite>', "qualified"),
    (-1, "", "failed"),
    (5, "<testsuite/>", "incomplete"),
    (5, "", "failed"),
    (5, '<testsuite><testcase name="real"><failure/></testcase></testsuite>', "failed"),
])
def test_execution_preserves_actual_collection_failure_and_skip_outcomes(
    tmp_path, monkeypatch, code, junit, outcome,
):
    root = tmp_path / "candidate"
    test = root / "plugins/sample/tests/test_sample.py"
    test.parent.mkdir(parents=True)
    test.write_text("test fixture", encoding="utf-8")
    output = tmp_path / "evidence"

    def run(*args, **kwargs):
        if code == -1:
            raise subprocess.TimeoutExpired(args[0], 600, output="partial native log", stderr="deadline")
        if "-c" in args[0]:
            return SimpleNamespace(
                returncode=0, stdout='{"python":"3.13","system":"Windows"}', stderr="",
            )
        if junit:
            (output / "junit").mkdir()
            (output / "junit/actual.xml").write_text(junit, encoding="utf-8")
        return SimpleNamespace(returncode=code, stdout="actual test log", stderr="")
    monkeypatch.setitem(sys.modules, "agent_procutil.owned_process", SimpleNamespace(run_caller_owned=run))
    monkeypatch.setattr(worker, "Transport", lambda *args: SimpleNamespace(
        git=lambda *args: SimpleNamespace(stdout="b" * 40),
    ))
    value = request()
    if code == 5:
        test = root / "tools/test_sample.py"
        test.parent.mkdir()
        test.write_text("test fixture", encoding="utf-8")
        value["selection"] = {
            "kind": "tools", "files": ["tools/test_sample.py"], "k": "",
            "full": False, "explicit_tiers": False,
        }
    actual = worker.execute(root, value, output)
    assert actual["outcome"] == outcome
    assert json.loads((output / "result.json").read_text("utf-8"))["outcome"] == outcome
    assert (output / "phase-2.stdout.log").read_text("utf-8") == (
        "partial native log" if code == -1 else "actual test log"
    )
    if code == -1:
        assert actual["failure_kind"] == "timeout"
    if code == 5 and outcome == "incomplete":
        assert actual["incomplete_reason"] == "empty-selection"


def test_execution_preserves_real_unicode_console_output_and_failed_outcome(tmp_path, monkeypatch):
    import io

    import agent_procutil.owned_process as owned_process
    from agent_procutil.owned_process import run_caller_owned

    root = tmp_path / "candidate"
    test = root / "plugins/sample/tests/test_sample.py"
    test.parent.mkdir(parents=True)
    test.touch()
    output = tmp_path / "evidence"
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    stdout, stderr = io.BytesIO(), io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(stdout, encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(stderr, encoding="cp1252"))

    def run(argv, **kwargs):
        (output / "junit").mkdir()
        (output / "junit/actual.xml").write_text(
            '<testsuite><testcase name="real"><failure/></testcase></testsuite>',
            encoding="utf-8",
        )
        return run_caller_owned(
            [sys.executable, "-c",
             "import sys;print(chr(0xfffd));print(chr(0x65e5),file=sys.stderr);sys.exit(1)"],
            **kwargs,
        )

    monkeypatch.setattr(owned_process, "run_caller_owned", run)
    monkeypatch.setattr(worker, "Transport", lambda *args: SimpleNamespace(
        git=lambda *args: SimpleNamespace(stdout="b" * 40),
    ))
    actual = worker.execute(root, request(), output)
    assert actual["outcome"] == "failed"
    assert actual["error"] == "selected command failed (1)"
    assert actual["tests"][0]["outcome"] == "failed"
    assert (output / "phase-2.stdout.log").read_text("utf-8") == "\ufffd\n"
    assert (output / "phase-2.stderr.log").read_text("utf-8") == "\u65e5\n"
    assert "\ufffd" in stdout.getvalue().decode("utf-8").splitlines()
    assert "\u65e5" in stderr.getvalue().decode("utf-8").splitlines()


def result():
    return {
        "version": 1, "request": request(), "run_id": 42, "run_attempt": 1,
        "control_sha": "c" * 40, "actual_source_sha": "b" * 40,
        "platform": {"system": "Windows"}, "outcome": "qualified",
        "tests": [{"name": "real", "outcome": "passed"}],
        "phases": [
            {"name": "source-and-selection", "status": "passed"},
            {"name": "canonical-plugin-tests", "status": "passed"},
            {"name": "test-runtime", "status": "passed"},
        ],
    }


@pytest.mark.parametrize("mutation", [
    {"run_id": 43}, {"run_attempt": 2}, {"control_sha": "d" * 40},
    {"actual_source_sha": "e" * 40}, {"platform": {"system": "Linux"}},
    {"tests": []}, {"tests": [{"outcome": "skipped"}]}, {"phases": []},
])
def test_foreign_partial_empty_and_skipped_results_never_qualify(mutation):
    value = result()
    contract.validate_result(value, request(), run_id=42, control_sha="c" * 40, run_attempt=1)
    value.update(mutation)
    with pytest.raises(ValueError):
        contract.validate_result(value, request(), run_id=42, control_sha="c" * 40, run_attempt=1)


@pytest.mark.parametrize("packet", list(contract.NATIVE_PACKETS))
@pytest.mark.parametrize("mutation", [
    {}, {"tests": []}, {"tests": [{"name": "test_public_embody", "outcome": "skipped"}]},
    {"tests": [{"class": "foreign", "name": "test_public_embody", "outcome": "passed"}]},
    {"tests": [{"class": "not_test_native_seed_application",
                "name": "test_public_embody", "outcome": "passed"}]},
    {"tests": [{"class": "native.test_native_seed_application",
                "name": "test_public_embody", "outcome": "passed"}] * 2},
    {"native_prerequisites": {"copilot": "1.0.106", "psmux": "3.3.8"}},
    {"phases": []},
])
def test_native_result_requires_actual_caller_and_pinned_prerequisites(mutation, packet):
    value = result()
    expected = request()
    expected["selection"] = {"kind": "packet", "name": packet}
    _, module, case, phase = contract.NATIVE_PACKETS[packet]
    value.update(
        request=expected, native_prerequisites=contract.NATIVE_VERSIONS,
        tests=[{"class": "native." + module, "name": case, "outcome": "passed"}],
        phases=[{"name": name, "status": "passed"} for name in (
            "source-and-selection", "native-prerequisites",
            phase, "test-runtime",
        )],
    )
    value.update(mutation)
    if mutation:
        with pytest.raises(ValueError):
            contract.validate_result(value, expected, run_id=42, control_sha="c" * 40, run_attempt=1)
    else:
        contract.validate_result(value, expected, run_id=42, control_sha="c" * 40, run_attempt=1)


@pytest.mark.parametrize("caller,other", [("copilot", "embody"), ("embody", "copilot")])
def test_worktrees_packets_cannot_qualify_the_other_public_caller(caller, other):
    value, expected = result(), request()
    expected["selection"] = {"kind": "packet", "name": "native-worktrees-" + caller}
    _, module, _, phase = contract.NATIVE_PACKETS[expected["selection"]["name"]]
    value.update(
        request=expected, native_prerequisites=contract.NATIVE_VERSIONS,
        tests=[{"class": "native." + module, "name": "test_public_" + other, "outcome": "passed"}],
        phases=[{"name": name, "status": "passed"} for name in (
            "source-and-selection", "native-prerequisites", phase, "test-runtime",
        )],
    )
    with pytest.raises(ValueError, match="native caller"):
        contract.validate_result(value, expected, run_id=42, control_sha="c" * 40, run_attempt=1)


@pytest.mark.parametrize("mutation", [
    {}, {"tests": []}, {"tests": [{"outcome": "skipped"}]}, {"phases": []},
    {"tests": [{"class": "other", "name": contract.MCP_LIFETIME_CASE, "outcome": "passed"}]},
    {"tests": [{"class": "test_custody_native_lifetime", "name": "other", "outcome": "passed"}]},
])
def test_mcp_lifetime_result_requires_exact_functional_case_and_all_owner_phases(mutation):
    value, expected = result(), request()
    expected["selection"] = {"kind": "packet", "name": contract.MCP_LIFETIME_PACKET}
    value.update(
        request=expected,
        tests=[{"class": "test_custody_native_lifetime",
                "name": contract.MCP_LIFETIME_CASE, "outcome": "passed"}],
        phases=[{"name": name, "status": "passed"} for name in (
            "source-and-selection", "prepare-mcp-test-runtime",
            contract.MCP_LIFETIME_PACKET, "test-runtime",
        )],
    )
    value.update(mutation)
    if mutation:
        with pytest.raises(ValueError):
            contract.validate_result(value, expected, run_id=42, control_sha="c" * 40, run_attempt=1)
    else:
        contract.validate_result(value, expected, run_id=42, control_sha="c" * 40, run_attempt=1)


@pytest.mark.parametrize("native_exit", [0, 1])
def test_mcp_lifetime_execution_uses_owned_native_lane_without_cli_or_desktop_prerequisites(
    tmp_path, monkeypatch, native_exit,
):
    root, output = tmp_path / "candidate", tmp_path / "evidence"
    root.mkdir()
    monkeypatch.setattr(worker.platform, "system", lambda: "Windows")
    monkeypatch.setattr(worker, "Transport", lambda *_: SimpleNamespace(
        git=lambda *_: SimpleNamespace(stdout="b" * 40),
    ))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "-c" in argv:
            return SimpleNamespace(returncode=0, stdout='{"system":"Windows"}', stderr="")
        assert argv[2:] == ["agent-mcp", "--prepare-only"]
        return SimpleNamespace(returncode=0, stdout="prepared", stderr="")

    def lifetime(candidate, evidence):
        assert candidate == root and evidence == output
        if native_exit:
            raise ValueError("native owner cleanup unconfirmed")
        (output / "junit").mkdir()
        (output / "junit/actual.xml").write_text(
            '<testsuite><testcase classname="test_custody_native_lifetime" '
            f'name="{contract.MCP_LIFETIME_CASE}"/></testsuite>', encoding="utf-8",
        )

    monkeypatch.setitem(sys.modules, "agent_procutil.owned_process", SimpleNamespace(run_caller_owned=run))
    monkeypatch.setattr(worker, "mcp_lifetime", lifetime)
    monkeypatch.setattr(worker, "native_prerequisites", lambda *_a, **_k: pytest.fail("unrelated CLI prerequisites"))
    value = request()
    value["selection"] = {"kind": "packet", "name": contract.MCP_LIFETIME_PACKET}
    actual = worker.execute(root, value, output)
    assert actual["outcome"] == ("failed" if native_exit else "qualified")
    assert actual["phases"][2]["name"] == contract.MCP_LIFETIME_PACKET
    assert "native_prerequisites" not in actual
    assert len(calls) == (1 if native_exit else 2)


@pytest.mark.parametrize("outcome", ["passed", "skipped", "empty", "missing-evidence", "foreign"])
def test_desktop_packet_requires_exact_case_and_independent_calibration(
    tmp_path, monkeypatch, outcome,
):
    from test_windows_qualification_desktop import evidence

    root, output = tmp_path / "candidate", tmp_path / "evidence"
    root.mkdir()
    monkeypatch.setattr(worker.platform, "system", lambda: "Windows")
    monkeypatch.setattr(worker, "Transport", lambda *_: SimpleNamespace(
        git=lambda *_: SimpleNamespace(stdout="b" * 40),
    ))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if "-c" in argv:
            assert argv[0] == sys.executable
            return SimpleNamespace(returncode=0, stdout='{"system":"Windows"}', stderr="")
        assert argv[2:] == ["desktop", "--candidate", str(root), "--output", str(output)]
        assert kwargs["timeout"] == 90
        (output / "junit").mkdir()
        case = (
            '<testcase classname="tools.test_windows_qualification_desktop_native" '
            f'name="{contract.DESKTOP_CASE}">'
            + ('<skipped/>' if outcome == "skipped" else "") + "</testcase>"
        )
        (output / "junit/actual.xml").write_text(
            "<testsuite>" + ("" if outcome == "empty" else case) + "</testsuite>",
            encoding="utf-8",
        )
        if outcome != "missing-evidence":
            (output / "packet").mkdir()
            report = evidence()
            if outcome == "foreign":
                report["nonce"] = "d" * 32
            (output / "packet/desktop-capability.json").write_text(json.dumps(report), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="actual desktop capture", stderr="")

    monkeypatch.setitem(sys.modules, "agent_procutil.owned_process", SimpleNamespace(run_caller_owned=run))
    monkeypatch.setattr(worker, "native_prerequisites", lambda *_a, **_k: pytest.fail("unrelated native preparation"))
    value = request()
    value["selection"] = {"kind": "packet", "name": contract.DESKTOP_PACKET}
    actual = worker.execute(root, value, output)
    assert actual["outcome"] == {
        "passed": "qualified", "skipped": "incomplete", "empty": "incomplete",
        "missing-evidence": "failed", "foreign": "failed",
    }[outcome]
    assert [row["name"] for row in actual["phases"]] == [
        "source-and-selection", contract.DESKTOP_PACKET, "test-runtime",
    ]
    assert len(calls) == 2 and "native_prerequisites" not in actual


def test_desktop_packet_preserves_normal_caller_containment_and_budgets(tmp_path, monkeypatch):
    import plugin_test_containment as containment

    root, output, sandbox = tmp_path / "candidate", tmp_path / "evidence", tmp_path / "sandbox"
    sandbox.mkdir()
    for key, value in {
        "RUNNER_TEMP": str(tmp_path), "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.setattr(worker.platform, "system", lambda: "Windows")
    monkeypatch.setattr(containment, "TestSandbox", lambda **_: nullcontext(str(sandbox)))
    calls = []

    def contained(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["limits"] == containment.Limits(wall_seconds=60)
        assert "native_application" not in kwargs and "qualification" not in kwargs
        assert kwargs["env"]["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert kwargs["env"]["WINDOWS_QUALIFICATION_DESKTOP"] == "1"
        assert kwargs["env"]["WINDOWS_QUALIFICATION_DESKTOP_REQUEST"] == "a" * 32
        assert kwargs["env"]["WINDOWS_QUALIFICATION_DESKTOP_OUTPUT"] == str(output / "packet")
        assert kwargs["sandbox"] == sandbox and kwargs["cwd"] == root
        assert "--timeout=50" in argv and argv[argv.index("-k") + 1] == contract.DESKTOP_CASE
        return 0

    monkeypatch.setattr(containment, "run_contained", contained)
    assert worker.desktop_tests(root, output, "a" * 32) == 0
    assert len(calls) == 1
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "self-hosted")
    with pytest.raises(ValueError, match="disposable hosted owner"):
        worker.desktop_tests(root, tmp_path / "refused", "a" * 32)
    assert len(calls) == 1 and not (tmp_path / "refused").exists()


@pytest.mark.parametrize("selected,message", [
    (focused(), "desktop worker requires its explicit named packet"),
    ({"kind": "packet", "name": contract.DESKTOP_PACKET},
     "desktop capability requires its explicit disposable hosted owner"),
])
def test_actual_desktop_controller_refuses_before_output_or_native_access(tmp_path, selected, message):
    value, payload = request(), event()
    value["selection"] = selected
    payload["inputs"]["request"] = contract.canonical(value)
    event_path, output = tmp_path / "event.json", tmp_path / "not-created"
    event_path.write_text(contract.canonical(payload), encoding="utf-8")
    environment = {
        **os.environ, "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_ACTOR": "owner", "GITHUB_TRIGGERING_ACTOR": "owner",
        "GITHUB_REF": "refs/heads/main", "GITHUB_REF_PROTECTED": "true",
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted",
        "RUNNER_TEMP": str(tmp_path),
    }
    completed = subprocess.run(
        [sys.executable, "-B", str(Path(worker.__file__)), "desktop",
         "--candidate", str(tmp_path), "--output", str(output)],
        env=environment, capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert completed.returncode != 0 and message in completed.stderr
    assert not output.exists()


def test_mcp_lifetime_retains_containment_until_its_canonical_native_owner(tmp_path, monkeypatch):
    import _admission_protocol
    import plugin_test_cache
    import plugin_test_containment
    import plugin_test_resources

    environment = tmp_path / "prepared"
    python = environment / "Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    released = []
    monkeypatch.setattr(plugin_test_cache, "ordinary_cache_root", lambda _: environment)
    monkeypatch.setattr(plugin_test_resources, "venv_root", lambda _: tmp_path / "venvs")
    monkeypatch.setattr(_admission_protocol, "acquire", lambda *_: SimpleNamespace(
        release=lambda: released.append(True),
    ))
    monkeypatch.setattr(plugin_test_containment, "TestSandbox", lambda **_: nullcontext(str(sandbox)))

    def run(argv, **kwargs):
        assert kwargs["native_application"] is True
        assert kwargs["limits"] == plugin_test_containment.Limits(wall_seconds=900)
        assert kwargs["env"]["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert kwargs["env"]["AGENT_MCP_NATIVE_LIFETIME"] == "1"
        assert os.environ["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert "-k" in argv and argv[argv.index("-k") + 1] == contract.MCP_LIFETIME_CASE
        assert argv[0] == str(python)
        raise plugin_test_containment.ContainmentError("hosted owner refused")

    monkeypatch.setattr(plugin_test_containment, "run_contained", run)
    with pytest.raises(plugin_test_containment.ContainmentError, match="hosted owner refused"):
        worker.mcp_lifetime(tmp_path, tmp_path / "out")
    assert released == [True]


@pytest.mark.parametrize("prerequisites", [True, False])
@pytest.mark.parametrize("preparation", ["success", "failure", "cancelled", "skipped", "unrecognized"])
@pytest.mark.parametrize("packet", list(contract.NATIVE_PACKETS))
def test_native_execution_gates_consumer_and_sanitizes_uploaded_files(
    tmp_path, monkeypatch, prerequisites, preparation, packet,
):
    root, output = tmp_path / "candidate", tmp_path / "evidence"
    root.mkdir()
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://feed.example.invalid/pypi/simple/")
    monkeypatch.setenv("WINDOWS_QUALIFICATION_NATIVE_PREPARATION", preparation)
    monkeypatch.setattr(worker.platform, "system", lambda: "Windows")
    monkeypatch.setattr(worker, "Transport", lambda *args: SimpleNamespace(
        git=lambda *args: SimpleNamespace(stdout="b" * 40),
    ))
    calls = []
    plugin, module, case, phase = contract.NATIVE_PACKETS[packet]

    def run(argv, **kwargs):
        calls.append(argv)
        if "-c" in argv:
            return SimpleNamespace(returncode=0, stdout='{"python":"3.12","system":"Windows"}', stderr="")
        (output / "junit").mkdir()
        (output / "junit/actual.xml").write_text(
            f'<testsuite><testcase classname="native.{module}" '
            f'name="{case}"><system-out>https://feed.example.invalid/pypi/simple/'
            ' https://download.example.invalid/wheel?fixture=example'
            '</system-out></testcase></testsuite>', encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="selected feed.example.invalid", stderr="")

    def prepare(owned, *, require_feed):
        assert require_feed is (plugin == "agent-worktrees")
        if not prerequisites:
            raise ValueError("unavailable feed.example.invalid")
        return dict(contract.NATIVE_VERSIONS)

    monkeypatch.setitem(sys.modules, "agent_procutil.owned_process", SimpleNamespace(run_caller_owned=run))
    monkeypatch.setattr(worker, "native_prerequisites", prepare)
    if packet == "native-worktrees-caller-jobs":
        import windows_qualification_lifetime

        monkeypatch.setattr(
            windows_qualification_lifetime, "worktrees_lifetime",
            lambda root, output, *, prepare: prepare(worker.native_command(root, output, packet), 1200),
        )
    value = request()
    value["selection"] = {"kind": "packet", "name": packet}
    actual = worker.execute(root, value, output)
    qualified = prerequisites and preparation == "success"
    assert actual["outcome"] == ("qualified" if qualified else "failed")
    assert bool(calls) is qualified
    if qualified:
        assert calls[0][2] == plugin
        assert phase in {row["name"] for row in actual["phases"]}
    if preparation != "success":
        assert actual["phases"][-1]["name"] == "native-prerequisites"
        assert actual["phases"][-1]["status"] == "failed"
        assert actual["tests"] == []
        status = preparation if preparation != "unrecognized" else "unknown"
        assert actual["error"].endswith(f"({status})")
        contract.validate_result(
            actual, value, run_id=actual["run_id"], control_sha=actual["control_sha"],
            run_attempt=actual["run_attempt"],
        )
    for path in output.rglob("*"):
        if path.is_file():
            assert "feed.example.invalid" not in path.read_text("utf-8")
            assert "download.example.invalid" not in path.read_text("utf-8")


def test_native_sanitization_handles_interrupted_workers_and_oversized_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://feed.example.invalid/simple/")
    log = tmp_path / "phase-3.stderr.log"
    log.write_bytes(b"feed.example.invalid https://download.example.invalid/wheel \xc3")
    oversized = tmp_path / "oversized.xml"
    oversized.write_bytes(b"x" * (16 * 1024 * 1024 + 1))
    assert worker.sanitize_native_files(tmp_path) is True
    assert not oversized.exists()
    assert log.read_text("utf-8") == "[configured-feed] [redacted-url] \ufffd"
    assert worker.sanitize_native_files(tmp_path) is False


def fake_client(tmp_path, monkeypatch):
    client = object.__new__(client_module.Client)
    client.root = tmp_path
    client.repository = "owner/repository"
    client.state_root = tmp_path
    directory = tmp_path / request()["request_id"]
    directory.mkdir()
    state = {
        "repository": client.repository, "request": request(), "phase": "prepared",
        "provider": {"branch": "main", "owner": "owner", "control_sha": "c" * 40},
        "created_at": 1,
    }
    client.save(directory, state)
    return client, directory, state


def test_lost_dispatch_response_is_durable_and_resume_does_not_dispatch(tmp_path, monkeypatch):
    client, directory, state = fake_client(tmp_path, monkeypatch)
    calls = []

    def failed(*args):
        calls.append(args)
        raise TimeoutError("lost response")
    monkeypatch.setattr(client, "gh", failed)
    with pytest.raises(RuntimeError, match="effect unknown"):
        client.dispatch(directory, state)
    saved = json.loads((directory / "state.json").read_text("utf-8"))
    assert saved["phase"] == "dispatch-unknown"
    monkeypatch.setattr(client, "status", lambda identifier: saved)
    assert client.resume(saved["request"]["request_id"]) == saved
    assert len(calls) == 1
    assert json.loads((directory / "dispatch.json").read_text("utf-8"))["inputs"]["request_id"] == "a" * 32


def test_status_correlates_request_not_latest_success(tmp_path, monkeypatch):
    client, directory, state = fake_client(tmp_path, monkeypatch)
    run = {
        "display_title": "winqual " + "a" * 32, "actor": {"login": "owner"},
        "head_branch": "main", "path": ".github/workflows/" + contract.WORKFLOW,
        "id": 42, "status": "completed", "conclusion": "failure",
        "head_sha": "c" * 40, "run_attempt": 1, "html_url": "https://example.invalid/run",
    }
    other = {**run, "display_title": "winqual " + "d" * 32, "conclusion": "success"}
    monkeypatch.setattr(client, "api", lambda endpoint: {"workflow_runs": [other, run]})
    actual = client.status(state["request"]["request_id"])
    assert actual["run"]["id"] == 42 and actual["run"]["conclusion"] == "failure"
    client.save(directory, state)
    monkeypatch.setattr(client, "api", lambda endpoint: {"workflow_runs": [run, copy.deepcopy(run)]})
    with pytest.raises(ValueError, match="duplicate"):
        client.status(state["request"]["request_id"])


@pytest.mark.parametrize("unsupported", [
    "version", "selector", "packet", "inflight", "observation-loss",
    "startup-seed", "startup-no-seed", "startup-cold-resume", "copilot", "caller-jobs",
    "manager-no-mux", "manager-mux",
    "workflow", "unprotected", "desktop",
])
def test_actual_provider_support_refuses_before_staging_or_creating_request_state(
    tmp_path, monkeypatch, unsupported,
):
    client = object.__new__(client_module.Client)
    client.root, client.state_root, client.repository = tmp_path, tmp_path, "owner/repository"
    effects = []
    packet = {
        "packet": "native-driver-auth-wait", "inflight": "native-driver-inflight",
        "observation-loss": "native-driver-observation-loss",
        "startup-seed": "native-driver-startup-seed", "startup-no-seed": "native-driver-startup-no-seed",
        "startup-cold-resume": "native-driver-startup-cold-resume",
        "copilot": "native-worktrees-copilot",
        "caller-jobs": "native-worktrees-caller-jobs",
        "manager-no-mux": "native-worktrees-manager-no-mux",
        "manager-mux": "native-worktrees-manager-mux",
        "desktop": contract.DESKTOP_PACKET,
    }.get(unsupported)
    selected = (
        {"kind": "packet", "name": packet} if packet else focused()
    )
    files = list(contract.DESKTOP_FILES) if packet == contract.DESKTOP_PACKET else [
        "plugins/agent-worktrees/tests/native/test_native_seed_application.py",
        "plugins/agent-worktrees/tests/native/test_manager_application_launch.py",
        "plugins/agent-worktrees/tests/native/test_headed_worktrees_copilot.py",
    ] if packet in {
        "native-worktrees-copilot", "native-worktrees-caller-jobs", "native-worktrees-manager-no-mux",
        "native-worktrees-manager-mux",
    } else [
        f"plugins/agent-remote-driver/tests/native/{contract.NATIVE_PACKETS[packet][1]}.py",
        "plugins/agent-remote-driver/tests/headed_mux_fixture.py",
    ] if packet else ["plugins/sample/tests/test_sample.py"]
    client.transport = SimpleNamespace(
        git=lambda *args: SimpleNamespace(
            stdout=(
                "b" * 40 if args[0] == "rev-parse"
                else "\n".join("100644 blob " + "c" * 40 + "\t" + file for file in files)
            ),
        ),
        scoped_credentials=lambda owner: effects.append("credential-selection"),
        append=lambda *args: effects.append("push"),
    )
    support = {
        "request_versions": [] if unsupported == "version" else [1],
        "result_versions": [1], "workflow": contract.WORKFLOW,
        "transport_ref": contract.TRANSPORT_REF, "transport_format": contract.TRANSPORT_MARKER,
        "selection_kinds": [] if unsupported == "selector" else ["plugin", "packet"],
        "packets": ["bridge-http52", "native-worktrees-embody"],
    }

    def api(endpoint):
        if endpoint == "user":
            return {"login": "owner"}
        if endpoint == "repos/owner/repository":
            return {"private": False, "owner": {"type": "User", "login": "owner"}, "default_branch": "main"}
        if "/branches/" in endpoint:
            return {"protected": unsupported != "unprotected"}
        if "/commits/" in endpoint:
            return {"sha": "c" * 40}
        if "/contents/" in endpoint:
            return {"content": base64.b64encode(contract.canonical(support).encode()).decode()}
        return {
            "state": "disabled_manually" if unsupported == "workflow" else "active",
            "path": ".github/workflows/" + contract.WORKFLOW,
        }
    monkeypatch.setattr(client, "api", api)
    with pytest.raises(
        ValueError, match="protected controller does not support" if packet else None,
    ):
        client.submit(selected, "HEAD")
    assert effects == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("packet", [
    "native-driver-auth-wait", "native-driver-inflight", "native-driver-observation-loss",
    "native-driver-startup-seed", "native-driver-startup-no-seed", "native-driver-startup-cold-resume",
    contract.DESKTOP_PACKET,
])
@pytest.mark.parametrize("missing", [True, False])
def test_receiver_packet_requires_its_candidate_fixture_before_provider_or_transport(
    tmp_path, monkeypatch, missing, packet,
):
    client = object.__new__(client_module.Client)
    client.root = tmp_path
    files = list(contract.DESKTOP_FILES) if packet == contract.DESKTOP_PACKET else [
        f"plugins/agent-remote-driver/tests/native/{contract.NATIVE_PACKETS[packet][1]}.py",
        "plugins/agent-remote-driver/tests/headed_mux_fixture.py",
    ]

    def git(*args):
        if args[0] == "rev-parse":
            return SimpleNamespace(stdout="b" * 40)
        assert args == ("ls-tree", "b" * 40, "--", *files)
        return SimpleNamespace(stdout="\n".join(
            "100644 blob " + "c" * 40 + "\t" + file for file in files[1 if missing else 0:]
        ))

    client.transport = SimpleNamespace(git=git)

    def preflight(selected):
        assert selected == {"kind": "packet", "name": packet}
        raise ValueError("provider preflight reached")

    monkeypatch.setattr(client, "preflight", preflight)
    with pytest.raises(ValueError, match="missing" if missing else "provider preflight reached"):
        client.submit({"kind": "packet", "name": packet}, "HEAD")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("visible", [True, False])
def test_interrupted_transport_resumes_only_original_confirmed_envelope(tmp_path, monkeypatch, visible):
    client, directory, state = fake_client(tmp_path, monkeypatch)
    state.update(phase="transport-unknown", envelope_sha="e" * 40)
    client.save(directory, state)
    monkeypatch.setattr(client, "preflight", lambda selected: state["provider"])
    client.transport = SimpleNamespace(
        scoped_credentials=lambda owner: None,
        observe=lambda *args, **kwargs: "e" * 40 if visible else None,
        validate_tip=lambda tip: None,
        git=lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    dispatches = []
    monkeypatch.setattr(client, "dispatch", lambda directory, value: dispatches.append(value) or value)
    if visible:
        actual = client.resume(state["request"]["request_id"])
        assert actual["request"]["envelope_sha"] == "e" * 40
        assert actual["request"]["request_id"] == "a" * 32
        assert len(dispatches) == 1
    else:
        with pytest.raises(ValueError, match="remains unknown"):
            client.resume(state["request"]["request_id"])
        assert dispatches == []


def test_new_transport_attempt_cannot_inherit_old_rejection(tmp_path, monkeypatch):
    client, directory, state = fake_client(tmp_path, monkeypatch)
    state.update(
        phase="transport-attempt", envelope_sha="e" * 40, previous_tip="d" * 40,
        transport_failure={"returncode": 1, "stdout": "old rejection", "stderr": ""},
    )
    original = copy.deepcopy(state)

    def interrupted(candidate, persist):
        persist({
            "phase": "transport-attempt", "envelope_sha": "f" * 40,
            "previous_tip": "e" * 40, "transport_failure": None,
        })
        raise TimeoutError("push response unavailable")

    client.transport = SimpleNamespace(append=interrupted)
    with pytest.raises(RuntimeError, match="push response unavailable"):
        client.append_transport(directory, state)
    saved = client.load(state["request"]["request_id"])[1]
    assert saved["transport_failure"] is None
    assert saved["transport_history"] == [{
        key: original[key] for key in
        ("envelope_sha", "previous_tip", "phase", "transport_failure")
    }]
    assert saved["envelope_sha"] == "f" * 40
    assert saved["request"] == original["request"]


def test_confirmed_envelope_cannot_bypass_a_retained_history_anchor(tmp_path, monkeypatch):
    client, directory, state = fake_client(tmp_path, monkeypatch)
    state.update(
        phase="transport-unknown", envelope_sha="e" * 40, transport_history_anchor="d" * 40,
    )
    client.save(directory, state)
    monkeypatch.setattr(client, "preflight", lambda selected: state["provider"])
    client.transport = SimpleNamespace(
        scoped_credentials=lambda owner: None, observe=lambda *args, **kwargs: "e" * 40,
        git=lambda *args, **kwargs: SimpleNamespace(returncode=int(args[2] == "d" * 40)),
    )
    monkeypatch.setattr(client, "dispatch", lambda *args: pytest.fail("rewritten history dispatch"))
    with pytest.raises(ValueError, match="history changed"):
        client.resume(state["request"]["request_id"])
    assert client.load(state["request"]["request_id"])[1] == state


@pytest.mark.parametrize("changed", [
    "candidate", "missing-history", "missing-first-history", "rewritten-history",
])
def test_known_rejection_does_not_replace_mismatched_original_or_shared_history(
    tmp_path, monkeypatch, changed,
):
    client, directory, state = fake_client(tmp_path, monkeypatch)
    envelope = "e" * 40
    previous = None if changed == "missing-first-history" else "d" * 40
    state.update(
        phase="transport-attempt", envelope_sha=envelope, previous_tip=previous,
        transport_failure={"returncode": 1, "stderr": "", "stdout": (
            f"!\t{envelope}:{contract.TRANSPORT_REF}\t[rejected] (fetch first)\n"
        )},
    )
    client.save(directory, state)
    monkeypatch.setattr(client, "preflight", lambda selected: state["provider"])
    effects = []
    client.transport = SimpleNamespace(
        scoped_credentials=lambda owner: None,
        observe=lambda *args, **kwargs: (
            None if changed in {"missing-history", "missing-first-history"} else "f" * 40
        ),
        validate_tip=lambda envelope: None,
        git=lambda *args, **kwargs: SimpleNamespace(
            returncode=(
                0 if args[0] == "merge-base" and args[2] == previous
                and changed != "rewritten-history" else 1
            ),
            stdout=(previous + " " if previous else "") + (
                "a" * 40 if changed == "candidate" else state["request"]["source_sha"]
            ),
        ),
        append=lambda *args: effects.append("push"),
    )
    monkeypatch.setattr(client, "dispatch", lambda *args: effects.append("dispatch"))
    with pytest.raises(ValueError, match="ancestry differs|history changed"):
        client.resume(state["request"]["request_id"])
    assert effects == []
    assert client.load(state["request"]["request_id"])[1] == state


@pytest.mark.parametrize("foreign", [False, True])
def test_result_download_uses_original_run_attempt_and_checks_manifest(tmp_path, monkeypatch, foreign):
    client, _, state = fake_client(tmp_path, monkeypatch)
    state["run"] = {
        "id": 42, "status": "completed", "conclusion": "success",
        "head_sha": "c" * 40, "run_attempt": 1,
    }
    monkeypatch.setattr(client, "status", lambda identifier: state)
    monkeypatch.setattr(client, "api", lambda endpoint: {"artifacts": [{
        "id": 8, "name": "winqual-" + "a" * 32 + "-1",
        "expired": False, "size_in_bytes": 1000,
    }]})
    downloads = []

    def download(*args):
        downloads.append(args)
        value = result()
        if foreign:
            value["request"]["source_sha"] = "d" * 40
        output = Path(args[args.index("--dir") + 1])
        (output / "result.json").write_text(contract.canonical(value), encoding="utf-8")
        return ""
    monkeypatch.setattr(client, "gh", download)
    if foreign:
        with pytest.raises(ValueError, match="identity mismatch"):
            client.result("a" * 32)
    else:
        assert client.result("a" * 32)["outcome"] == "qualified"
        assert client.result("a" * 32)["outcome"] == "qualified"
        assert len(downloads) == 1
    assert downloads[0][:3] == ("run", "download", "42")
    assert downloads[0][downloads[0].index("--name") + 1] == "winqual-" + "a" * 32 + "-1"


@pytest.mark.parametrize("outcome,exit_code", [("qualified", 0), ("failed", 1), ("incomplete", 1)])
def test_result_cli_does_not_return_success_for_unqualified_evidence(
    tmp_path, monkeypatch, capsys, outcome, exit_code,
):
    monkeypatch.setattr(client_module, "Client", lambda *args: SimpleNamespace(
        result=lambda identifier: {"outcome": outcome},
        lock=lambda identifier: nullcontext(),
    ))
    actual = client_module.main([
        "--worktrees", str(tmp_path / "catalog-command"),
        "--repo", "owner/repository", "result", "--request-id", "a" * 32,
    ])
    assert actual == exit_code
    assert json.loads(capsys.readouterr().out)["outcome"] == outcome


def test_invalid_candidate_workspace_is_a_clean_cli_refusal(tmp_path, monkeypatch, capsys):
    def reject(*args):
        raise ValueError("candidate origin differs from the requested public repository")
    monkeypatch.setattr(client_module, "Client", reject)
    assert client_module.main([
        "--worktrees", str(tmp_path / "catalog-command"),
        "--repo", "owner/repository", "status", "--request-id", "a" * 32,
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "candidate origin differs" in captured.err
    assert "Traceback" not in captured.err


def test_same_request_operations_exclude_duplicate_resume_but_other_requests_remain_free(tmp_path, monkeypatch):
    client, directory, state = fake_client(tmp_path, monkeypatch)
    other = tmp_path / "other-request"
    other.mkdir()
    with client.lock(state["request"]["request_id"]):
        from single_instance_lease import OperationLockedError
        with pytest.raises(OperationLockedError):
            with client.lock(state["request"]["request_id"]):
                pytest.fail("the same request cannot have two mutation owners")
        with client_module.request_lock(other):
            assert (directory / "state.json").is_file()


def test_tool_entry_uses_candidate_environment_schema_not_controller_schema(tmp_path, monkeypatch):
    root = tmp_path / "candidate"
    tools = root / "tools"
    tools.mkdir(parents=True)
    (tools / "test_sample.py").write_text("def test_sample(): pass\n", encoding="utf-8")
    environment_source = Path(worker.__file__).with_name("plugin_test_environment.py").read_text("utf-8")
    (tools / "plugin_test_environment.py").write_text(
        environment_source
        + '\n_ROOT_ENV["QUALIFICATION_FUTURE_ROOT"] = "future-root"\n'
        + "ROOT_ENV_NAMES = tuple(_ROOT_ENV)\n",
        encoding="utf-8",
    )
    value = request()
    value["selection"] = {
        "kind": "tools", "files": ["tools/test_sample.py"], "k": "",
        "full": False, "explicit_tiers": False,
    }
    payload = event()
    payload["inputs"]["request"] = contract.canonical(value)
    event_path = tmp_path / "event.json"
    event_path.write_text(contract.canonical(payload), encoding="utf-8")
    for name, content in {
        "GITHUB_EVENT_PATH": str(event_path), "GITHUB_ACTOR": "owner",
        "GITHUB_TRIGGERING_ACTOR": "owner", "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_PROTECTED": "true",
    }.items():
        monkeypatch.setenv(name, content)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.delitem(sys.modules, "plugin_test_environment", raising=False)
    monkeypatch.setattr(sys, "argv", [
        str(worker.__file__), "tools", "--candidate", str(root), "--output", str(tmp_path / "out"),
    ])

    def candidate_tests(candidate, selected, output):
        from plugin_test_environment import isolated_environment
        sandbox = tmp_path / "sandbox"
        environment = isolated_environment(os.environ, sandbox)
        assert Path(environment["QUALIFICATION_FUTURE_ROOT"]).is_relative_to(sandbox)
        assert candidate == root and selected == value["selection"]
        return 0
    monkeypatch.setattr(worker, "tool_tests", candidate_tests)
    assert worker.main() == 0


@pytest.mark.portfolio_tier("T2")
@pytest.mark.parametrize("explicit", [False, True])
def test_actual_tool_consumer_enforces_explicit_tiers(tmp_path, monkeypatch, explicit):
    import plugin_test_containment as containment
    root = tmp_path / "candidate"
    tools = root / "tools"
    tools.mkdir(parents=True)
    (tools / "test_tiers.py").write_text(
        "from pathlib import Path\n"
        "import pytest\n"
        "@pytest.mark.portfolio_tier('T3')\n"
        "def test_native_tier():\n"
        "    Path(__file__).with_name('t3-ran').write_text('executed')\n"
        "@pytest.mark.portfolio_tier('T4')\n"
        "def test_external_tier():\n"
        "    Path(__file__).with_name('t4-ran').write_text('executed')\n",
        encoding="utf-8",
    )
    run = containment.run_contained

    def fixture_dependencies(argv, **kwargs):
        # Supply the real policy module to a tiny candidate fixture, without
        # changing the worker's argv or bypassing pytest's plugin loading.
        kwargs["env"]["PYTHONPATH"] = os.pathsep.join((str(tools), str(worker.ROOT / "tools")))
        return run(argv, **kwargs)
    monkeypatch.setattr(containment, "run_contained", fixture_dependencies)
    output = tmp_path / "evidence"
    output.mkdir()
    assert worker.tool_tests(root, {
        "kind": "tools", "files": ["tools/test_tiers.py"], "k": "",
        "full": False, "explicit_tiers": explicit,
    }, output) == 0
    cases = worker.junit_cases(output / "junit")
    assert len(cases) == 2
    assert {case["outcome"] for case in cases} == {"passed" if explicit else "skipped"}
    assert (tools / "t3-ran").exists() is explicit
    assert (tools / "t4-ran").exists() is explicit


def test_manual_workflow_never_adds_required_ci_or_candidate_write_credentials():
    value = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/windows-qualification.yml").read_text("utf-8"),
    )
    assert set(value.get("on", value.get(True))) == {"workflow_dispatch"}
    assert "concurrency" not in value and value["permissions"] == {}
    assert value["jobs"]["windows"]["needs"] == "admit"
    assert value["jobs"]["admit"]["outputs"]["native"] == "${{ steps.admit.outputs.native }}"
    upload = value["jobs"]["windows"]["steps"][-1]
    assert upload["if"] == (
        "${{ always() && (needs.admit.outputs.native != 'true' || "
        "steps.sanitize.outcome == 'success') }}"
    )
    for job in value["jobs"].values():
        assert job["permissions"] == {"contents": "read"}
        assert "matrix" not in job.get("strategy", {})
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False
            if "secrets." in json.dumps(step):
                assert step["name"] in {
                    "Execute typed selection through canonical bounded owners",
                    "Sanitize native evidence independently after worker interruption",
                }
                assert set(step["env"]) == (
                    {"UV_DEFAULT_INDEX"} if step["name"].startswith("Sanitize") else
                    {"UV_DEFAULT_INDEX", "WINDOWS_QUALIFICATION_NATIVE_PREPARATION"}
                )
                assert step["env"]["UV_DEFAULT_INDEX"] == (
                    "${{ (needs.admit.outputs.packet == 'native-worktrees-embody' || "
                    "needs.admit.outputs.packet == 'native-worktrees-copilot' || "
                    "needs.admit.outputs.packet == 'native-worktrees-caller-jobs' || "
                    "needs.admit.outputs.packet == 'native-worktrees-manager-no-mux' || "
                    "needs.admit.outputs.packet == 'native-worktrees-manager-mux') && "
                    "secrets.WINDOWS_QUALIFICATION_INDEX_URL || '' }}"
                )
                if step["name"].startswith("Sanitize"):
                    assert step["if"] == (
                        "always() && needs.admit.outputs.native == 'true'"
                    )
            if step.get("id") == "native":
                assert step["if"] == (
                    "needs.admit.outputs.native == 'true'"
                )
    promotion = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/validate-and-promote.yml").read_text("utf-8"),
    )
    steps = promotion["jobs"]["native-resume-windows"]["steps"]
    helper = next(step for step in steps if step.get("with", {}).get("path") == "native-control")
    assert helper["with"]["ref"] == "${{ github.workflow_sha }}"
    assert helper["with"]["persist-credentials"] is False
    assert helper["with"]["sparse-checkout"] == "tools/download_pinned_native.ps1"
    acquire = next(step for step in steps if "download_pinned_native.ps1" in step.get("run", ""))
    assert r'.\native-control\tools\download_pinned_native.ps1' in acquire["run"]


@pytest.mark.parametrize("workflow", ["windows-qualification", "validate-and-promote"])
@pytest.mark.parametrize("responses,body_delay,budget,retry_after,expected_attempts", [
    ((504, 504, 200), 0, 120, None, 3), ((504, 504, 504), 0, 120, None, 3),
    ((404,), 0, 120, None, 1), ((200,), 2, 1, None, 1), ((504,), 0, 1, None, 1),
    ((429,), 0, 4, "15", 1), ((429,), 0, 4, "date", 1),
    # Resetting the timer per attempt would reach the final 200 instead.
    ((504, 504, 200), 2, 7, None, 2),
])
@pytest.mark.portfolio_tier("T2")
def test_pinned_native_download_retries_only_transient_failures(
    tmp_path, workflow, responses, body_delay, budget, retry_after, expected_attempts,
):
    from windows_qualification_transport import command
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell 7 is unavailable")
    source = Path(__file__).resolve().parents[1] / f".github/workflows/{workflow}.yml"
    definition = yaml.safe_load(source.read_text("utf-8"))
    job = "windows" if workflow == "windows-qualification" else "native-resume-windows"
    step = next(step for step in definition["jobs"][job]["steps"]
                if "download_pinned_native.ps1" in step.get("run", ""))
    script = re.sub(r"`[ \t]*\r?\n", " ", step["run"])
    line = next(line.strip() for line in script.splitlines()
                if "download_pinned_native.ps1" in line)
    argv = shlex.split(line, comments=True)
    helper = source.parents[2] / "tools/download_pinned_native.ps1"
    argv[argv.index("-OutFile") + 1] = str(tmp_path / "native archive.zip")
    argv[argv.index("-TimeoutSeconds") + 1] = str(budget)
    attempts = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status = responses[min(len(attempts), len(responses) - 1)]
            attempts.append(status)
            body = b"pinned archive" if status == 200 else b"download unavailable"
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            if retry_after:
                self.send_header(
                    "Retry-After", formatdate(time.time() + 15, usegmt=True)
                    if retry_after == "date" else retry_after,
                )
            self.end_headers()
            time.sleep(body_delay)
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    class Server(ThreadingHTTPServer):
        daemon_threads = False

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    archive = tmp_path / "native archive.zip"
    try:
        argv[argv.index("-Uri") + 1] = f"http://127.0.0.1:{server.server_port}/archive"
        started = time.monotonic()
        actual = command([
            pwsh, "-NoProfile", "-File", str(helper), *argv[2:],
        ], cwd=tmp_path, check=False)
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert attempts == list(responses[:expected_attempts])
    if budget < 120:
        assert elapsed < budget + 10, "acquisition exceeded its budget and PowerShell startup allowance"
    assert (actual.returncode == 0) is (responses[-1] == 200 and not body_delay)
    if actual.returncode == 0:
        assert archive.read_bytes() == b"pinned archive"
    else:
        assert not archive.exists()
