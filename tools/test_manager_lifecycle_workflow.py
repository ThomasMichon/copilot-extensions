"""The manual development lane never accepts partial lifecycle evidence."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import xml.etree.ElementTree as ET

import pytest
import yaml

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "installation-context-full.yml"
)


@pytest.mark.parametrize("outcome", [
    "passed", "failed", "skipped", "missing", "partial", "stale", "wrong-run", "runtime-claim",
    "requested-mismatch", "foreign-transport",
])
def test_payload_preflight_cannot_claim_runtime_or_accept_incomplete_evidence(
    tmp_path, monkeypatch, outcome,
):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(
        row for row in workflow["jobs"]["manager-lifecycle-windows"]["steps"]
        if row.get("id") == "suite-payloads"
    )
    names = ("agent-worktrees", "agent-machines")
    data = {
        "candidate_commit": "a" * 40,
        "identity": {"candidate_commit": "a" * 40, "run_id": 123, "run_attempt": 2},
        "profile": {
            "inventory": {name: {"version": "1.0"} for name in names},
            "repository_config": {"require_application_readiness": True},
        },
        "payloads": {
            name: {"version": "1.0", "manifest_sha256": "b" * 64} for name in names
        },
        "runtime_installation": "not-observed", "copilot_sha256": "c" * 64,
        "transport": {
            "repository": str(tmp_path / "transport.git"),
            "url": "http://127.0.0.1:12345/suite-transport.git",
            "commit": "d" * 40, "tree": "e" * 40,
        },
    }
    if outcome == "partial":
        del data["payloads"]["agent-machines"]
    elif outcome == "stale":
        data["candidate_commit"] = "d" * 40
    elif outcome == "wrong-run":
        data["identity"]["run_id"] = 122
    elif outcome == "runtime-claim":
        data["runtime_installation"] = "complete"
    elif outcome == "foreign-transport":
        data["transport"]["url"] = "http://example.invalid/suite-transport.git"

    def run(arguments, **limits):
        assert limits["timeout"] == 1200
        assert arguments[arguments.index("-k") + 1] == (
            "test_installed_suite_payload_closure_uses_the_real_cli"
        )
        reports = Path(arguments[arguments.index("--junit-dir") + 1])
        reports.mkdir(parents=True)
        document = ET.Element("testsuite")
        if outcome != "missing":
            case = ET.SubElement(
                document, "testcase", name="test_installed_suite_payload_closure_uses_the_real_cli",
            )
            if outcome == "skipped":
                ET.SubElement(case, "skipped")
            ET.SubElement(
                ET.SubElement(document, "properties"), "property",
                name="installed-manager-suite-payloads", value=json.dumps(data),
            )
        ET.ElementTree(document).write(reports / "group.xml")
        return subprocess.CompletedProcess(arguments, 1 if outcome == "failed" else 0, "", "")

    package = ModuleType("agent_procutil")
    process = ModuleType("agent_procutil.owned_process")
    process.run_caller_owned = run
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, process.__name__, process)
    monkeypatch.chdir(tmp_path)
    catalog = Path(".github/plugin/marketplace.json")
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({
        "plugins": [{"name": name, "version": "1.0"} for name in names],
    }))
    for key, value in {
        "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2",
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "REQUESTED_SOURCE": "d" * 40 if outcome == "requested-mismatch" else "a" * 40,
    }.items():
        monkeypatch.setenv(key, value)
    code = compile(step["run"], "<payload-preflight-workflow>", "exec")
    if outcome == "passed":
        exec(code, {})
    else:
        with pytest.raises(subprocess.CalledProcessError if outcome == "failed" else AssertionError):
            exec(code, {})


@pytest.mark.parametrize(
    "outcome",
    [
        "passed",
        "source-mismatch",
        "requested-mismatch",
        "failed",
        "skipped",
        "missing",
        "tty-only",
        "partial",
        "stale",
        "duplicate",
        "same-owner",
        "invalid-native",
    ],
)
def test_manual_lifecycle_requires_exact_source_and_complete_native_evidence(
    tmp_path,
    monkeypatch,
    outcome,
):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["manager-lifecycle-windows"]
    assert job["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'manager-lifecycle'"
    )
    checkout = job["steps"][0]["with"]
    assert checkout["ref"] == "${{ github.sha }}"
    assert checkout["persist-credentials"] is False
    step = next(value for value in job["steps"] if value.get("id") == "lifecycle")
    upload = job["steps"][-1]
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    calls = []

    def run(arguments, **limits):
        calls.append(arguments)
        assert limits["timeout"] <= 1200
        if arguments[0] == "git":
            value = (
                "b"
                if arguments[-1] == "HEAD^{tree}"
                else ("c" if outcome == "source-mismatch" else "a")
            )
            return subprocess.CompletedProcess(arguments, 0, value * 40 + "\n", "")
        assert arguments[1:3] == ["tools/run-plugin-tests.py", "worktree-manager"]
        assert "--allow-host-state" not in arguments
        for flag in ("--timeout", "--plugin-timeout", "--test-timeout"):
            assert arguments[arguments.index(flag) + 1] == "900"
        reports = Path(arguments[arguments.index("--junit-dir") + 1])
        reports.mkdir(parents=True)
        suite = ET.Element("testsuite")
        if outcome != "missing":
            case = ET.SubElement(
                suite,
                "testcase",
                name="test_installed_manager_creates_resumes_and_opens_one_real_cli",
            )
            if outcome == "skipped":
                ET.SubElement(case, "skipped")
        data = {
            "profile": "installed-core",
            "identity": {
                "candidate_commit": "a" * 40,
                "candidate_tree": "b" * 40,
                "source_commit": "a" * 40,
                "run_id": 123,
                "run_attempt": 2,
            },
            "installation": {
                name: {
                    "source_commit": "a" * 40,
                    "origins_verified": True,
                    "candidate_bytes_verified": True,
                }
                for name in ("manager", "engine")
            },
            **{
                name: True
                for name in (
                    "create",
                    "open_existing",
                    "detach_preserved",
                    "normal_exit",
                    "resume_continued",
                    "launch_refusal",
                )
            },
            "native_before_open": [100, 1000],
            "native_after_resume": [200, 2000],
            "elapsed_seconds": 500.0,
            "worktree": "example-worktree",
            "conversation": "example-conversation",
        }
        if outcome == "partial":
            data["resume_continued"] = False
        if outcome == "stale":
            data["identity"]["candidate_commit"] = "d" * 40
        if outcome == "same-owner":
            data["native_after_resume"] = data["native_before_open"]
        if outcome == "invalid-native":
            data["native_before_open"] = [True, 1000]
        encoded = json.dumps(data)
        if outcome == "duplicate":
            encoded = encoded[:-1] + ',"create":true}'
        ET.SubElement(
            ET.SubElement(suite, "properties"),
            "property",
            name=(
                "installed-manager-tty"
                if outcome == "tty-only"
                else "installed-manager-lifecycle"
            ),
            value=encoded,
        )
        ET.ElementTree(suite).write(reports / "group.xml")
        return subprocess.CompletedProcess(
            arguments, 17 if outcome == "failed" else 0, "", ""
        )

    package = ModuleType("agent_procutil")
    process = ModuleType("agent_procutil.owned_process")
    process.run_caller_owned = run
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, process.__name__, process)
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "GITHUB_SHA": "a" * 40,
        "GITHUB_RUN_ID": "123",
        "GITHUB_RUN_ATTEMPT": "2",
        "RUNNER_TEMP": str(tmp_path / "runner"),
        "REQUESTED_SOURCE": "e" * 40 if outcome == "requested-mismatch" else "a" * 40,
    }.items():
        monkeypatch.setenv(key, value)
    code = compile(step["run"], "<manual-lifecycle-workflow>", "exec")
    if outcome == "passed":
        exec(code, {})
    else:
        expected = (
            subprocess.CalledProcessError if outcome == "failed" else AssertionError
        )
        with pytest.raises(expected):
            exec(code, {})
    assert len(calls) == (
        0
        if outcome == "requested-mismatch"
        else 1
        if outcome == "source-mismatch"
        else 3
    )
