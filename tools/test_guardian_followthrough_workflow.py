"""The manual Darwin packet pins source and cannot expand required CI."""

from __future__ import annotations

import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from tools import plugin_test_containment as containment


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load(
    (ROOT / ".github/workflows/installation-context-acceptance.yml").read_text("utf-8")
)
JOB = WORKFLOW["jobs"]["guardian-followthrough-macos"]
STEPS = {step["name"]: step for step in JOB["steps"] if "name" in step}
SOURCE = STEPS["Validate immutable guardian commit before checkout"]
IDENTITY = STEPS["Record actual native qualification identity"]
CALLER = STEPS["Full caller-owned native capture and Darwin census controls"]


def _python_body(step):
    prefix, suffix = "python - <<'PY'\n", "\nPY\n"
    command = step["run"]
    assert command.startswith(prefix) and command.endswith(suffix)
    body = command[len(prefix):-len(suffix)]
    return compile(body, "<guardian-workflow-step>", "exec")


def _enabled(expression, inputs, event="workflow_dispatch"):
    assert expression.startswith("${{ ") and expression.endswith(" }}")
    for term in expression[4:-3].split(" && "):
        if term == "github.event_name == 'workflow_dispatch'":
            if event != "workflow_dispatch":
                return False
            continue
        match = re.fullmatch(r"(!?)inputs\.([a-z_]+)", term)
        assert match, term
        negative, name = match.groups()
        if bool(inputs[name]) == bool(negative):
            return False
    return True


def test_manual_selector_is_isolated_and_preserves_existing_routes():
    trigger = WORKFLOW.get("on", WORKFLOW.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["guardian_followthrough"]["type"] == "boolean"
    assert inputs["guardian_followthrough"]["default"] is False
    assert inputs["guardian_ref"]["type"] == "string"
    assert inputs["guardian_ref"]["default"] == ""
    defaults = {
        name: item["default"] for name, item in inputs.items()
        if item["type"] == "boolean"
    }
    jobs = WORKFLOW["jobs"]
    selected = {**defaults, "guardian_followthrough": True}
    assert {
        name for name, job in jobs.items() if _enabled(job["if"], selected)
    } == {"guardian-followthrough-macos"}
    assert not _enabled(JOB["if"], selected, event="push")
    assert {
        name for name, job in jobs.items() if _enabled(job["if"], defaults)
    } == {"derived-diagnostics-posix", "derived-diagnostics-windows"}
    for selector in defaults.keys() - {"guardian_followthrough"}:
        selected = {**defaults, selector: True}
        enabled = {
            name for name, job in jobs.items() if _enabled(job["if"], selected)
        }
        assert enabled and "guardian-followthrough-macos" not in enabled
        for name in enabled:
            assert jobs[name]["if"] == "${{ inputs." + selector + " }}"


@pytest.mark.parametrize("requested", ["", "A" * 40, "1234567890abcdef" * 2 + "12345678"])
def test_actual_ref_validator_selects_only_an_immutable_sha(tmp_path, monkeypatch, requested):
    output = tmp_path / "outputs"
    monkeypatch.setenv("GUARDIAN_REF", requested)
    monkeypatch.setenv("DISPATCH_SHA", "b" * 40)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    exec(_python_body(SOURCE), {})
    assert output.read_text("utf-8") == f"sha={(requested or 'b' * 40).lower()}\n"


@pytest.mark.parametrize("requested", [
    "dev", "refs/pull/7753/merge", "a" * 39, "a" * 41, "g" * 40,
    " " + "a" * 40, "a" * 40 + "\n", "$(touch unexpected)",
    "a" * 40 + "\nsha=dev",
])
def test_actual_ref_validator_refuses_before_publishing_output(tmp_path, monkeypatch, requested):
    output = tmp_path / "outputs"
    monkeypatch.setenv("GUARDIAN_REF", requested)
    monkeypatch.setenv("DISPATCH_SHA", "b" * 40)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    with pytest.raises(SystemExit, match="full immutable 40-hex"):
        exec(_python_body(SOURCE), {})
    assert not output.exists()


def test_invalid_default_sha_also_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("GUARDIAN_REF", "")
    monkeypatch.setenv("DISPATCH_SHA", "dev")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    with pytest.raises(SystemExit, match="full immutable 40-hex"):
        exec(_python_body(SOURCE), {})


@pytest.mark.parametrize("matches", [True, False])
def test_checkout_identity_is_checked_before_recording_evidence(monkeypatch, capsys, matches):
    expected = "a" * 40
    actual = expected if matches else "b" * 40
    calls = []

    def git(argv, **kwargs):
        calls.append(argv)
        assert kwargs == {"text": True}
        if argv == ["git", "rev-parse", "HEAD"]:
            return actual + "\n"
        assert argv == ["git", "show", "-s", "--format=Parents: %P", "HEAD"]
        return "Parents: " + "c" * 40 + " " + "d" * 40 + "\n"

    monkeypatch.setenv("EXPECTED_SHA", expected)
    monkeypatch.setattr(subprocess, "check_output", git)
    monkeypatch.setattr(platform, "platform", lambda: "Darwin-fixture")
    if not matches:
        with pytest.raises(SystemExit, match="does not match"):
            exec(_python_body(IDENTITY), {})
        assert len(calls) == 1 and capsys.readouterr().out == ""
    else:
        exec(_python_body(IDENTITY), {})
        output = capsys.readouterr().out
        assert f"Checkout: {actual}" in output
        assert "Parents: " + "c" * 40 + " " + "d" * 40 in output
        assert "Python: " in output and "OS: Darwin-fixture" in output


def test_checkout_is_read_only_and_cannot_interpolate_input_into_shell():
    assert WORKFLOW["permissions"] == JOB["permissions"] == {"contents": "read"}
    assert JOB["runs-on"] == "macos-latest" and JOB["timeout-minutes"] == 25
    assert JOB["env"] == {"UV_PYTHON": "3.11"}
    assert not JOB.get("continue-on-error") and "strategy" not in JOB
    actions = [step["uses"] for step in JOB["steps"] if "uses" in step]
    assert actions == [
        "actions/setup-python@v5", "actions/checkout@v4", "astral-sh/setup-uv@v9.0.0",
    ]
    checkout = next(step for step in JOB["steps"] if step.get("id") == "guardian-checkout")
    assert checkout["with"] == {
        "ref": "${{ steps.guardian-source.outputs.sha }}",
        "persist-credentials": False, "fetch-depth": 2,
    }
    assert JOB["steps"].index(SOURCE) < JOB["steps"].index(checkout)
    assert JOB["steps"][0]["with"]["python-version"] == "3.11"
    assert SOURCE["env"] == {
        "GUARDIAN_REF": "${{ inputs.guardian_ref }}", "DISPATCH_SHA": "${{ github.sha }}",
    }
    for step in JOB["steps"]:
        command = step.get("run", "")
        assert "${{" not in command and "secrets." not in str(step)
        assert not step.get("continue-on-error")
        assert "--allow-host-state" not in command and "sudo" not in command


@pytest.mark.parametrize("result", [0, 1, 124])
def test_actual_caller_packet_uses_containment_and_propagates_failure(
    tmp_path, monkeypatch, result,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    calls = []

    def isolated(base, sandbox):
        assert base is os.environ and sandbox.parent == tmp_path
        return {"contained": "1"}

    def contained(command, **kwargs):
        sandbox = kwargs["sandbox"]
        assert sandbox.is_dir()
        assert kwargs["cwd"] == tmp_path
        assert kwargs["limits"] == {"wall_seconds": 300}
        assert kwargs["env"] == {
            "contained": "1",
            "PYTHONPATH": os.pathsep.join((
                str(tmp_path / "tools"), str(tmp_path / "libs/agent-procutil/src"),
            )),
        }
        assert command == [
            sys.executable, "-m", "pytest", "-q", "--durations=15",
            "-p", "pytest_portfolio_guard", "--timeout=30",
            f"--basetemp={sandbox / 'pytest'}",
            "libs/agent-procutil/tests/test_caller_owned.py",
        ]
        calls.append(sandbox)
        return result

    monkeypatch.setitem(sys.modules, "plugin_test_containment", SimpleNamespace(
        Limits=lambda **kwargs: kwargs, isolated_environment=isolated,
        run_contained=contained,
    ))
    with pytest.raises(SystemExit) as caught:
        exec(_python_body(CALLER), {})
    assert caught.value.code == result
    assert len(calls) == 1 and not calls[0].exists()


def test_actual_caller_packet_retains_workspace_when_teardown_is_unverified(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    failure = containment.UnreapedProcessError("synthetic unsettled teardown")
    sandboxes = []

    def unsettled(command, **kwargs):
        sandbox = kwargs["sandbox"]
        (sandbox / "owner-state").write_text("still owned", encoding="utf-8")
        sandboxes.append(sandbox)
        raise failure

    monkeypatch.setattr(containment, "run_contained", unsettled)
    monkeypatch.setitem(sys.modules, "plugin_test_containment", containment)
    with pytest.raises(containment.UnreapedProcessError) as caught:
        exec(_python_body(CALLER), {})
    assert caught.value is failure
    assert len(sandboxes) == 1
    sandbox = sandboxes[0]
    assert sandbox.is_dir()
    assert (sandbox / "owner-state").read_text("utf-8") == "still owned"
    output = capsys.readouterr()
    assert str(sandbox) in output.out + output.err
    assert "retained" in output.out + output.err


def test_exact_packet_has_no_broad_suite_or_timeout_override():
    tooling = STEPS["Prepare seasoned shared-library test tooling"]
    assert shlex.split(tooling["run"]) == [
        "python", "tools/seasoned_uv.py", "--uv", "uv", "--registry-only", "--",
        "--system", "pytest", "pytest-timeout>=2.3,<3", "psutil",
    ]
    packet = [
        step for step in JOB["steps"] if step.get("if")
    ]
    assert len(packet) == 5
    assert packet[0] is CALLER
    for step in packet:
        assert step["if"] == "${{ !cancelled() && steps.guardian-tooling.outcome == 'success' }}"
    expected = [
        ("agent-worktrees", "tests/test_installer_guardian.py", None),
        ("agent-worktrees", "tests/test_creation_tail_repair.py",
         "test_creation_retains_companion_lifecycle_through_guidance_against_cmd_embody"),
        ("customizing-copilot", "tests/test_instruction_delivery.py",
         "test_shipped_inline_allowlist_is_explicit_and_substantive"),
        ("copilot-extensions-harness", "tests/test_reviewer_source.py",
         "test_windows_command_shim_uses_cmd"),
    ]
    for step, (plugin, test_file, selector) in zip(packet[1:], expected, strict=True):
        command = ["python", "tools/run-plugin-tests.py", plugin, "--test-file", test_file]
        if selector:
            command.extend(("-k", selector))
        assert shlex.split(step["run"]) == command
