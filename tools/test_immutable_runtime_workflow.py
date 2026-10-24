"""The opt-in native runtime lane qualifies only an immutable POSIX source."""

from __future__ import annotations

from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load(
    (ROOT / ".github/workflows/installation-context-acceptance.yml").read_text("utf-8")
)
JOB = WORKFLOW["jobs"]["immutable-runtime-linux"]
STEPS = {step["name"]: step for step in JOB["steps"] if "name" in step}
SOURCE = STEPS["Validate immutable runtime commit before checkout"]
IDENTITY = STEPS["Record actual immutable runtime qualification identity"]
EXECUTION = STEPS["Qualify isolated native development lifecycle"]


def _python_body(step):
    prefix, suffix = "python - <<'PY'\n", "\nPY\n"
    command = step["run"]
    assert command.startswith(prefix) and command.endswith(suffix)
    return compile(command[len(prefix):-len(suffix)], "<runtime-workflow-step>", "exec")


def _enabled(expression, inputs):
    assert expression.startswith("${{ ") and expression.endswith(" }}")
    for term in expression[4:-3].split(" && "):
        if term == "github.event_name == 'workflow_dispatch'":
            continue
        match = re.fullmatch(r"(!?)inputs\.([a-z_]+)", term)
        assert match, term
        negative, name = match.groups()
        if bool(inputs[name]) == bool(negative):
            return False
    return True


def test_manual_selection_preserves_every_existing_route_and_default():
    trigger = WORKFLOW.get("on", WORKFLOW.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["immutable_runtime"]["type"] == "boolean"
    assert inputs["immutable_runtime"]["default"] is False
    assert inputs["immutable_runtime_ref"]["type"] == "string"
    assert inputs["immutable_runtime_ref"]["default"] == ""
    routes = {
        "immutable_runtime": {"immutable-runtime-linux", "immutable-runtime-macos"},
        "guardian_followthrough": {"guardian-followthrough-macos"},
        "watch_owner": {
            "watch-owner-native-linux", "watch-owner-native-windows",
            "watch-owner-macos-refusal",
            "watch-owner-boundaries-linux", "watch-owner-boundaries-windows",
        },
        "dispatch_delegation": {"dispatch-delegation-linux", "dispatch-delegation-windows"},
        "pr_service": {"pr-service-linux", "pr-service-windows"},
        "pr_artifacts": {"pr-artifacts-linux", "pr-artifacts-windows"},
        "dependency_compile": {"dependency-compile-linux", "dependency-compile-windows"},
        "candidate_profiles": {"candidate-profiles-linux", "candidate-profiles-windows"},
        "chat_construction": {"chat-construction-linux", "chat-construction-windows"},
        "ssh_snapshot": {"ssh-snapshot-native-linux", "ssh-snapshot-native-windows"},
        "wheel_inputs": {"wheel-inputs-linux", "wheel-inputs-windows"},
        "runtime_profiles": {"runtime-profile-policy-linux", "runtime-profile-policy-windows"},
    }
    defaults = {
        name: entry["default"] for name, entry in inputs.items()
        if entry["type"] == "boolean"
    }
    assert set(defaults) == set(routes) and not any(defaults.values())

    def selected(values):
        return {
            name for name, job in WORKFLOW["jobs"].items()
            if _enabled(job["if"], values)
        }

    diagnostics = {"derived-diagnostics-posix", "derived-diagnostics-windows"}
    assert selected(defaults) == diagnostics
    assert selected({**defaults, "immutable_runtime_ref": "a" * 40}) == diagnostics
    for selector, expected in routes.items():
        assert selected({**defaults, selector: True}) == expected
        if selector != "immutable_runtime":
            assert selected({**defaults, selector: True, "immutable_runtime": True}) == (
                expected | routes["immutable_runtime"]
            )
    assert set(WORKFLOW["jobs"]) == diagnostics | set().union(*routes.values())
    assert len(WORKFLOW["jobs"]) == 28


@pytest.mark.parametrize("requested", ["", "A" * 40, "0123456789abcdef" * 2 + "01234567"])
def test_actual_validator_accepts_only_exact_sha_or_dispatch_default(
    tmp_path, monkeypatch, requested,
):
    output = tmp_path / "outputs"
    monkeypatch.setenv("IMMUTABLE_RUNTIME_REF", requested)
    monkeypatch.setenv("DISPATCH_SHA", "b" * 40)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    exec(_python_body(SOURCE), {})
    assert output.read_text("utf-8") == f"sha={(requested or 'b' * 40).lower()}\n"


@pytest.mark.parametrize("requested", [
    "dev", "refs/heads/dev", "refs/pull/123/merge", "abc123",
    "a" * 39, "a" * 41, "g" * 40, " " + "a" * 40,
    "a" * 40 + "\n", "a" * 40 + "\r\n", "a" * 40 + "\nsha=dev",
    "$(touch unexpected)",
])
def test_actual_validator_refuses_input_before_publishing_output(
    tmp_path, monkeypatch, requested,
):
    output = tmp_path / "outputs"
    monkeypatch.setenv("IMMUTABLE_RUNTIME_REF", requested)
    monkeypatch.setenv("DISPATCH_SHA", "b" * 40)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    with pytest.raises(SystemExit, match="full immutable 40-hex"):
        exec(_python_body(SOURCE), {})
    assert not output.exists()


def test_actual_validator_also_checks_the_dispatch_sha(tmp_path, monkeypatch):
    output = tmp_path / "outputs"
    monkeypatch.setenv("IMMUTABLE_RUNTIME_REF", "")
    monkeypatch.setenv("DISPATCH_SHA", "dev")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    with pytest.raises(SystemExit, match="full immutable 40-hex"):
        exec(_python_body(SOURCE), {})
    assert not output.exists()


@pytest.mark.parametrize("matches", [True, False])
def test_actual_identity_checks_checkout_before_recording_evidence(monkeypatch, capsys, matches):
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
    monkeypatch.setattr(platform, "platform", lambda: "native-fixture")
    if not matches:
        with pytest.raises(SystemExit, match="does not match"):
            exec(_python_body(IDENTITY), {})
        assert len(calls) == 1 and capsys.readouterr().out == ""
    else:
        exec(_python_body(IDENTITY), {})
        output = capsys.readouterr().out
        assert f"Checkout: {actual}" in output
        assert "Parents: " + "c" * 40 + " " + "d" * 40 in output
        assert f"Python: {sys.version}" in output and "OS: native-fixture" in output
        assert len(calls) == 2


@pytest.mark.parametrize("fail_at", ["rev-parse", "show"])
def test_actual_identity_propagates_git_failures(monkeypatch, fail_at):
    monkeypatch.setenv("EXPECTED_SHA", "a" * 40)

    def git(argv, **kwargs):
        if argv[1] == fail_at:
            raise subprocess.CalledProcessError(23, argv)
        return "a" * 40 + "\n"

    monkeypatch.setattr(subprocess, "check_output", git)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        exec(_python_body(IDENTITY), {})
    assert caught.value.returncode == 23


@pytest.mark.parametrize("name,runner", [
    ("immutable-runtime-linux", "ubuntu-latest"),
    ("immutable-runtime-macos", "macos-latest"),
])
def test_exact_native_matrix_and_read_only_immutable_checkout(name, runner):
    job = WORKFLOW["jobs"][name]
    assert job["steps"] == JOB["steps"]
    assert job["if"] == "${{ inputs.immutable_runtime }}"
    assert job["runs-on"] == runner
    assert "${{" not in job["runs-on"]
    assert job["strategy"] == {
        "fail-fast": False,
        "matrix": {"mode": ["legacy", "namespaced"]},
    }
    assert job["timeout-minutes"] == 50
    assert job["env"] == {"UV_PYTHON": "3.11"}
    assert WORKFLOW["permissions"] == job["permissions"] == {"contents": "read"}
    assert [step["uses"] for step in job["steps"] if "uses" in step] == [
        "actions/setup-python@v5", "actions/checkout@v4", "astral-sh/setup-uv@v9.0.0",
    ]
    assert job["steps"][0]["with"] == {"python-version": "3.11"}
    checkout = next(step for step in job["steps"] if step.get("id") == "runtime-checkout")
    assert checkout["with"] == {
        "ref": "${{ steps.runtime-source.outputs.sha }}",
        "persist-credentials": False,
        "fetch-depth": 2,
    }
    assert job["steps"].index(SOURCE) < job["steps"].index(checkout)
    assert job["steps"].index(checkout) < job["steps"].index(IDENTITY)
    assert job["steps"].index(IDENTITY) < job["steps"].index(EXECUTION)
    assert SOURCE["env"] == {
        "IMMUTABLE_RUNTIME_REF": "${{ inputs.immutable_runtime_ref }}",
        "DISPATCH_SHA": "${{ github.sha }}",
    }
    assert IDENTITY["env"] == {"EXPECTED_SHA": "${{ steps.runtime-source.outputs.sha }}"}


@pytest.mark.parametrize("name", ["immutable-runtime-linux", "immutable-runtime-macos"])
def test_exact_runner_selection_budgets_and_failure_propagation(name):
    job = WORKFLOW["jobs"][name]
    assert EXECUTION["env"] == {"RUNTIME_MODE": "${{ matrix.mode }}"}
    assert EXECUTION["run"] == (
        "python tools/run-plugin-tests.py agent-worktrees "
        "--test-file tests/test_python_runtime_dev_live.py "
        '-k "$RUNTIME_MODE" --timeout 2500 --plugin-timeout 2700'
    )
    assert shlex.split(EXECUTION["run"]) == [
        "python", "tools/run-plugin-tests.py", "agent-worktrees",
        "--test-file", "tests/test_python_runtime_dev_live.py",
        "-k", "$RUNTIME_MODE", "--timeout", "2500", "--plugin-timeout", "2700",
    ]
    assert not job.get("continue-on-error")
    assert "defaults" not in job and "defaults" not in WORKFLOW
    commands = [step for step in job["steps"] if "run" in step]
    assert commands == [SOURCE, IDENTITY, EXECUTION]
    for step in job["steps"]:
        assert not step.get("continue-on-error")
        assert "if" not in step and "shell" not in step
        command = step.get("run", "")
        assert "${{" not in command
        assert "--allow-host-state" not in command and "sudo" not in command
        assert "secrets." not in str(step)
