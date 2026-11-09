"""Coverage selection for canonical and derived installation diagnostics."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "installation_context_ci_scope", HERE / "installation_context_ci_scope.py"
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("outcome", ["passed", "failed", "skipped", "missing", "source-mismatch"])
def test_runtime_only_portfolio_refuses_incomplete_native_evidence(
    tmp_path, monkeypatch, capsys, outcome,
):
    import xml.etree.ElementTree as ET

    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    job = workflow["jobs"]["worktrees-runtime-only-linux"]
    step = next(value for value in job["steps"] if value.get("id") == "composition")
    shells = ("powershell.exe", "pwsh") if os.name == "nt" else ("bash",)
    names = [
        "test_native_runtime_only_survives_driver_admission[" + action + "]"
        for action in ("install", "update")
    ]
    for shell in shells:
        names.append("test_runtime_only_suppresses_existing_project_inference_without_changing_defaults[" + shell + "]")
        for conflict in ("project", "action"):
            names.append("test_runtime_only_frontdoor_refuses_before_native_admission[" + conflict + "-" + shell + "]")
    if os.name == "nt":
        names.extend(
            "test_real_cold_driver_and_native_reentry_use_the_same_owner[" + case + "]"
            for case in ("False-False", "False-True", "True-False")
        )
    calls = []

    def run(arguments, *, timeout, max_stdout_bytes, max_stderr_bytes, env=None):
        calls.append(arguments)
        if arguments[0] == "git":
            return subprocess.CompletedProcess(arguments, 0, "a" * 40 + "\n", "")
        reports = Path(arguments[arguments.index("--junit-dir") + 1])
        reports.mkdir(parents=True)
        suite = ET.Element("testsuite")
        for name in names[:-1] if outcome == "missing" else names:
            case = ET.SubElement(suite, "testcase", name=name)
            if outcome == "skipped":
                ET.SubElement(case, "skipped")
        ET.ElementTree(suite).write(reports / "synthetic-controls.xml")
        return subprocess.CompletedProcess(
            arguments, 17 if outcome == "failed" else 0,
            "runner progress\n", "runner diagnostic\n",
        )

    package = ModuleType("agent_procutil")
    process = ModuleType("agent_procutil.owned_process")
    process.run_caller_owned = run
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, process.__name__, process)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner-temp"))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "job-environment"))
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("WORKTREES_RUNTIME_ONLY_SOURCE", (
        "b" * 40 if outcome == "source-mismatch" else "a" * 40
    ))
    body = compile(step["run"], "<runtime-only-workflow>", "exec")
    if outcome == "passed":
        exec(body, {})
    else:
        expected = subprocess.CalledProcessError if outcome == "failed" else AssertionError
        with pytest.raises(expected):
            exec(body, {})
    assert len(calls) == (1 if outcome == "source-mismatch" else 2)
    if outcome == "failed":
        captured = capsys.readouterr()
        assert "runner progress" in captured.out
        assert "runner diagnostic" in captured.err


def test_bridge_historical_guards_preserve_selection_and_failure_diagnostics():
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    job = workflow["jobs"]["bridge-contracts-linux"]
    step = next(
        value for value in job["steps"]
        if value.get("name") == "Exercise immutable bytes and historical publication guards"
    )
    arguments = shlex.split(step["run"])
    files = [
        arguments[index + 1] for index, value in enumerate(arguments)
        if value == "--test-file"
    ]
    assert files == [
        "tests/test_http48_source_retention.py",
        "tests/test_http48_capture_guards.py",
        "tests/test_http47_capture_guards.py",
    ]
    assert arguments[arguments.index("-k") + 1] == (
        "http48 or shared_capture_seed_has_owned_committed_inputs "
        "or capture_reproducible_real_commit_preserves_history"
    )
    assert not set(arguments) & {
        "--test-timeout", "--timeout", "--plugin-timeout",
        "--allow-host-state", "--allow-explicit-tiers",
    }
    options = shlex.split(step["env"]["PYTEST_ADDOPTS"])
    assert "-vv" in options and "--durations=20" in options
    upload = next(
        value for value in job["steps"]
        if value.get("name") == "Retain historical publication guard outcomes"
    )
    assert upload["if"] == "always()"
    assert upload["with"]["path"] == arguments[arguments.index("--junit-dir") + 1]
    assert not step.get("continue-on-error")
    names = [value.get("name") for value in job["steps"]]
    assert (
        names.index("Independently verify current HTTP-52 controlled source evidence")
        < names.index(step["name"])
        < names.index("Validate canonical historical evidence")
        < names.index("Export only canonical current outputs and source preimages")
        < names.index("Retain qualified current HTTP-52 outputs")
    )


@pytest.mark.parametrize("outcome", ["passed", "failed", "skipped", "missing", "empty", "duplicate"])
@pytest.mark.parametrize("step_id", ["readers", "incoming"])
def test_registration_inspection_portfolio_requires_complete_native_evidence(
    tmp_path, monkeypatch, capsys, outcome, step_id,
):
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    job = workflow["jobs"]["registration-inspection-linux"]
    assert job["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'registration-inspection'"
    )
    assert workflow["jobs"]["registration-inspection-windows"]["steps"] == job["steps"]
    step = next(value for value in job["steps"] if value.get("id") == step_id)
    required_names = (
        [
            "test_actual_neutral_cwd_cli_and_existing_inventory_remain_read_only",
            "test_native_namespace_selection_and_invalid_context_do_not_use_legacy_state",
        ] if step_id == "readers" else [
            "test_closed_source_retains_native_inputs_and_application_through_reader",
            *(
                "test_incoming_inspection_ignores_broken_prior_runtime_and_preserves_state[" + scenario + "]"
                for scenario in (
                    "alias-disabled", "alias-enabled", "fresh", "ambiguous", "preparation-failure",
                )
            ),
        ]
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "run-plugin-tests.py").write_text(f"""import json, sys
from pathlib import Path
import xml.etree.ElementTree as ET
arguments = sys.argv[1:]
Path("arguments.json").write_text(json.dumps(arguments))
if {outcome!r} == "failed":
    print("runner failure progress", flush=True)
    print("runner failure reason", file=sys.stderr, flush=True)
    sys.exit(17)
reports = Path(arguments[arguments.index("--junit-dir") + 1])
reports.mkdir(parents=True)
root = ET.Element("testsuites")
suite = ET.SubElement(root, "testsuite")
names = {required_names!r}
if {outcome!r} == "missing":
    names.pop()
if {outcome!r} == "empty":
    names.clear()
if {outcome!r} == "duplicate":
    names.append(names[-1])
for name in names:
    case = ET.SubElement(suite, "testcase", name=name)
    if {outcome!r} == "skipped":
        ET.SubElement(case, "skipped")
ET.ElementTree(root).write(reports / "synthetic-admission-control.xml")
""", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner-temp"))
    monkeypatch.setenv("REGISTRATION_INSPECTION_REPORTS", str(tmp_path / "runner-temp"))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "job-environment"))
    body = compile(step["run"], "<registration-inspection-workflow>", "exec")
    if outcome == "passed":
        exec(body, {})
    else:
        expected = subprocess.CalledProcessError if outcome == "failed" else AssertionError
        with pytest.raises(expected):
            exec(body, {})
    if outcome == "failed":
        captured = capsys.readouterr()
        assert "runner failure progress" in captured.out
        assert "runner failure reason" in captured.err
    arguments = json.loads((tmp_path / "arguments.json").read_text())
    expected_file = (
        "tests/test_registration_inspection.py"
        if step_id == "readers" else "tests/test_incoming_registration_inspection.py"
    )
    assert arguments[:3] == ["agent-worktrees", "--test-file", expected_file]
    assert ("--allow-explicit-tiers" in arguments) == (step_id == "incoming")
    assert not set(arguments) & {"--allow-host-state", "--timeout", "--plugin-timeout"}
    if step_id == "incoming":
        assert "tests/native/test_incoming_registration_inspection_native.py" in arguments


@pytest.mark.parametrize("expected", ["", "b" * 40, "a" * 40])
def test_registration_inspection_source_pin_precedes_execution(monkeypatch, expected):
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    step = next(
        value for value in workflow["jobs"]["registration-inspection-linux"]["steps"]
        if value.get("name") == "Verify the explicitly selected qualification source"
    )
    monkeypatch.setenv("REGISTRATION_INSPECTION_SOURCE", expected)
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setattr(subprocess, "check_output", lambda *_args, **_kwargs: "a" * 40 + "\n")
    body = compile(step["run"], "<registration-inspection-source>", "exec")
    if expected == "a" * 40:
        exec(body, {})
    else:
        with pytest.raises(AssertionError):
            exec(body, {})


@pytest.mark.parametrize("outcome", ["passed", "failed", "skipped"])
@pytest.mark.parametrize("step_id", ["readers", "consumers"])
def test_related_knowledge_portfolio_requires_native_reader_execution(
    tmp_path, monkeypatch, outcome, step_id,
):
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    step = next(
        value for value in workflow["jobs"]["related-knowledge-linux"]["steps"]
        if value.get("id") == step_id
    )
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "run-plugin-tests.py").write_text(
        f"""import json, sys
from pathlib import Path
import xml.etree.ElementTree as ET
arguments = sys.argv[1:]
Path("arguments.json").write_text(json.dumps(arguments))
if {outcome!r} == "failed":
    sys.exit(17)
reports = Path(arguments[arguments.index("--junit-dir") + 1])
reports.mkdir(parents=True)
root = ET.Element("testsuites")
suite = ET.SubElement(root, "testsuite")
for variant in ("bare-anchor", "harness-worktree"):
    case = ET.SubElement(suite, "testcase",
        name="test_native_bound_knowledge_discovery_is_read_only[" + variant + "]")
    if {outcome!r} == "skipped" and variant == "harness-worktree":
        ET.SubElement(case, "skipped")
ET.ElementTree(root).write(reports / "native.xml")
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path / "runner-temp"))
    monkeypatch.setenv("GITHUB_ENV", str(tmp_path / "job-environment"))
    body = compile(step["run"], "<related-knowledge-workflow>", "exec")
    if outcome == "passed" or (outcome == "skipped" and step_id == "consumers"):
        exec(body, {})
    else:
        expected = subprocess.CalledProcessError if outcome == "failed" else AssertionError
        with pytest.raises(expected):
            exec(body, {})
    arguments = json.loads((tmp_path / "arguments.json").read_text())
    assert arguments[0] == "agent-worktrees"
    assert ("--allow-explicit-tiers" in arguments) == (step_id == "readers")
    selected = [
        arguments[index + 1] for index, value in enumerate(arguments)
        if value == "--test-file"
    ]
    expected = [
        "tests/test_related_knowledge.py", "tests/native/test_related_knowledge_native.py",
    ] if step_id == "readers" else [
        "tests/test_pr_related_binding.py", "tests/test_session_context_registry.py",
        "tests/test_session_context_companions.py", "tests/test_repos_configuration.py",
    ]
    assert selected == expected
    assert "--allow-host-state" not in arguments


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_gateway_native_groups_preserve_the_complete_disjoint_selection(platform):
    from _pytest.mark.expression import Expression

    root = HERE.parent
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "installation-context-acceptance.yml").read_text()
    )
    events = workflow.get("on", workflow.get(True))
    assert len(events["workflow_dispatch"]["inputs"]) <= 25
    job = workflow["jobs"]["gateway-host-application-" + platform]
    steps = [step for step in job["steps"] if "run" in step]
    assert job["timeout-minutes"] == 6 and len(steps) == 3
    assert [shlex.split(step["run"]) for step in steps] == [
        ["python", "tools/run-standalone-tests.py", "fleet-manager", "--prepare", "--timeout", "180"],
        ["python", "tools/run-standalone-tests.py", "fleet-manager", "--timeout", "180"],
        ["python", "tools/run-standalone-tests.py", "fleet-manager", "--timeout", "180"],
    ]
    assert not any(step.get("continue-on-error") for step in steps)
    continuity = workflow["jobs"]["gateway-continuity-application-" + platform]
    assert continuity["timeout-minutes"] == 6
    assert continuity["needs"] == "gateway-host-application-" + platform
    followthrough = [step for step in continuity["steps"] if "run" in step]
    assert len(followthrough) == 1 and not followthrough[0].get("continue-on-error")
    assert shlex.split(followthrough[0]["run"]) == [
        "python", "tools/run-standalone-tests.py", "fleet-manager", "--prepare", "--timeout", "180",
    ]
    normal = workflow["jobs"]["gateway-normal-setup-" + platform]
    assert normal["timeout-minutes"] == 6
    assert normal["needs"] == "gateway-continuity-application-" + platform
    normal_steps = [step for step in normal["steps"] if "run" in step]
    assert [shlex.split(step["run"]) for step in normal_steps] == [
        ["python", "tools/run-plugin-tests.py", "agent-worktrees", "--prepare-only"],
        ["python", "tools/run-plugin-tests.py", "agent-worktrees",
         "--test-file", "tests/test_repository_continuity.py",
         "-k", "normal_ssh_setup or setup_status"],
        ["python", "tools/run-standalone-tests.py", "fleet-manager", "--prepare", "--timeout", "180",
         "--ephemeral-native-ssh-setup"],
    ]
    assert not any(step.get("continue-on-error") for step in normal_steps)
    assert normal_steps[2]["env"]["COPILOT_EXTENSIONS_SSH_SETUP_NATIVE"] == "1"
    expressions = []
    for step in steps + followthrough + [normal_steps[2]]:
        arguments = shlex.split(step["env"]["PYTEST_ADDOPTS"])
        expressions.append(Expression.compile(arguments[arguments.index("-k") + 1]))
    original = Expression.compile(
        "test_gateway_host or test_gateway_enrollment "
        "or wheel_contains_record_bound_exact_primitive_and_no_runtime_dependencies "
        "or test_ssh_machine or test_machine_identity or test_credential_references"
        " or test_provider_owned_caller_reuses_enrollment_without_export_rotation_or_reenrollment"
        " or test_provider_caller_does_not_recreate_a_concurrently_removed_identity"
        " or test_ssh_setup or normal_manager_engine_ssh_first_touch_repeat_update_and_stop"
    )
    nodes = {
        path.name + "::" + node.name
        for path in (root / "fleet-manager" / "tests").rglob("test_*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    }

    def selected(expression):
        return {
            node for node in nodes
            if expression.evaluate(lambda keyword: keyword.casefold() in node.casefold())
        }

    groups = [selected(expression) for expression in expressions]
    assert groups[0] == {
        "test_gateway_host_distribution.py::test_selected_gateway_application_first_touch_repeat_update_and_stop",
    }
    assert groups[1] == {
        "test_gateway_host_distribution.py::"
        "test_selected_ssh_application_first_touch_repeat_update_and_stop",
    }
    assert groups[3] == {
        "test_gateway_host_distribution.py::"
        "test_selected_continuity_ssh_application_first_touch_repeat_update_and_stop",
    }
    assert groups[4] == {
        node for node in nodes if node.startswith("test_ssh_setup.py::")
    } | {
        "test_repository_ssh_setup_installed.py::"
        "test_normal_manager_engine_ssh_first_touch_repeat_update_and_stop",
    }
    for index, group in enumerate(groups):
        assert group.isdisjoint(set().union(*groups[:index]))
    assert set().union(*groups) == selected(original)
    assert (
        "test_continuity_bootstrap.py::"
        "test_provider_owned_caller_reuses_enrollment_without_export_rotation_or_reenrollment"
    ) in groups[2]
    assert (
        "test_continuity_bootstrap.py::"
        "test_provider_caller_does_not_recreate_a_concurrently_removed_identity"
    ) in groups[2]


@pytest.mark.parametrize(
    "path",
    [
        "libs/installation-context/_installation_context_mode_cli.py",
        "libs/installation-context/tests/test_installation_mode_governance.py",
        "plugins/agent-vault/scripts/installation-context/installation-context.ps1",
        "plugins/agent-ssh/scripts/installation-context/installation-context.sh",
        "plugins/agent-bridge/src/agent_bridge/_installation_context_files.py",
        "worktree-manager/src/worktree_manager/_installation_context.py",
        "worktree-manager/src/worktree_manager/agent_plugin_runtime.py",
        "worktree-manager/tests/test_agent_plugin_runtime.py",
        "worktree-manager/tests/_installation_context_fixtures.py",
        "libs/peer-launch/peer_launch.py",
        "plugins/agent-bridge/src/agent_bridge/_peer_launch.py",
        "plugins/agent-dispatch/src/agent_dispatch/peer_launch.py",
        "plugins/agent-worktrees/src/agent_worktrees/reconcile.py",
        "plugins/agent-worktrees/tests/test_reconcile.py",
        "plugins/agent-worktrees/src/agent_worktrees/installer.py",
        "plugins/agent-worktrees/src/agent_worktrees/installation_cli.py",
        "plugins/agent-worktrees/src/agent_worktrees/deploy_manifest.py",
        "plugins/agent-worktrees/tests/test_installer_command_binding.py",
        "plugins/agent-worktrees/scripts/install.sh",
        "plugins/agent-worktrees/scripts/install.ps1",
        "plugins/agent-worktrees/scripts/command_binding.py",
        "plugins/agent-worktrees/src/agent_worktrees/_command_binding.py",
        "plugins/agent-worktrees/scripts/command_lifecycle.py",
        "plugins/agent-worktrees/src/agent_worktrees/_command_lifecycle.py",
        "plugins/agent-worktrees/scripts/emit-command-catalog.sh",
        "plugins/agent-worktrees/scripts/emit-command-catalog.ps1",
        "plugins/agent-worktrees/payload-invocation.json",
        "libs/payload-invocation/command_binding.py",
        "libs/payload-invocation/tests/test_derived_diagnostic_cli.py",
        "libs/payload-invocation/templates/catalog-posix.tmpl",
        "tools/sync-installation-context.py",
        ".github/workflows/ci.yml",
        ".github/workflows/installation-context-acceptance.yml",
        "tools/installation_context_ci_scope.py",
        "tools/test_installation_context_ci_scope.py",
    ],
)
def test_selects_each_contract_surface(path: str) -> None:
    assert MODULE.needs_installation_context_smoke([path])


@pytest.mark.parametrize("paths", [[], ["docs/architecture.md"], ["plugins/agent-vault/README.md"]])
def test_unrelated_changes_do_not_expand_ci(paths: list[str]) -> None:
    assert not MODULE.needs_installation_context_smoke(paths)


def test_dispatch_selects_smoke_without_git(monkeypatch, capsys) -> None:
    def unexpected_git(*args, **kwargs):
        raise AssertionError("manual dispatch must not need diff metadata")

    monkeypatch.setattr(MODULE.subprocess, "run", unexpected_git)
    assert MODULE.main(["--event", "workflow_dispatch"]) == 0
    assert capsys.readouterr().out == "true\n"


def test_missing_base_selects_smoke_visibly(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        MODULE.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 128, "", "unknown revision\n"),
    )
    assert MODULE.main(["--base", "missing"]) == 0
    output = capsys.readouterr()
    assert output.out == "true\n"
    assert "base revision unavailable" in output.err


def test_diff_error_is_not_reported_as_unrelated(monkeypatch, capsys) -> None:
    results = iter(
        [
            subprocess.CompletedProcess([], 0, "base\n", ""),
            subprocess.CompletedProcess([], 128, "", "invalid head\n"),
        ]
    )
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *args, **kwargs: next(results))
    assert MODULE.main(["--base", "base", "--head", "missing"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "invalid head" in output.err


@pytest.mark.parametrize("retired_job", [
    "installation-context-windows", "command-lifecycle-windows",
    "vault-snapshot-windows", "fleet-native-windows",
])
def test_native_jobs_do_not_reenter_required_unit_only_ci(retired_job: str) -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    assert retired_job not in workflow["jobs"]
    assert set(workflow["jobs"]["pr-gate"]["needs"]) == {"checks", "unit-tests"}
    assert workflow["env"]["PROMOTION_UNIT_PROFILE"] == "copilot-extensions.promotion-units/v1"
    units = workflow["jobs"]["unit-tests"]
    execution = next(step for step in units["steps"] if step.get("id") == "units")
    assert execution["if"] == "steps.impact.outputs.run == 'true'"
    assert "tools/ci_pr_unit_reuse.py" in execution["run"]
    assert not execution.get("continue-on-error", False)


def test_native_launch_acceptance_is_explicit_and_source_pinned() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    assert trigger["workflow_dispatch"]["inputs"]["native_launch"] == {
        "description": "Qualify native entry, paired Sync, copied setup, terminal ownership and mux admission",
        "type": "boolean", "default": False,
    }
    job = workflow["jobs"]["native-launch-windows"]
    assert job["if"] == "${{ inputs.native_launch }}"
    assert job["runs-on"] == "windows-latest"
    assert job["timeout-minutes"] == 30
    assert job["steps"][0]["with"] == {
        "ref": "${{ github.sha }}", "persist-credentials": False,
    }
    preflight = next(step for step in job["steps"] if step.get("name", "").startswith("Verify native"))
    assert "$actual -cne $env:GITHUB_SHA" in preflight["run"]
    execution = next(
        step["run"] for step in job["steps"]
        if "tests/native/test_native_seed_console.py" in step.get("run", "")
    )
    assert "tests/native/test_native_seed_console.py" in execution
    assert "tests/test_native_seed_shell.py" in execution
    arguments = shlex.split(execution)
    assert set(arguments[arguments.index("-k") + 1].split(" or ")) == {
        "copied_setup_admission", "real_copied_setup", "same_target_joins_at_discovery",
    }
    assert "--allow-host-state" not in execution
    assert not any(step.get("continue-on-error", False) for step in job["steps"])
    for name in ("derived-diagnostics-posix", "derived-diagnostics-windows"):
        assert "!inputs.native_launch" in workflow["jobs"][name]["if"]


@pytest.mark.parametrize(("name", "runner"), [
    ("native-seed-reply-linux", "ubuntu-latest"),
    ("native-seed-reply-macos", "macos-latest"),
])
def test_native_seed_reader_acceptance_uses_stock_posix_shells_only_when_selected(name, runner) -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    job = workflow["jobs"][name]
    assert job["if"] == "${{ inputs.native_launch }}"
    assert job["runs-on"] == runner
    assert job["timeout-minutes"] == 12
    assert job["steps"][0]["with"] == {
        "ref": "${{ github.sha }}", "persist-credentials": False,
    }
    preflight = next(step for step in job["steps"] if step.get("name", "").startswith("Verify native"))
    assert 'git rev-parse HEAD' in preflight["run"] and '"$EXPECTED_SHA"' in preflight["run"]
    if runner == "macos-latest":
        assert '/bin/bash -c \'[[ "$BASH_VERSINFO" == 3 ]]\'' in preflight["run"]
    execution = next(
        step["run"] for step in job["steps"]
        if "--test-file tests/test_native_seed_reply_shell.py" in step.get("run", "")
    )
    assert 'export PATH="/bin:$PATH"' in execution
    assert "--test-file tests/test_native_seed_reply_shell.py" in execution
    assert "--test-file tests/test_native_seed_phase_shell.py" in execution
    assert "--test-file tests/test_session_shell.py" in execution
    assert "--test-file tests/native/test_native_seed_exec_posix.py" in execution
    assert "--test-file tests/native/test_native_seed_setup_posix.py" in execution
    assert "--timeout 360 --plugin-timeout 480 --test-timeout 30" in execution
    assert "--allow-host-state" not in execution
    assert not any(step.get("continue-on-error", False) for step in job["steps"])


def test_dispatch_maintenance_native_acceptance_is_manual_bounded_and_source_pinned() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "dispatch-maintenance" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    packets = {
        "policy": {"test_action_native_ps1.py", "test_action_native_policy.py",
                   "test_cutover_spawn_env.py", "test_conditional_deploy.py"},
        "installer": {"test_install_lifecycle_emit.py", "test_installer_required_cutover.py",
                      "test_retained_runtime.py", "test_runtime_preparation.py",
                      "test_installer_engine_adoption.py"},
        "worker": {"test_windows_bootstrap_reconcile_lifecycle.py",
                   "test_bootstrap_check_reconcile_always_on.py"},
    }
    for packet, expected in packets.items():
        job = workflow["jobs"][f"dispatch-maintenance-{packet}-windows"]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'dispatch-maintenance'"
        )
        assert job["runs-on"] == "windows-latest" and job["timeout-minutes"] == 15
        assert job["env"]["PYTEST_ADDOPTS"] == "-ra"
        assert job["steps"][0]["with"] == {
            "ref": "${{ github.sha }}", "persist-credentials": False,
        }
        preflight = job["steps"][-2]
        assert "if (-not $IsWindows)" in preflight["run"]
        assert "$actual -cne $env:GITHUB_SHA" in preflight["run"]
        words = shlex.split(job["steps"][-1]["run"])
        assert words[:3] == ["python", "tools/run-plugin-tests.py", "agent-dispatch"]
        selected = {Path(words[i + 1]).name for i, word in enumerate(words) if word == "--test-file"}
        assert selected == expected
        assert "--timeout 300 --plugin-timeout 360" in job["steps"][-1]["run"]
        assert "--allow-host-state" not in words
        assert not any(step.get("continue-on-error", False) for step in job["steps"])


def test_optional_mux_safety_qualification_uses_disposable_pinned_windows_tooling() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    for key, platform in (
        ("interactive-completion-linux", "ubuntu-latest"),
        ("interactive-completion-windows", "windows-latest"),
    ):
        job = workflow["jobs"][key]
        assert job["if"] == "${{ inputs.interactive_completion }}"
        assert job["runs-on"] == platform
        steps = job["steps"]
        tooling = next(step for step in steps if step.get("name", "").startswith("Prepare pinned PSMux"))
        assert tooling["if"] == "${{ runner.os == 'Windows' }}"
        assert "RUNNER_TEMP" in tooling["run"] and "GITHUB_PATH" in tooling["run"]
        assert "v3.4.0/psmux-v3.4.0-windows-x64.zip" in tooling["run"]
        assert "a66b6be7c13c6fa0dc3660a267b9e34eaa91a4eb7f07fbd55a0468018bd0f721" in tooling["run"]
        assert "Get-FileHash" in tooling["run"]
        native = next(step for step in steps if step.get("name") == "Qualify warm UI lifetime in a private native mux")
        assert native["if"] == "${{ runner.os == 'Linux' || runner.os == 'Windows' }}"
        assert "--allow-explicit-tiers" in native["run"]
        assert "tests/native/test_warm_picker_lifetime.py" in native["run"]
        assert "tests/test_mux_active_pane.py" in native["run"]
        assert "--allow-host-state" not in native["run"]
        assert "kill-server" not in native["run"]


def test_displaced_native_sources_and_unavailable_portfolio_remain_explicit() -> None:
    root = HERE.parent
    for source in (
        "libs/installation-context/tests/test_installation_mode_governance.py",
        "libs/installation-context/tests/test_diagnostic_consumers.py",
        "libs/payload-invocation/tests/test_command_binding.py",
        "libs/payload-invocation/tests/test_command_lifecycle.py",
        "plugins/agent-vault/tests/test_install_snapshot_ps1.py",
    ):
        assert (root / source).is_file()
    inventory = yaml.safe_load((HERE / "periodic-integration-inventory.json").read_text())
    assert inventory["required_pr_dependency"] is False
    assert inventory["release_dependency"] is False
    assert inventory["failure_owner"]["tracker"]
    windows = inventory["existing_windows_rotation"]
    assert windows["status"] == "unavailable"
    assert windows["execution_enabled"] is False
    assert windows["owner"]
    periodic = yaml.safe_load((root / inventory["workflow"]).read_text())
    assert periodic["jobs"][windows["job"]]["if"] == "${{ false }}"
    trigger = periodic.get("on", periodic.get(True))
    assert set(trigger) == {"schedule", "workflow_dispatch"}


@pytest.mark.parametrize(("name", "route"), [
    ("update-diagnostics-linux", "ubuntu-latest"),
    ("update-diagnostics-windows", "windows-latest"),
])
def test_update_diagnostics_native_packet_preserves_bounds_and_source(name, route):
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    job = workflow["jobs"][name]
    assert job["if"] == "${{ inputs.update_diagnostics }}"
    assert job["runs-on"] == route
    assert job["timeout-minutes"] == 15
    checkout = job["steps"][0]
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    identity = next(step for step in job["steps"] if step.get("id") == "source")
    assert "os.environ['GITHUB_SHA']" in identity["run"]
    tooling = next(step for step in job["steps"] if step.get("id") == "tooling")
    assert tooling["run"] == "python tools/run-plugin-tests.py agent-worktrees --prepare-only"
    families = [
        step for step in job["steps"] if step.get("name", "").startswith("Qualify ")
    ]
    commands = [step["run"] for step in families]
    retirement = next(
        step for step in families if step["name"] == "Qualify MCP identity-pinned runtime tree retirement"
    )
    assert retirement["run"].endswith(
        "--subsuite-timeout ${{ runner.os == 'Windows' && 600 || 300 }}"
    )
    expected = {
        "tests/test_update_auth.py", "tests/test_update_driver.py",
        "tests/test_update_launch_cli.py", "tests/test_update_entry.py",
        "tests/test_update_entry_windows.py",
    }
    assert all(any(path in command for command in commands) for path in expected)
    required = {
        "agent-worktrees": {
            "tests/test_update_driver_json.py", "tests/test_provision_hook_cwd.py",
            "tests/test_global_binstub_publication.py",
        },
        "worktree-manager": {
            "tests/test_update_driver_cli.py", "tests/test_manager_broker_installation.py",
            "tests/test_launch_update_requests.py", "tests/test_installation_updates.py",
            "tests/test_bootstrap_repair_recovery.py",
        },
        "agent-machines": {"tests/test_fleet_update.py", "tests/test_self_update.py"},
        "update-driver": {"tests/test_core.py", "tests/test_legacy_record_readback.py"},
        "agent-vault": {
            "tests/test_service_launch.py", "tests/test_install_service_failures.py",
        },
        "runtime-source": {
            "tests/test_runtime_source_native.py", "tests/test_source_snapshot_publication.py",
        },
        "agent-mcp": {"tests/test_source_install_transactions.py"},
        "agent-procutil": {"tests/test_retirement_custody.py"},
        "payload-invocation": {"tests/test_command_lifecycle.py", "tests/test_generate.py"},
    }
    selected = {target: set() for target in required}
    for step in families:
        command = step["run"]
        arguments = shlex.split(command)
        if arguments[:2] == ["python", "tools/retirement_custody_acceptance.py"]:
            assert arguments[2:] == ["--ephemeral-ci-consent"]
            assert "runner.os == 'Linux'" in step["if"]
        else:
            assert arguments[:2] == ["python", "tools/run-plugin-tests.py"]
            if step is not retirement:
                assert "--subsuite-timeout" not in arguments
            target = arguments[2]
            assert target in required
            if target not in {"agent-machines", "update-driver"}:
                assert "--allow-explicit-tiers" in arguments
            selected[target].update(
                arguments[index + 1] for index, value in enumerate(arguments)
                if value == "--test-file"
            )
            if target == "worktree-manager":
                assert arguments[arguments.index("-k") + 1].split(" or ") == [
                    "manual_auto_hold", "operator_repair_classification",
                    "bootstrap_retains_update_repair", "native_launch_submits_fixed_phases",
                    "native_bootstrap_hold", "target_bypasses_bootstrap",
                    "bootstrap_preserves_hold_path",
                    "engine_handoff_is_explicit_only_for_mutating_update_commands",
                    "scoped_engine_passthrough_retains_both_channels",
                ]
        assert "!cancelled() && steps.tooling.outcome == 'success'" in step["if"]
        assert not any(flag in command for flag in (
            "--test-timeout", "--timeout", "--plugin-timeout", "--allow-host-state",
        ))
    assert all(paths <= selected[target] for target, paths in required.items())
    cache = next(step for step in families if "generated_shim_external_cache_cold_and_warm" in step["run"])
    assert cache["shell"] == "pwsh"
    assert not any(step.get("continue-on-error", False) for step in job["steps"])


@pytest.mark.parametrize("matches", [False, True])
def test_update_diagnostics_checks_actual_checkout_before_reporting_source(
    monkeypatch, capsys, matches,
):
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    step = next(
        item for item in workflow["jobs"]["update-diagnostics-linux"]["steps"]
        if item.get("name") == "Record exact update qualification source"
    )
    argv = shlex.split(step["run"])
    assert argv[:2] == ["python", "-c"] and len(argv) == 3
    expected = "a" * 40
    monkeypatch.setenv("GITHUB_SHA", expected)
    monkeypatch.setattr(
        subprocess, "check_output",
        lambda *a, **kw: (expected if matches else "b" * 40) + "\n",
    )
    if matches:
        exec(compile(argv[2], "<update-workflow-identity>", "exec"), {})
        assert capsys.readouterr().out == f"source={expected}\n"
    else:
        with pytest.raises(AssertionError):
            exec(compile(argv[2], "<update-workflow-identity>", "exec"), {})
        assert not capsys.readouterr().out


@pytest.mark.parametrize("fail_on", ["tools/seasoned_uv.py", "-m"])
def test_windows_step_does_not_mask_native_admission_failure(
    tmp_path, fail_on,
) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    step = next(
        step for step in workflow["jobs"]["derived-diagnostics-windows"]["steps"]
        if step.get("name") == "All derived native copies"
    )
    body = step["run"].replace("${{ matrix.mode }}", "fixture").replace(
        "${{ matrix.pointer }}", "fixture",
    )
    assert "${{" not in body
    log = tmp_path / "commands"
    prefix = """
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
function python {
    $code = if ($args[0] -eq $env:FIXTURE_FAIL_ON) { 23 } else { 0 }
    & $env:FIXTURE_PYTHON -I -c 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.open("a").write(sys.argv[2]+"\\n"); raise SystemExit(int(sys.argv[3]))' $env:FIXTURE_COMMAND_LOG $args[0] $code
    $global:LASTEXITCODE = $LASTEXITCODE
}
"""
    command = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", prefix + body]
    result = subprocess.run(
        command,
        env={
            **os.environ, "FIXTURE_PYTHON": sys.executable.replace("\\", "/"),
            "FIXTURE_COMMAND_LOG": str(log), "FIXTURE_FAIL_ON": fail_on,
        },
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 23, result.stdout + result.stderr
    assert log.read_text().splitlines() == (
        ["tools/seasoned_uv.py", "-m"] if fail_on == "-m" else ["tools/seasoned_uv.py"]
    )


def test_derived_native_diagnostics_are_not_a_required_smoke_fallback() -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    commands = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values() for step in job.get("steps", [])
    )
    assert "test_derived_diagnostic_cli.py" not in commands
    assert "tools/run-plugin-tests.py" not in commands
    assert "tools/run-standalone-tests.py" not in commands


@pytest.mark.parametrize("platform,runner", [
    ("linux", "ubuntu-latest"), ("windows", "windows-latest"),
])
def test_candidate_profiles_remain_focused_opt_in_native_acceptance(platform, runner) -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert trigger["workflow_dispatch"]["inputs"]["candidate_profiles"]["default"] is False
    name = f"candidate-profiles-{platform}"
    job = workflow["jobs"][name]
    assert job["if"] == "${{ inputs.candidate_profiles }}"
    assert job["runs-on"] == runner
    assert job["timeout-minutes"] == 15
    assert "strategy" not in job
    execution = next(
        step for step in job["steps"]
        if step.get("name") == "Isolated role construction, origins and reuse"
    )
    assert "--test-file tests/test_python_installer_engine.py" in execution["run"]
    for selector in ("local_role", "local_package", "role_health", "selected_extras"):
        assert selector in execution["run"]
    assert not execution.get("continue-on-error", False)
    for diagnostics in ("derived-diagnostics-posix", "derived-diagnostics-windows"):
        assert "!inputs.candidate_profiles" in workflow["jobs"][diagnostics]["if"]
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    assert name not in required["jobs"]


@pytest.mark.parametrize(("plugin", "test_file", "selector"), [
    ("bridge", "test_bridge_first_use_publication.py",
     ["-k", "test_bridge_cell_stamp_preserves_global_ownership"]),
    ("dispatch", "test_dispatch_scoped_publication.py", []),
])
def test_scoped_binding_remains_an_explicit_bounded_native_portfolio(
    plugin: str, test_file: str, selector: list[str],
) -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    portfolio = f"{plugin}-binding"
    assert portfolio in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    for platform, runner in (("linux", "ubuntu-latest"), ("windows", "windows-latest")):
        name = f"{portfolio}-{platform}"
        job = workflow["jobs"][name]
        assert job["if"] == (
            f"github.event_name == 'workflow_dispatch' && inputs.portfolio == '{portfolio}'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 10
        if plugin == "bridge":
            assert job["strategy"] == {
                "fail-fast": False,
                "matrix": {"packet": [
                    "available", "pointer and not publisher", "publisher and not pointer",
                    "publisher-and-pointer and not deactivated", "deactivated",
                ]},
            }
            selector = [
                "-k", "test_bridge_cell_stamp_preserves_global_ownership and (${{ matrix.packet }})",
            ]
        else:
            assert "strategy" not in job
        executions = [shlex.split(step["run"]) for step in job["steps"] if "run" in step]
        assert executions == [[
            "python", "tools/run-plugin-tests.py", "payload-invocation",
            "--test-file", f"tests/{test_file}", *selector,
            "--allow-explicit-tiers", "--test-timeout", "60", "--timeout", "540",
        ]]
        assert name not in required["jobs"]


def test_pr_sweep_is_a_focused_manual_portfolio_not_required_ci() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "pr-sweep" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    assert trigger["workflow_dispatch"]["inputs"]["portfolio"]["default"] == "context"
    job = workflow["jobs"]["pr-sweep-linux"]
    assert job["if"] == "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'pr-sweep'"
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] == 10
    assert "strategy" not in job and job["env"]["UV_PYTHON"] == "3.12"
    execution = next(step for step in job["steps"] if "run" in step)
    assert execution["run"].count("--test-file") == 6
    assert "--test-file tests/test_pr_sweep_process.py" in execution["run"]
    assert "--test-timeout 60 --timeout 540" in execution["run"]
    assert not execution.get("continue-on-error", False)
    guard = next(step for step in job["steps"] if step.get("name", "").startswith("Qualify publication"))
    assert guard["run"].split() == [
        "timeout", "--signal=TERM", "--kill-after=10s", "120s",
        "python", "-m", "pytest", "-q", "tools/test_check_large_files.py",
        "tools/test_check_no_internal_identifiers.py",
    ]
    assert not guard.get("continue-on-error", False)
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    assert "pr-sweep-linux" not in required["jobs"]


def test_codespaces_bootstrap_remains_an_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "codespaces-bootstrap" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    for platform, runner, admission in (
        ("linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        name = f"codespaces-bootstrap-{platform}"
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'codespaces-bootstrap'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][admission] == "1" and "strategy" not in job
        assert name not in required["jobs"]
        executions = [step["run"].split() for step in job["steps"] if "run" in step]
        assert executions == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_codespaces_bootstrap.py",
             "--test-file", "tests/test_first_use_codespaces.py",
             "--test-file", "tests/test_first_use_codespaces_checkout.py",
             "--test-timeout", "900", "--timeout", "1200"],
            ["python", "tools/run-plugin-tests.py", "agent-codespaces",
             "--test-file", "tests/test_bootstrap_check_reconcile_always_on.py",
             "--test-file", "tests/test_bootstrap_driver.py",
             "--test-file", "tests/test_bridge_provider_manifest.py",
             "--test-file", "tests/test_write_session_guidance.py",
             "--test-timeout", "60", "--timeout", "240"],
        ]


def test_source_profile_acceptance_uses_existing_opt_in_and_unchanged_native_budget() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    for platform, runner in (
        ("linux", "ubuntu-latest"), ("windows", "windows-latest"), ("macos", "macos-latest"),
    ):
        job = workflow["jobs"][f"source-profiles-{platform}"]
        assert job["if"] == "${{ inputs.wheel_inputs }}"
        assert job["runs-on"] == runner
        assert job["timeout-minutes"] == 6
        commands = [step["run"] for step in job["steps"] if "run" in step]
        assert commands[:2] == [
            "python tools/seasoned_uv.py --uv uv --registry-only -- --system pytest packaging pyyaml",
            "python -m pytest -q tools/test_runtime_source_wheels.py",
        ]
        assert len(commands) == 3
        execution = job["steps"][-1]
        assert execution["shell"] == "python"
        packet = execution["run"]
        ast.parse(packet)
        assert (
            "tools/test_local_install_source.py::"
            "test_application_adapter_runs_real_install_repeat_and_retains_failed_start[no-legacy-bootstrap]"
        ) in packet
        assert (
            "tools/test_local_install_source.py::"
            "test_copied_application_qualifies_after_the_prepared_source_is_removed"
        ) in packet
        assert 'shutil.which("uv") is None' in packet
        assert "isolated_environment(os.environ, temporary)" in packet
        assert "code = run_contained(" in packet
        assert "limits=Limits(wall_seconds=300)" in packet
        assert "owner.retained or temporary.exists()" in packet
        assert "raise SystemExit(code)" in packet


def test_path_capture_diagnostics_leave_full_portfolio_unchanged() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "path-captures" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    full = workflow["jobs"]["exhaustive"]
    assert full["timeout-minutes"] == 90
    assert full["if"] == "github.event_name != 'workflow_dispatch' || inputs.portfolio == 'context'"
    diagnostic = workflow["jobs"]["path-captures"]
    assert diagnostic["runs-on"] == "ubuntu-latest"
    assert diagnostic["strategy"] == {
        "fail-fast": False,
        "matrix": {
            "phase": ["paths", "producers", "slots"],
        },
    }
    assert diagnostic["timeout-minutes"] == 20
    assert diagnostic["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'path-captures'"
    )
    windows = workflow["jobs"]["path-captures-windows"]
    assert windows["runs-on"] == "windows-latest"
    for key in ("strategy", "timeout-minutes", "if", "steps"):
        assert windows[key] == diagnostic[key]
    execution = [step for step in diagnostic["steps"] if "python -m pytest" in step.get("run", "")]
    assert len(execution) == 5
    assert execution[0]["if"] == "matrix.phase == 'paths' && runner.os == 'Linux'"
    assert [step["if"] for step in execution[1:]] == [
        "always() && matrix.phase == 'paths'",
        "always() && matrix.phase == 'paths'",
        "always() && matrix.phase == 'producers'",
        "always() && matrix.phase == 'slots'",
    ]
    assert all(not step.get("continue-on-error", False) for step in execution)
    assert all(step["env"]["INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS"] == "1" for step in execution)
    assert "test_posix_path_capture.py" in execution[1]["run"]
    assert "test_bootstrap_context_selection.py" in execution[2]["run"]
    assert "test_digest_cache_hardening.py" in execution[3]["run"]
    assert "test_exemplar_completion_writer_gates_activation_tail" in execution[3]["run"]
    assert "git archive" in execution[0]["run"]
    assert "--durations=1" in execution[0]["run"]
    selectors = [
        token for step in execution[1:] for token in step["run"].split()
        if token.startswith("libs/installation-context/tests/")
    ]
    assert len(selectors) == len(set(selectors))
    assert set(selectors) == {
        "libs/installation-context/tests/test_json_query_bytes.py",
        "libs/installation-context/tests/test_posix_path_capture.py",
        "libs/installation-context/tests/test_installation_context_posix.py",
        "libs/installation-context/tests/test_directory_evidence_cost.py",
        "libs/installation-context/tests/test_vendoring.py",
        "libs/installation-context/tests/test_bootstrap_context_selection.py",
        "libs/installation-context/tests/test_digest_cache_hardening.py",
        *(
            "libs/installation-context/tests/test_snapshot_provenance.py::" + name
            for name in (
                "test_runtime_slot_serializes_concurrent_publishers",
                "test_agent_machines_snapshot_publication_failure_is_retryable",
                "test_exemplar_completion_writer_gates_activation_tail",
                "test_exemplar_producer_evidence_is_accepted_without_activation_mutation",
                "test_agent_machines_fixed_identity_cutover_adapter",
                "test_exemplar_producer_hash_failure_does_not_omit_payload_hash",
                "test_exemplar_payload_hash_rejects_tree_membership_change",
                "test_exemplar_slot_actions_release_installed_payload_cwd_without_legacy_staging",
                "test_exemplar_slot_actions_do_not_adopt_ambient_context",
                "test_exemplar_slot_actions_reject_foreign_snapshot_payload",
                "test_exemplar_slot_actions_reject_spoofed_staging_payload_identity",
                "test_agent_machines_powershell_watchdog_bounds_cell_lock_wait",
            )
        ),
    }
    assert all("-n 2 --dist=load" in step["run"] for step in execution[1:])
    assert all("-k " not in step["run"] and "--maxfail" not in step["run"] for step in execution)


def test_complete_native_platform_qualification_keeps_scope_and_deadline() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "context-windows" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    windows = workflow["jobs"]["exhaustive-windows"]
    assert windows["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'context-windows'"
    )
    assert windows["runs-on"] == "windows-latest"
    for name in ("exhaustive", "exhaustive-windows"):
        job = workflow["jobs"][name]
        assert job["timeout-minutes"] == 90
        assert not job.get("continue-on-error", False)
        tooling = [step["run"] for step in job["steps"] if "seasoned_uv.py" in step.get("run", "")]
        assert len(tooling) == 1 and "pytest-xdist==3.8.0" in tooling[0]
        execution = [step for step in job["steps"] if "python -m pytest" in step.get("run", "")]
        assert len(execution) == 1
        assert execution[0]["env"]["INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS"] == "1"
        command = execution[0]["run"]
        assert command.strip().endswith("libs/installation-context/tests")
        assert "-n 2 --dist=load" in command
        assert "-k " not in command and "--maxfail" not in command


def test_full_derived_native_acceptance_has_explicit_bounded_matrix() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {
        "watch_owner", "dispatch_delegation", "pr_service", "ssh_service", "pr_artifacts", "dependency_compile",
        "candidate_profiles", "ssh_snapshot", "wheel_inputs",
        "guardian_followthrough", "guardian_ref",
        "runtime_profiles", "chat_construction", "update_diagnostics",
        "immutable_runtime", "immutable_runtime_ref",
        "interactive_completion", "native_launch", "ssh_service",
        "ssh_headless", "update_diagnostics",
        "runtime_removal", "source_cache", "uv_deployment_compatibility",
        "windows_broker_probe", "windows_broker_consumers", "native_daemon_exit",
    }
    assert inputs["watch_owner"]["type"] == "boolean"
    assert inputs["watch_owner"]["default"] is False
    assert inputs["ssh_service"]["type"] == "boolean"
    assert inputs["ssh_service"]["default"] is False
    assert inputs["update_diagnostics"]["type"] == "boolean"
    assert inputs["update_diagnostics"]["default"] is False
    assert inputs["uv_deployment_compatibility"]["type"] == "boolean"
    assert inputs["uv_deployment_compatibility"]["default"] is False
    routes = {
        "derived-diagnostics-posix": "ubuntu-latest",
        "derived-diagnostics-windows": "windows-latest",
    }
    assert set(workflow["jobs"]) == set(routes) | {
        "watch-owner-native-linux", "watch-owner-native-windows",
        "watch-owner-macos-refusal",
        "watch-owner-boundaries-linux", "watch-owner-boundaries-windows",
        "dispatch-delegation-linux", "dispatch-delegation-windows",
        "pr-service-linux", "pr-service-windows",
        "update-diagnostics-linux", "update-diagnostics-windows",
        "ssh-service-linux", "ssh-service-windows",
        "pr-artifacts-linux", "pr-artifacts-windows",
        "gateway-role-tunnels", "gateway-host-application-linux", "gateway-host-application-windows",
        "gateway-continuity-application-linux", "gateway-continuity-application-windows",
        "dependency-compile-linux", "dependency-compile-windows",
        "source-build-linux", "source-build-windows",
        "source-cache-linux", "source-cache-windows",
        "uv-deployment-compatibility-linux", "uv-deployment-compatibility-windows",
        "marketplace-source-linux", "marketplace-source-windows",
        "packaging-seed-linux", "packaging-seed-windows",
        "installer-latency-linux", "installer-latency-windows",
        "packaging-seed-linux", "packaging-seed-windows",
        "logger-installer-latency-linux", "logger-installer-latency-windows",
        "candidate-profiles-linux", "candidate-profiles-windows",
        "chat-construction-linux", "chat-construction-windows",
        "ssh-snapshot-native-linux", "ssh-snapshot-native-windows",
        "wheel-inputs-linux", "wheel-inputs-windows",
        "source-profiles-linux", "source-profiles-windows",
        "source-profiles-macos",
        "guardian-followthrough-macos",
        "runtime-profile-policy-linux", "runtime-profile-policy-windows",
        "immutable-runtime-linux", "immutable-runtime-macos",
        "interactive-completion-linux", "interactive-completion-macos",
        "interactive-completion-windows",
        "native-launch-windows",
        "worktree-entry-sync-linux", "worktree-entry-sync-windows",
        "native-seed-reply-linux", "native-seed-reply-macos",
        "ssh-headless-linux", "ssh-headless-windows",
        "update-diagnostics-linux", "update-diagnostics-windows",
        "packaging-seed-linux", "packaging-seed-windows",
        "runtime-removal-macos", "runtime-removal-windows",
        "windows-broker-portable", "windows-broker-probe", "windows-broker-consumers",
        "windows-native-process",
        "native-daemon-exit-windows",
        "uv-deployment-compatibility-linux", "uv-deployment-compatibility-windows",
    }
    for name, runner in routes.items():
        job = workflow["jobs"][name]
        assert job["if"] == (
            "${{ !inputs.interactive_completion && !inputs.native_launch && !inputs.immutable_runtime "
            "&& !inputs.guardian_followthrough && !inputs.watch_owner "
            "&& !inputs.dispatch_delegation && !inputs.pr_service && !inputs.ssh_service "
            "&& !inputs.pr_artifacts && !inputs.dependency_compile && !inputs.source_cache "
            "&& !inputs.uv_deployment_compatibility && !inputs.candidate_profiles "
            "&& !inputs.chat_construction && !inputs.ssh_snapshot && !inputs.ssh_headless "
            "&& !inputs.wheel_inputs && !inputs.runtime_profiles && !inputs.update_diagnostics "
            "&& !inputs.runtime_removal && !inputs.windows_broker_probe && !inputs.windows_broker_consumers "
            "&& !inputs.native_daemon_exit }}"
        )
        assert job["runs-on"] == runner
        assert job["timeout-minutes"] == 4
        matrix = job["strategy"]["matrix"]
        assert set(matrix) == {"mode", "pointer"}
        assert set(matrix["mode"]) == {"mode_legacy", "mode_active"}
        assert set(matrix["pointer"]) == {"pointer_absent", "pointer_retained"}
        assert job["strategy"]["fail-fast"] is False
        step = next(step for step in job["steps"] if step.get("name") == "All derived native copies")
        assert "test_derived_diagnostic_cli.py" in step["run"]
        assert '-k "${{ matrix.mode }} and ${{ matrix.pointer }}"' in step["run"]
        assert "-m " not in step["run"].rsplit("pytest", 1)[1]
        if runner == "windows-latest":
            assert step["env"]["INSTALLATION_CONTEXT_POWERSHELL"] == "powershell"


def test_chat_floor_matrix_keeps_python310_tests_with_python311_runner_bootstrap():
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    for key in ("chat-construction-linux", "chat-construction-windows"):
        job = workflow["jobs"][key]
        assert job["strategy"]["matrix"]["python"] == ["3.10", "3.11"]
        steps = job["steps"]
        selected = next(step for step in steps if step.get("id") == "chat-python")
        bootstrap = next(step for step in steps if step.get("name") == "Python 3.11 runner bootstrap")
        acceptance = next(step for step in steps if step.get("name") == "Independent artifacts and native Chat acceptance")
        assert selected["with"]["python-version"] == "${{ matrix.python }}"
        assert bootstrap["with"]["python-version"] == "3.11"
        assert steps.index(selected) < steps.index(bootstrap) < steps.index(acceptance)
        assert acceptance["env"]["UV_PYTHON"] == "${{ steps.chat-python.outputs.python-path }}"


def test_constructor_groups_and_mutations_move_to_existing_lanes_without_dropping_cache_scope():
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    marker = "backend_mutation_cannot_publish_cache_or_completion_and_retry_is_clean"
    refusal_terms = [
        marker, "shared_executables_keep_the_original_transaction_before_cache_effects",
        "changed_retained_archive_refuses_before_complete_stamp",
        "installed_missing_content_refuses_before_preparation_and_complete_stamp",
        "request_custody_is_not_a_build_input_but_build_environment_still_is",
        "control_and_target_share_only_verified_installation_wheels",
        "canonical_setuptools_setup_boilerplate_keeps_the_graph_cacheable",
    ]
    refusal_expression = " or ".join(refusal_terms)
    groups = (
        "distinct_build_requirements_get_separate_tooling_and_cached_wheels or "
        "all_requirement_groups_are_acquired_before_the_feed_becomes_unavailable or "
        "offline_builds_keep_native_cache_selection_and_caller_relative_interpreter"
    )
    expected_files = {"tools/test_installer_source_build.py", "tools/test_runtime_source_wheel_cache.py"}
    for key in ("source-build-linux", "source-build-windows"):
        job = workflow["jobs"][key]
        assert job["timeout-minutes"] == 6
        matrix = job["strategy"]["matrix"]
        assert matrix["suite"] == ["profiles", "refusals", "manager", "normal", "toolkit"]
        normal = next(item for item in matrix["include"] if item["suite"] == "normal")
        assert normal["normal_expression"] == f"not ({refusal_expression} or {groups})"
        profiles = next(item for item in matrix["include"] if item["suite"] == "profiles")
        refusals = next(item for item in matrix["include"] if item["suite"] == "refusals")
        assert profiles["constructor_expression"] == groups
        assert refusals["constructor_expression"] == refusal_expression
        admission = next(step for step in job["steps"] if step.get("name", "").startswith("Real authenticated source,"))
        assert admission["if"] == "${{ matrix.suite == 'profiles' || matrix.suite == 'refusals' }}"
        assert shlex.split(admission["run"]) == [
            "python", "-m", "pytest", "-q",
            "tools/test_runtime_source_build.py", "tools/test_runtime_source_wheel_cache.py",
            "-k", "${{ matrix.expression }}",
        ]
        toolkit = next(step for step in job["steps"] if step.get("name") == "Copied admission toolkit and fresh interpreter")
        assert toolkit["if"] == "${{ matrix.suite == 'toolkit' }}"
        assert shlex.split(toolkit["run"]) == [
            "python", "-m", "pytest", "-q", "tools/test_runtime_admission_toolkit.py",
            "libs/payload-invocation/tests/test_runtime_admission.py",
        ]
        execution = next(step for step in job["steps"] if step.get("name", "").startswith("Ordinary constructors,"))
        assert set(shlex.split(execution["run"])[4:6]) == expected_files
        assert '${{ matrix.normal_expression || \'\' }}' in execution["run"]
        refusal = next(step for step in job["steps"] if step.get("name", "").startswith("Ordinary constructor profiles"))
        assert refusal["if"] == "${{ matrix.suite == 'profiles' || matrix.suite == 'refusals' }}"
        assert shlex.split(refusal["run"]) == [
            "python", "-m", "pytest", "-q", "tools/test_installer_source_build.py",
            "-k", "${{ matrix.constructor_expression }}",
        ]
        backend = next(step for step in job["steps"] if step.get("name", "").startswith("Acquire the pinned real backend"))
        assert backend["if"] == "${{ matrix.suite != 'manager' && matrix.suite != 'toolkit' }}"
        assert job["steps"].index(backend) < job["steps"].index(refusal)
    for key in ("source-cache-linux", "source-cache-windows"):
        matrix = workflow["jobs"][key]["strategy"]["matrix"]
        assert matrix == {"suite": ["normal"]}
    for name in expected_files:
        functions = {
            node.name for node in ast.walk(ast.parse((HERE.parent / name).read_text()))
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        }
        moved = {
            name for name in functions
            if any(term in name for term in [*refusal_terms, *groups.split(" or ")])
        }
        if name.endswith("test_runtime_source_wheel_cache.py"):
            assert not moved
        else:
            assert moved == {f"test_{term}" for term in [*refusal_terms, *groups.split(" or ")]}


@pytest.mark.parametrize("job_key", ["source-build-linux", "source-build-windows"])
@pytest.mark.parametrize("failure_at", [None, 0, 1])
def test_manager_native_step_retains_owned_cleanup_and_child_failure(
    tmp_path, monkeypatch, job_key, failure_at,
) -> None:
    import plugin_test_containment as containment

    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    job = workflow["jobs"][job_key]
    step = next(
        step for step in job["steps"]
        if step.get("name") == "Persisted Manager admission and fresh-process offline reuse"
    )
    assert step["shell"] == "python"
    calls = []

    def run(command, *, cwd, env, sandbox, limits):
        assert cwd == tmp_path and sandbox.is_dir()
        assert Path(env["HOME"]).resolve().is_relative_to(sandbox.resolve())
        source_paths = [str(tmp_path / "tools")]
        if calls:
            source_paths.extend([
                str(tmp_path / "worktree-manager" / "src"),
                str(tmp_path / "libs" / "agent-procutil" / "src"),
                str(tmp_path / "libs" / "worktree-identity" / "src"),
                str(tmp_path / "libs" / "update-driver" / "src"),
                str(tmp_path / "libs" / "single-instance-lease" / "src"),
            ])
        assert env["PYTHONPATH"] == os.pathsep.join(source_paths)
        calls.append((command, sandbox, limits))
        return 23 if failure_at == len(calls) - 1 else 0

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr(containment, "run_contained", run)
    with pytest.raises(SystemExit) as result:
        exec(compile(step["run"], "<manager-native-workflow>", "exec"), {})
    assert result.value.code == (0 if failure_at is None else 23)
    assert len(calls) == (1 if failure_at == 0 else 2)
    assert not any(sandbox.exists() for _command, sandbox, _limits in calls)
    assert calls[0][0][:4] == [sys.executable, "-m", "pytest", "-q"]
    assert calls[0][0][4:-2] == [
        "tools/test_manager_source_install.py",
        "tools/test_manager_source_native.py",
        "tools/test_runtime_source_reuse.py",
        "worktree-manager/tests/test_manager_update_check.py",
        "libs/versioned-runtime/tests/test_runtime_source_identity.py::test_source_and_repair_slots_preserve_the_declared_package_version",
    ]
    assert [limits.wall_seconds for _command, _sandbox, limits in calls] == [300, 120][:len(calls)]
    if len(calls) == 2:
        assert calls[1][0][4:-2] == ["worktree-manager/tests/test_runtime_source.py"]
    assert job["timeout-minutes"] == 6


def test_manager_native_step_loads_first_party_helpers_without_site_packages(monkeypatch) -> None:
    import plugin_test_containment as containment

    repo = HERE.parent
    workflow = yaml.safe_load(
        (repo / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    step = next(
        step for step in workflow["jobs"]["source-build-linux"]["steps"]
        if step.get("name") == "Persisted Manager admission and fresh-process offline reuse"
    )
    original_run = containment.run_contained
    calls = []
    script = """
import pathlib, sys
import agent_procutil
import worktree_identity
import update_driver
import single_instance_lease
from worktree_manager import runtime_source
repo = pathlib.Path(sys.argv[1])
assert pathlib.Path(agent_procutil.__file__).resolve() == (
    repo / "libs" / "agent-procutil" / "src" / "agent_procutil" / "__init__.py"
).resolve()
assert pathlib.Path(runtime_source.__file__).resolve() == (
    repo / "worktree-manager" / "src" / "worktree_manager" / "runtime_source.py"
).resolve()
assert pathlib.Path(worktree_identity.__file__).resolve() == (
    repo / "libs" / "worktree-identity" / "src" / "worktree_identity" / "__init__.py"
).resolve()
assert pathlib.Path(update_driver.__file__).resolve() == (
    repo / "libs" / "update-driver" / "src" / "update_driver" / "__init__.py"
).resolve()
assert pathlib.Path(single_instance_lease.__file__).resolve() == (
    repo / "libs" / "single-instance-lease" / "src" / "single_instance_lease" / "__init__.py"
).resolve()
print("manager-source-helpers-without-site-packages-ok")
"""

    def run(command, **kwargs):
        calls.append(command)
        probe = script if len(calls) == 2 else "print('original-admission-path-retained')"
        return original_run(
            [sys.executable, "-S", "-c", probe, str(repo)], **kwargs,
        )

    monkeypatch.chdir(repo)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setattr(containment, "run_contained", run)
    with pytest.raises(SystemExit) as result:
        exec(compile(step["run"], "<manager-native-workflow>", "exec"), {})
    assert result.value.code == 0
    assert len(calls) == 2


def test_real_broker_consumers_retain_native_removal_failures_without_expanding_other_lanes() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    jobs = workflow["jobs"]
    native = jobs["native-daemon-exit-windows"]
    assert native["if"] == "${{ inputs.native_daemon_exit }}"
    assert native["runs-on"] == "windows-latest" and native["timeout-minutes"] == 15
    assert not native.get("continue-on-error")
    assert native["env"] == {"PROCUTIL_NATIVE_DAEMON_LIFETIME": "1", "UV_PYTHON": "3.12"}
    native_commands = [step["run"] for step in native["steps"] if "run" in step]
    assert len(native_commands) == 1
    assert shlex.split(native_commands[0]) == [
        "python", "tools/run-plugin-tests.py", "agent-procutil",
        "--test-file", "tests/test_native_daemon_cleanup.py",
        "--test-file", "tests/test_native_daemon.py",
        "--test-timeout", "60", "--timeout", "600", "--plugin-timeout", "720",
    ]
    assert jobs["windows-broker-probe"]["timeout-minutes"] == 4
    assert jobs["windows-broker-portable"]["timeout-minutes"] == 6
    consumer = jobs["windows-broker-consumers"]
    assert consumer["timeout-minutes"] == 15 and consumer["runs-on"] == "windows-latest"
    assert consumer["if"] == (
        "${{ (inputs.windows_broker_probe || inputs.windows_broker_consumers) "
        "&& !inputs.watch_owner && !inputs.dispatch_delegation }}"
    )
    assert not consumer.get("continue-on-error")
    checkout = next(step for step in consumer["steps"] if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"]["persist-credentials"] is False
    execution = next(
        step for step in consumer["steps"] if step.get("name") == "Complete native finite removal portfolio"
    )
    assert execution["if"] == "${{ !cancelled() && steps.finite-tooling.outcome == 'success' }}"
    assert not execution.get("continue-on-error")
    assert execution["env"] == {"PYTEST_ADDOPTS": "--timeout=90"}
    assert shlex.split(execution["run"]) == [
        "python", "-m", "pytest", "-q", "--durations=10",
        "tools/test_installer_engine_uninstall.py", "tools/test_runtime_uninstall_process.py",
        "tools/test_uninstall_path_candidates.py",
    ]


def test_logger_batch_acceptance_is_optional_and_bounded() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    for platform, runner in (("linux", "ubuntu-latest"), ("windows", "windows-latest")):
        job = workflow["jobs"][f"logger-installer-latency-{platform}"]
        assert job["if"] == "${{ inputs.dependency_compile }}"
        assert job["runs-on"] == runner and job["timeout-minutes"] == 6
        command = next(step["run"] for step in job["steps"] if "run" in step)
        assert "agent-logger" in command and "--allow-explicit-tiers" in command
        assert "--test-file tests/test_install_batch.py" in command
        assert "--test-timeout 300 --timeout 300" in command
        assert not job.get("continue-on-error")


def test_foundation_snapshot_staging_is_disposable_and_bounded() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    for name, runner in (
        ("foundation-linux", "ubuntu-latest"), ("foundation-windows", "windows-latest"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'foundation'"
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        step = next(
            entry for entry in job["steps"]
            if entry.get("name") == "Qualify selected snapshot staging and ordinary-copy refusal"
        )
        assert step["env"] == {"APR_NATIVE_OS_ACCOUNT_ACCEPTANCE": "1"}
        assert step["run"].split() == [
            "python", "tools/run-plugin-tests.py", "agent-pull-requests",
            "--allow-explicit-tiers", "--allow-host-state",
            "--test-file", "tests/test_cell_invocation.py",
            "-k", '"native_staged_snapshot', "or", "legacy_staging_reentry", "or", 'frozen_input"',
            "--test-timeout", "60", "--timeout", "300",
        ]


def test_pull_requests_command_adoption_uses_explicit_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "pull-requests-commands" in (
        trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    )
    assert workflow["jobs"]["exhaustive"]["if"] == (
        "github.event_name != 'workflow_dispatch' || inputs.portfolio == 'context'"
    )
    for name, runner in (
        ("pull-requests-command-linux", "ubuntu-latest"),
        ("pull-requests-command-windows", "windows-latest"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'pull-requests-commands'"
        )
        assert job["timeout-minutes"] == 25
        assert job["runs-on"] == runner and not job.get("strategy")
        steps = [step["run"] for step in job["steps"] if "run" in step]
        assert len(steps) == 2
        assert steps[0].split() == [
            "python", "tools/run-plugin-tests.py", "update-driver",
            "--test-file", "tests/test_first_use_pull_requests_command.py",
            "--test-timeout", "900", "--timeout", "1080",
        ]
        assert steps[1].split() == [
            "python", "tools/run-plugin-tests.py", "agent-pull-requests",
            "--test-file", "tests/test_cell_invocation.py",
            "-k", '"dispatch_preserves', "or", 'command_cannot"',
            "--test-timeout", "60", "--timeout", "180",
        ]
        assert not job.get("continue-on-error")


@pytest.mark.parametrize(("platform", "runner", "opt_in"), [
    ("linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
    ("windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
])
def test_bridge_commands_partition_real_native_cases_without_relaxing_bounds(
    platform, runner, opt_in,
) -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "bridge-commands" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    job = workflow["jobs"][f"bridge-commands-{platform}"]
    assert job["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'bridge-commands'"
    )
    assert job["timeout-minutes"] == 35 and not job.get("continue-on-error")
    assert job["runs-on"] == runner
    assert job["strategy"]["fail-fast"] is False
    matrix = job["strategy"]["matrix"]
    assert set(matrix) == ({"packet", "include"} if platform == "windows" else {"packet"})
    packets = {packet["name"]: packet["select"] for packet in matrix["packet"]}
    assert packets == {
        "refusal-driver": "refusal and (missing-driver or old-driver or old-resolver or old-dispatcher)",
        "refusal-policy": "refusal and (managed or malformed-policy or no-self or nested)",
        "refusal-source": "refusal and (unselected or unavailable or missing-provenance or malformed-provenance)",
        "refusal-context": "refusal and (pinned or custom-root or cell or ordinary-copy)",
        **{entry: f"original_operation and {entry}" for entry in ("payload", "primary", "ordinary")},
        "concurrent": "concurrent", "failed": "failed", "transport": "cold_wait",
    }
    if platform == "windows":
        assert matrix["include"] == [{
            "packet": {"name": "cmd", "select": "original_operation and cmd"},
        }]
    assert job["env"][opt_in] == "1"
    execution = next(step["run"] for step in job["steps"] if "run" in step)
    assert shlex.split(execution) == [
        "python", "tools/run-plugin-tests.py", "update-driver",
        "--test-file", "tests/test_first_use_bridge_command.py",
        "-k", "${{ matrix.packet.select }}", "--test-timeout", "900", "--timeout", "1080",
    ]
    publication = workflow["jobs"][f"bridge-command-publication-{platform}"]
    assert publication["if"] == job["if"] and publication["timeout-minutes"] == 35
    assert not publication.get("continue-on-error")
    assert publication["runs-on"] == runner
    assert publication["strategy"]["fail-fast"] is False
    assert publication["strategy"]["matrix"] == {
        "packet": [
            "cell_stamp and available", "cell_stamp and pointer and not publisher",
            "cell_stamp and publisher and not pointer",
            "cell_stamp and publisher-and-pointer and not deactivated",
            "cell_stamp and deactivated",
            "actual and legacy and payload", "actual and selected-cell and payload",
            "actual and catalog", "actual and stamped-global",
            "actual and stamped-unavailable", "actual and stamped-no-pointer",
        ],
    }
    execution = next(step["run"] for step in publication["steps"] if "run" in step)
    assert shlex.split(execution) == [
        "python", "tools/run-plugin-tests.py", "payload-invocation", "--allow-explicit-tiers",
        "--test-file", "tests/test_bridge_first_use_publication.py", "-k", "${{ matrix.packet }}",
        "--test-timeout", "60", "--timeout", "900",
    ]
    controls = workflow["jobs"][f"bridge-command-controls-{platform}"]
    assert controls["if"] == job["if"] and controls["timeout-minutes"] == 35
    assert not controls.get("continue-on-error")
    assert controls["runs-on"] == runner and "strategy" not in controls
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    for name in ("bridge-commands", "bridge-command-publication", "bridge-command-controls"):
        assert f"{name}-{platform}" not in required["jobs"]


def test_logger_command_adoption_uses_explicit_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "logger-commands" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    for platform, runner, opt_in in (
        ("linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        name = f"logger-commands-{platform}"
        job = workflow["jobs"][name]
        assert name not in required["jobs"] and not job.get("strategy")
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'logger-commands'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 35
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        execution = next(step for step in job["steps"] if step.get("name") == (
            "Qualify real command admission, caller loss and installed consumers"
        ))
        assert execution["env"]["PYTEST_ADDOPTS"] == "--capture=tee-sys"
        assert [shlex.split(step["run"]) for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_logger_command.py",
             "--test-timeout", "900", "--timeout", "1800"],
            ["python", "tools/run-plugin-tests.py", "agent-logger",
             "--test-file", "tests/test_runtime_gate.py",
             "--test-file", "tests/test_install_binstub.py",
             "--max-files-per-sub-suite", "1", "--test-timeout", "120", "--timeout", "600"],
        ]


def test_logger_bootstrap_windows_keeps_disjoint_storage_packets() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    for platform, runner, opt_in in (
        ("linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        name = f"logger-bootstrap-{platform}"
        job = workflow["jobs"][name]
        assert name not in required["jobs"] and not job.get("strategy")
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'logger-bootstrap'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
    execution = [step for step in workflow["jobs"]["logger-bootstrap-windows"]["steps"] if "run" in step]
    consumer = "test_actual_logger_cold_concurrent_install_keeps_selection_and_six_installed_consumers"
    assert [shlex.split(step["run"]) for step in execution] == [
        ["python", "tools/run-plugin-tests.py", "update-driver",
         "--test-file", "tests/test_first_use_logger.py", "-k", f"not {consumer}",
         "--max-files-per-sub-suite", "1", "--test-timeout", "900", "--timeout", "1200"],
        ["python", "tools/run-plugin-tests.py", "update-driver",
         "--test-file", "tests/test_first_use_logger_bootstrap.py",
         "--test-timeout", "900", "--timeout", "1200"],
        ["python", "tools/run-plugin-tests.py", "update-driver",
         "--test-file", "tests/test_first_use_logger.py", "-k", consumer,
         "--test-timeout", "900", "--timeout", "1200"],
        ["python", "tools/run-plugin-tests.py", "agent-logger",
         "--test-file", "tests/test_bootstrap_check_reconcile_always_on.py",
         "--test-file", "tests/test_bootstrap_driver.py", "--test-file", "tests/test_install_binstub.py",
         "--max-files-per-sub-suite", "1", "--test-timeout", "180", "--timeout", "420"],
    ]
    assert execution[1]["env"] == {"PYTEST_ADDOPTS": "--capture=tee-sys"}
    linux_execution = [
        step for step in workflow["jobs"]["logger-bootstrap-linux"]["steps"] if "run" in step
    ]
    assert [shlex.split(step["run"]) for step in linux_execution] == [
        shlex.split(step["run"]) for step in execution
    ]


def test_logger_snapshot_fixture_outer_budget_preserves_inner_deadlines() -> None:
    source = HERE.parent / "plugins/agent-logger/tests/test_install_binstub.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    stamp = next(
        node.value for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_STAMP_TIMEOUT_SECONDS"
                for target in node.targets)
    )
    assert ast.unparse(stamp) == "90 if os.name == 'nt' else 60"
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "test_stamp_supports_first_use_provision_from_snapshot_only"
    )
    outer = next(
        decorator.args[0] for decorator in function.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "timeout"
    )
    inner = [
        ast.unparse(keyword.value)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr == "run"
        for keyword in node.keywords if keyword.arg == "timeout"
    ]
    assert sorted(inner) == ["420", "_STAMP_TIMEOUT_SECONDS"]
    assert ast.unparse(outer) == "630 if os.name == 'nt' else 600"
    for allowance, stamp_limit in ((outer.body.value, stamp.body.value),
                                   (outer.orelse.value, stamp.orelse.value)):
        assert allowance == stamp_limit + 420 + 120


def test_codespaces_recipes_remain_manual_and_bounded() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "codespaces-recipes" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("codespaces-recipes-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("codespaces-recipes-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'codespaces-recipes'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        assert [step["run"].split() for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_codespaces.py",
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_genesis.py", "-k", "owning_codespaces",
             "--test-timeout", "900", "--timeout", "1080"],
        ]


def test_logger_selected_snapshots_remain_manual_with_bounded_cleanup() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    for name, runner, opt_in in (
        ("logger-recipes-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("logger-recipes-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'logger-recipes'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        assert [shlex.split(step["run"]) for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_logger.py",
             "--test-file", "tests/test_first_use_logger_snapshot.py",
             "--max-files-per-sub-suite", "1", "--test-timeout", "900", "--timeout", "1200"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_genesis.py", "-k", "owning_logger or selected_logger_snapshot",
             "--test-timeout", "900", "--timeout", "1080"],
        ]


def test_ssh_recipes_use_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "ssh-recipes" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("ssh-recipes-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("ssh-recipes-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'ssh-recipes'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        commands = [step["run"].split() for step in job["steps"] if "run" in step]
        assert commands == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_ssh.py",
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_genesis.py", "-k", "owning_ssh",
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "agent-ssh",
             "--test-file", "tests/test_bootstrap_check_reconcile_always_on.py",
             "--test-file", "tests/test_bootstrap_driver.py",
             "--test-timeout", "60", "--timeout", "240"],
        ]


def test_bridge_snapshots_use_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "bridge-snapshots" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("bridge-snapshots-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("bridge-snapshots-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'bridge-snapshots'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 35
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        bootstrap = next(step for step in job["steps"]
                         if step.get("name") == "Preserve canonical-installed-only bootstrap publication")
        assert bootstrap["env"]["UPDATE_DRIVER_BRIDGE_BOOTSTRAP_ACCEPTANCE"] == "1"
        assert [step["run"].split() for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge_snapshot.py",
             "-k", '"not', 'actual"', "--test-timeout", "90", "--timeout", "600"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge_snapshot.py",
             "-k", '"actual', "and", "not", 'actual_stamp"', "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "runtime-source",
             "--test-file", "tests/test_prepare_runtime_source.py",
             "--test-file", "tests/test_source_snapshot_publication.py",
             "--test-timeout", "120", "--timeout", "600"],
            ["python", "tools/run-plugin-tests.py", "agent-procutil",
             "--test-file", "tests/test_native_daemon_shell.py",
             "-k", "waitable", "--test-timeout", "90", "--timeout", "180"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge_snapshot.py",
             "-k", "actual_stamp", "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge.py",
             "-k", '"not', 'native_caller_loss"', "--test-timeout", "900", "--timeout", "900"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_logger_snapshot.py",
             "-k", '"not', 'actual"', "--test-timeout", "90", "--timeout", "600"],
            ["python", "tools/run-plugin-tests.py", "agent-bridge",
             "--test-file", "tests/test_bootstrap_driver.py",
             "--test-timeout", "60", "--timeout", "180"],
        ]


def test_bridge_recipes_use_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "bridge-recipes" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("bridge-recipes-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("bridge-recipes-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'bridge-recipes'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 35
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        assert [step["run"].split() for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge.py",
             "-k", '"not', 'native_caller_loss"',
             "--test-timeout", "900", "--timeout", "900"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge.py",
             "-k", '"native_caller_loss', "and", 'provision"',
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge.py",
             "-k", '"native_caller_loss', "and", 'install"',
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "agent-bridge",
             "--test-file", "tests/test_install_ps1_s4u_recovery.py",
             "--test-file", "tests/test_install_lifecycle_emit.py",
             "--test-timeout", "60", "--timeout", "180"],
        ]


def test_bridge_bootstrap_uses_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "bridge-bootstrap" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("bridge-bootstrap-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("bridge-bootstrap-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'bridge-bootstrap'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 35
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        assert job["env"]["UPDATE_DRIVER_BRIDGE_BOOTSTRAP_ACCEPTANCE"] == "1"
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        assert [step["run"].split() for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge_bootstrap.py",
             "-k", '"not', 'hook_deadline"', "--test-timeout", "900", "--timeout", "900"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge_bootstrap.py",
             "-k", "hook_deadline", "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_bridge.py",
             "-k", '"not', 'native_caller_loss"', "--test-timeout", "900", "--timeout", "900"],
            ["python", "tools/seasoned_uv.py", "--uv", "uv", "--registry-only",
             "--", "--system", "pytest", "packaging"],
            ["python", "-m", "pytest", "-q",
             "libs/versioned-runtime/tests/test_bootstrap_selected_readiness.py"],
            ["python", "tools/run-plugin-tests.py", "agent-bridge",
             "--test-file", "tests/test_bootstrap_driver.py",
             "--test-file", "tests/test_install_ps1_s4u_recovery.py",
             "--test-file", "tests/test_install_lifecycle_emit.py",
             "--test-timeout", "60", "--timeout", "360"],
            ["python", "tools/run-plugin-tests.py", "payload-invocation", "--allow-explicit-tiers",
             "--test-file", "tests/test_bootstrap_publication.py", "-k", "agent-bridge",
             "--test-timeout", "120", "--timeout", "600"],
        ]


def test_ssh_commands_use_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "ssh-commands" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("ssh-commands-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("ssh-commands-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'ssh-commands'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        assert [step["run"].split() for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_ssh_command.py",
             "--test-timeout", "900", "--timeout", "1200"],
            ["python", "tools/run-plugin-tests.py", "agent-ssh",
             "--test-file", "tests/test_payload_invocation.py",
             "--test-timeout", "60", "--timeout", "180"],
        ]


def test_ssh_bootstrap_uses_explicit_bounded_native_portfolio() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "ssh-bootstrap" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    for name, runner, opt_in in (
        ("ssh-bootstrap-linux", "ubuntu-latest", "UPDATE_DRIVER_NATIVE_LINUX_GENESIS"),
        ("ssh-bootstrap-windows", "windows-latest", "UPDATE_DRIVER_NATIVE_FOUNDATION_GENESIS"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'ssh-bootstrap'"
        )
        assert job["runs-on"] == runner and job["timeout-minutes"] == 25
        assert job["env"][opt_in] == "1" and not job.get("continue-on-error")
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"]["persist-credentials"] is False
        assert [step["run"].split() for step in job["steps"] if "run" in step] == [
            ["python", "tools/run-plugin-tests.py", "update-driver",
             "--test-file", "tests/test_first_use_ssh_bootstrap.py",
             "--test-timeout", "900", "--timeout", "1080"],
            ["python", "tools/run-plugin-tests.py", "agent-ssh",
             "--test-file", "tests/test_bootstrap_check_reconcile_always_on.py",
             "--test-file", "tests/test_bootstrap_driver.py",
             "--test-file", "tests/test_payload_invocation.py", "-k", '"bootstrap"',
             "--test-timeout", "60", "--timeout", "180"],
            ["python", "tools/run-plugin-tests.py", "agent-ssh",
             "--test-file", "tests/test_install_snapshot.py", "-k", "checkout_stamp",
             "--test-timeout", "120", "--timeout", "240"],
        ]


def test_worktree_state_durability_is_manual_native_and_source_pinned() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-full.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    assert "worktree-state" in trigger["workflow_dispatch"]["inputs"]["portfolio"]["options"]
    job = workflow["jobs"]["worktree-state-windows"]
    assert job["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'worktree-state'"
    )
    assert job["runs-on"] == "windows-latest" and job["timeout-minutes"] == 5
    assert not job.get("continue-on-error")
    checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    commands = [step for step in job["steps"] if "run" in step]
    assert commands[0]["env"]["EXPECTED_SOURCE"] == "${{ github.sha }}"
    assert "assert actual == os.environ['EXPECTED_SOURCE']" in commands[0]["run"]
    assert "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" in commands[0]["run"]
    assert [shlex.split(step["run"]) for step in commands[1:]] == [
        ["python", "tools/run-plugin-tests.py", "agent-worktrees",
         "--test-file", "tests/test_worktree_data_store.py",
         "-k", "native_windows_allocation or final_project_root_barrier_failure",
         "--allow-explicit-tiers"],
        ["python", "tools/run-plugin-tests.py", "efforts",
         "--test-file", "tests/test_agenda_export.py",
         "-k", "native_windows_complete_export", "--allow-explicit-tiers"],
    ]
    required = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    assert "worktree-state-windows" not in required["jobs"]


def test_pr_service_acceptance_is_bounded_and_independently_selected() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    selector = trigger["workflow_dispatch"]["inputs"]["pr_service"]
    assert selector["type"] == "boolean" and selector["default"] is False
    for name, runner in (
        ("pr-service-linux", "ubuntu-latest"),
        ("pr-service-windows", "windows-latest"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == "${{ inputs.pr_service }}"
        assert job["runs-on"] == runner and job["timeout-minutes"] == 10
        assert not job.get("strategy") and not job.get("continue-on-error")
        commands = [step for step in job["steps"] if "run" in step]
        assert len(commands) == 2 and not any(step.get("env") for step in commands)
        assert commands[0]["run"].split() == [
            "python", "tools/run-plugin-tests.py", "agent-pull-requests",
            "--test-file", "tests/test_watch_replay_store.py",
            "--test-file", "tests/test_watch_service.py",
            "--test-file", "tests/test_watch_gateway.py",
            "--test-file", "tests/test_watch_gateway_transport.py",
            "--test-file", "tests/test_watch_observer.py",
            "--test-file", "tests/test_watch_replay_client.py",
            "--test-file", "tests/test_watch_replay_binding.py",
            "--test-file", "tests/test_watch_replay_client_transport.py",
            "--max-files-per-sub-suite", "2",
        ]
        assert commands[1]["run"].split() == [
            "python", "tools/run-standalone-tests.py", "services/pr",
            "--prepare", "--timeout", "180", "--admission-wait", "30",
        ]


def test_compile_acceptance_is_bounded_native_and_independently_selected() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    selector = trigger["workflow_dispatch"]["inputs"]["dependency_compile"]
    assert selector["type"] == "boolean" and selector["default"] is False
    for name, runner in (
        ("dependency-compile-linux", "ubuntu-latest"),
        ("dependency-compile-windows", "windows-latest"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == "${{ inputs.dependency_compile }}"
        assert job["runs-on"] == runner
        assert job["timeout-minutes"] == 6 and not job.get("continue-on-error")
        assert not job.get("strategy")
        commands = [step["run"] for step in job["steps"] if "run" in step]
        assert commands == [
            "python tools/seasoned_uv.py --uv uv --registry-only -- --system pytest pyyaml",
            'python -m pytest -q tools/test_installer_dependency_compile.py '
            'tools/test_installer_dependency_policy.py '
            'tools/test_runtime_source_lock.py '
            'tools/test_runtime_source_authentication.py '
            'tools/test_runtime_source_feed.py '
            '-k "compile or real_uv_registry_transitive_and_isolated_build_age or runtime_source_lock '
            'or runtime_source_authentication or runtime_source_feed"',
        ]


def test_installer_latency_contracts_are_native_bounded_and_opt_in() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    for platform, runner in (("linux", "ubuntu-latest"), ("windows", "windows-latest")):
        job = workflow["jobs"][f"installer-latency-{platform}"]
        assert job["if"] == "${{ inputs.dependency_compile }}"
        assert job["runs-on"] == runner and job["timeout-minutes"] == 6
        assert not job.get("strategy") and not job.get("continue-on-error")
        commands = [step["run"] for step in job["steps"] if "run" in step]
        assert commands[:2] == [
            "python tools/seasoned_uv.py --uv uv --registry-only -- --system pytest pyyaml",
            "python -m pytest -q tools/test_installer_venv_seeding.py",
        ]
        assert commands[2] == (
            "python -m pytest -q "
            "tools/test_run_plugin_tests.py::test_short_temp_alias_reaches_real_daemon_registry"
        )
        daemon_step = next(step for step in job["steps"] if step.get("run") == commands[2])
        assert daemon_step["if"] == "runner.os == 'Windows'"
        assert shlex.split(commands[3]) == [
            "python", "tools/run-plugin-tests.py", "agent-codespaces", "--allow-explicit-tiers",
            "--test-file", "tests/test_installer_engine_adoption.py",
            "-k", "library_install_order_retry_and_errors or native_batch_installs_cold or signed_venv_recovery",
            "--test-timeout", "180", "--timeout", "300",
        ]


def test_wheel_acquisition_reuses_the_existing_bounded_opt_in_lane() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    selector = trigger["workflow_dispatch"]["inputs"]["wheel_inputs"]
    assert selector["type"] == "boolean" and selector["default"] is False
    for name, runner in (("wheel-inputs-linux", "ubuntu-latest"), ("wheel-inputs-windows", "windows-latest")):
        job = workflow["jobs"][name]
        assert job["if"] == "${{ inputs.wheel_inputs }}"
        assert job["runs-on"] == runner
        assert job["timeout-minutes"] == 6 and not job.get("continue-on-error")
        commands = [step["run"] for step in job["steps"] if "run" in step]
        assert len(commands) == 2 and "pytest packaging pyyaml" in commands[0]
        selection = commands[1].split()
        assert selection[:4] == ["python", "-m", "pytest", "-q"]
        assert "-k" not in selection
        for path in (
            "tools/test_installer_wheel_acquisition.py", "tools/test_runtime_feed_configuration.py",
            "tools/test_installer_closed_wheels.py", "tools/test_runtime_profile_release.py",
        ):
            assert path in selection


def test_watch_owner_acceptance_is_opt_in_native_and_separately_contained() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    expected_scenarios = {
        "normal-upgrade": (
            "test_normal_payload_init_refreshes_receipt or legacy_staging "
            "or bootstrap_admission or session_start_never_runs_legacy_reconciler"
        ),
        "two-shipped-cells": "test_two_normal_shipped_cells",
        "peer-roundtrip": "test_normal_payload_peer_roundtrip",
        "lease-only-endpoint-live": "native_account_lease_only_owner and endpoint_live",
        "lease-only-endpoint-missing": "native_account_lease_only_owner and endpoint_missing",
    }
    routes = (
        ("watch-owner-native-linux", "ubuntu-latest", True),
        ("watch-owner-native-windows", "windows-latest", True),
        ("watch-owner-boundaries-linux", "ubuntu-latest", False),
        ("watch-owner-boundaries-windows", "windows-latest", False),
    )
    for name, runner, native in routes:
        job = jobs[name]
        assert job["if"] == "${{ inputs.watch_owner }}"
        assert job["runs-on"] == runner
        assert job["timeout-minutes"] == 35
        assert job["strategy"]["fail-fast"] is False
        matrix = job["strategy"]["matrix"]
        assert set(matrix) == {"scenario"}
        commands = [step for step in job["steps"] if "run" in step]
        assert len(commands) == 1
        command = commands[0]
        assert "python tools/run-plugin-tests.py agent-pull-requests" in command["run"]
        assert "--allow-explicit-tiers" in command["run"]
        assert "--timeout 1200 --plugin-timeout 1800" in command["run"]
        assert '-k "${{ matrix.scenario.selector }}"' in command["run"]
        if native:
            assert command["env"] == {"APR_NATIVE_OS_ACCOUNT_ACCEPTANCE": "1"}
            assert "--allow-host-state" in command["run"]
            assert {
                case["name"]: case["selector"] for case in matrix["scenario"]
            } == expected_scenarios
        else:
            assert "--allow-host-state" not in command["run"]
            assert not command.get("env")
            assert {case["name"] for case in matrix["scenario"]} == {
                "controller-death", "concurrent-upgrade",
            }
            selectors = " ".join(case["selector"] for case in matrix["scenario"])
            for boundary in (
                "actual_controller_death_recovers", "bound_passive_endpoint_recovery",
                "completed_receipt_parallel_reads_keep_native_identity",
                "actual_installer_upgrade_drains_handlers_poll_ack",
            ):
                assert boundary in selectors
    macos = jobs["watch-owner-macos-refusal"]
    assert macos["if"] == "${{ inputs.watch_owner }}"
    assert macos["runs-on"] == "macos-latest"
    assert macos["timeout-minutes"] == 10
    commands = [step for step in macos["steps"] if "run" in step]
    assert len(commands) == 1
    assert "--allow-host-state" not in commands[0]["run"]
    assert "--allow-explicit-tiers --timeout 300 --plugin-timeout 600" in commands[0]["run"]
    assert "-k native_macos_governed_watch_refuses_unprovable_identity" in commands[0]["run"]


def test_dispatch_delegation_acceptance_is_bounded_and_independently_selected() -> None:
    workflow = yaml.safe_load(
        (HERE.parent / ".github/workflows/installation-context-acceptance.yml").read_text()
    )
    trigger = workflow.get("on", workflow.get(True))
    selector = trigger["workflow_dispatch"]["inputs"]["dispatch_delegation"]
    assert selector["type"] == "boolean" and selector["default"] is False
    for name, runner in (
        ("dispatch-delegation-linux", "ubuntu-latest"),
        ("dispatch-delegation-windows", "windows-latest"),
    ):
        job = workflow["jobs"][name]
        assert job["if"] == "${{ inputs.dispatch_delegation }}"
        assert job["runs-on"] == runner and job["timeout-minutes"] == 10
        assert not job.get("strategy") and not job.get("continue-on-error")
        commands = [step for step in job["steps"] if "run" in step]
        assert len(commands) == 1 and not commands[0].get("env")
        assert commands[0]["run"].split() == [
            "python", "tools/run-plugin-tests.py", "agent-dispatch",
            "--test-file", "tests/test_delegated_waiter_cli.py",
            "--test-file", "tests/test_delegated_waiter_context.py",
            "--test-file", "tests/test_delegated_waiter_fences.py",
            "--test-file", "tests/test_delegated_waiter_owner_contract.py",
            "--test-file", "tests/test_delegated_replay_consumer.py",
            "--test-file", "tests/test_delegated_replay_binding.py",
            "--test-file", "tests/test_delegated_replay_transport.py",
            "--test-file", "tests/test_delegated_replay_credentials.py",
            "--test-file", "tests/test_waiter_maintenance.py",
            "--max-files-per-sub-suite", "2",
        ]


def test_required_foundation_checks_generation_without_running_native_test_corpus() -> None:
    workflow = yaml.safe_load((HERE.parent / ".github/workflows/ci.yml").read_text())
    generation = next(
        step for step in workflow["jobs"]["checks"]["steps"]
        if step.get("name") == "Payload invocation canonical source"
    )
    assert generation["run"].split() == [
        "python", "libs/payload-invocation/generate.py", "--all", "--source-check",
    ]
    commands = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["checks"]["steps"]
    )
    assert "libs/payload-invocation/tests" not in commands
    assert "plugins/agent-index/tests/test_command_catalog.py" not in commands


@pytest.fixture(scope="module")
def native_partition_collections() -> dict[str, list[str]]:
    # Reuse only interpreter imports, not collection results or selector logic.
    script = """
import contextlib, io, json, sys, pytest
class Collected:
    def pytest_collection_finish(self, session):
        self.nodeids = [item.nodeid for item in session.items]
results = {}
for pointer in ("pointer_absent", "pointer_retained"):
    for mode in ("mode_legacy", "mode_active"):
        collected = Collected()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = pytest.main([
                "--collect-only", "-q", "--color=no", "-p", "no:cacheprovider",
                "libs/payload-invocation/tests/test_derived_diagnostic_cli.py",
                "-k", mode + " and " + pointer,
            ], plugins=[collected])
        if status:
            print(output.getvalue(), file=sys.stderr)
            raise SystemExit(status)
        results[pointer + ":" + mode] = collected.nodeids
print(json.dumps(results))
"""
    environment = {**os.environ, "PYTEST_ADDOPTS": "", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=HERE.parent, env=environment, capture_output=True, text=True, check=False,
        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("mode", ["mode_legacy", "mode_active"])
@pytest.mark.parametrize("pointer", ["pointer_absent", "pointer_retained"])
def test_full_native_matrix_collects_exact_inventory_partition(
    mode: str, pointer: str, native_partition_collections: dict[str, list[str]],
) -> None:
    root = HERE.parent
    spec = importlib.util.spec_from_file_location(
        "native_partition_inventory", HERE / "sync-installation-context.py",
    )
    assert spec and spec.loader
    inventory = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(inventory)
    script = "installation-context.ps1" if os.name == "nt" else "installation-context.sh"
    expected = {
        destination.relative_to(root).as_posix()
        for source, destination in inventory.vendor_pairs()
        if source.name == script
    }
    selected = [
        line for line in native_partition_collections[f"{pointer}:{mode}"]
        if "::test_derived_native_diagnostic_cli_read_only[" in line
    ]
    assert len(selected) == len(expected), selected
    assert {
        line.split(f"[{pointer}-{mode}-", 1)[1].removesuffix("]")
        for line in selected
    } == expected, selected
