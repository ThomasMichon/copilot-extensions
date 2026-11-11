"""Standalone qualification lanes and their separation from source CI."""

import json

import pytest

from _promotion_workflow_test_support import source_workflow
from test_run_standalone_tests import SCRIPT, runner
from test_run_standalone_tests import runner_scratch as runner_scratch


def test_fleet_client_portfolio_is_manual_exact_source_and_native_only():
    workflow = source_workflow(SCRIPT.parent.parent, "installation-context-full.yml")
    trigger = workflow.get("on", workflow.get(True))
    portfolio = trigger["workflow_dispatch"]["inputs"]["portfolio"]
    assert portfolio["default"] == "context" and "fleet-client-generations" in portfolio["options"]
    selected = {
        name for name, job in workflow["jobs"].items()
        if "inputs.portfolio == 'fleet-client-generations'" in job["if"]
    }
    assert selected == {"fleet-client-generations-linux", "fleet-client-generations-windows"}
    for suffix, host in (("linux", "ubuntu-latest"), ("windows", "windows-latest")):
        job = workflow["jobs"]["fleet-client-generations-" + suffix]
        assert job["if"] == (
            "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'fleet-client-generations'"
        )
        assert job["runs-on"] == host and not job.get("continue-on-error") and "strategy" not in job
        checkout = next(step for step in job["steps"] if step.get("uses") == "actions/checkout@v4")
        assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    assert workflow["permissions"] == {"contents": "read"}


def test_installed_artifact_inspection_consumes_large_resource_map_and_checks_last_member(tmp_path):
    import hashlib
    import os
    from pathlib import Path
    import sys
    from promotion_python_artifacts import PromotionArtifactError
    from smoke_public_toolchain import _run, inspect_installed_artifact

    environment = tmp_path / "consumer"
    _run([sys.executable, "-m", "venv", "--without-pip", str(environment)], cwd=tmp_path)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = Path(_run([
        str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))",
    ], cwd=tmp_path).strip())
    metadata = site / "inspection_fixture-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: inspection-fixture\nVersion: 1.0\n", encoding="utf-8",
    )
    files = site / "fixture"
    files.mkdir()
    content = b"synthetic packaged resource"
    digest = hashlib.sha256(content).hexdigest()
    resources = {}
    for index in range(1000):
        name = f"fixture/member-{index:04}.txt"
        (site / name).write_bytes(content)
        resources[name] = digest
    assert len(json.dumps(resources)) > 32767
    arguments = {
        "versions": {"inspection-fixture": "1.0"}, "resources": resources,
        "primary": "inspection-fixture", "component": "fixture",
    }
    inspect_installed_artifact(python, environment, **arguments)
    (site / name).write_bytes(b"changed last resource")
    with pytest.raises(PromotionArtifactError, match="AssertionError"):
        inspect_installed_artifact(python, environment, **arguments)


@pytest.mark.parametrize("fault", [None, "source", "consumer", "skipped"])
def test_fleet_client_requests_preserve_bounds_and_require_complete_native_reports(
    tmp_path, monkeypatch, capsys, fault,
):
    import os
    import runpy
    import shlex
    import subprocess
    import sys
    from types import SimpleNamespace
    import xml.etree.ElementTree as ET

    from _pytest.mark.expression import Expression

    workflow = source_workflow(SCRIPT.parent.parent, "installation-context-full.yml")
    step = next(step for step in workflow["jobs"]["fleet-client-generations-linux"]["steps"] if "run" in step)
    assert step["shell"] == "python"
    tests = tmp_path / "fleet-manager/tests"
    tests.mkdir(parents=True)
    for name in ("test_artifact_staging.py", "test_client_generation_cli.py", "test_unrelated.py"):
        (tests / name).touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--unsafe-inherited-selection")
    original = dict(os.environ)
    cases = [
        "test_fresh_installed_console_admission_and_durable_reopen_without_hosting",
        "test_missing_packaged_resource_is_not_replaced_by_repository_helper",
        *[
            f"test_actual_installed_console_generation_transitions[{transition}]"
            for transition in ("construct", "register-commands", "unregister-commands", "remove-runtime")
        ],
    ]
    calls = []

    def run(command, *, cwd, timeout, max_stdout_bytes, max_stderr_bytes, env=None):
        assert cwd == tmp_path and max_stdout_bytes == max_stderr_bytes == 16 * 1024 * 1024
        calls.append(command)
        output = ""
        if command[0] == "git":
            assert timeout == 30
            output = ("b" if fault == "source" else "a") * 40
        elif command[1] == "tools/run-standalone-tests.py":
            assert command[:6] == [
                sys.executable, "tools/run-standalone-tests.py", "fleet-manager", "--timeout", "120",
                *(["--prepare"] if "--prepare" in command else []),
            ]
            arguments = shlex.split(env["PYTEST_ADDOPTS"])
            assert "--unsafe-inherited-selection" not in arguments
            assert arguments[0] == "--ignore=" + str(tests / "test_unrelated.py")
            if "--prepare" in command:
                assert timeout == 300 and arguments[1:] == ["--collect-only"]
            else:
                assert timeout == 180
                if fault == "consumer":
                    raise subprocess.CalledProcessError(1, command)
                expression = Expression.compile(arguments[arguments.index("-k") + 1])
                suite = ET.Element("testsuite")
                for name in cases:
                    if expression.evaluate(lambda value: value.lower() in name.lower()):
                        case = ET.SubElement(suite, "testcase", name=name)
                        if fault == "skipped" and "remove-runtime" in name:
                            ET.SubElement(case, "skipped")
                root = ET.Element("testsuites")
                root.append(suite)
                ET.ElementTree(root).write(arguments[arguments.index("--junitxml") + 1])
        else:
            assert command[1:3] == [
                "-I", "tools/clean-room/scenarios/fleet-manager-client-admission/verify.py",
            ] and timeout == 30
            verifier = SCRIPT.parent / "clean-room/scenarios/fleet-manager-client-admission/verify.py"
            with monkeypatch.context() as context:
                context.setattr(sys, "argv", [str(verifier), command[-1]])
                runpy.run_path(str(verifier), run_name="__main__")
        return SimpleNamespace(stdout=output, stderr="", check_returncode=lambda: None)

    monkeypatch.setitem(sys.modules, "agent_procutil.owned_process", SimpleNamespace(run_caller_owned=run))
    body = compile(step["run"], "<fleet-client-workflow>", "exec")
    if fault:
        expected = subprocess.CalledProcessError if fault == "consumer" else AssertionError
        with pytest.raises(expected):
            exec(body, {})
        assert "INSTALLED_CLIENT_ADMISSION_AND_EXACT_GENERATION_LIFECYCLE_PASS" not in capsys.readouterr().out
    else:
        exec(body, {})
        assert len(calls) == 8
        assert "INSTALLED_CLIENT_ADMISSION_AND_EXACT_GENERATION_LIFECYCLE_PASS" in capsys.readouterr().out
        report = tmp_path / ".test-venvs/fleet-client-consumers/consumer.xml"
        assert len(list(ET.parse(report).getroot().iter("testcase"))) == 6
    assert dict(os.environ) == original


def test_bridge_contracts_portfolio_keeps_existing_budgets_and_explicit_scope():
    workflow = source_workflow(SCRIPT.parent.parent, "installation-context-full.yml")
    triggers = workflow["on"] if "on" in workflow else workflow[True]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert "bridge-contracts" in inputs["portfolio"]["options"]
    assert inputs["portfolio"]["default"] == "context"
    job = workflow["jobs"]["bridge-contracts-linux"]
    assert job["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'bridge-contracts'"
    )
    assert job["runs-on"] == "ubuntu-latest"
    commands = [step["run"] for step in job["steps"] if "run" in step]
    assert commands[0] == "python tools/check-agent-bridge-contracts.py"
    assert commands[1].split()[:3] == ["python", "tools/run-plugin-tests.py", "agent-bridge"]
    assert "--timeout" not in commands[1] and "--test-timeout" not in commands[1]
    assert commands[1].count("--test-file") == 3
    assert "shared_capture_seed_has_owned_committed_inputs" in commands[1]
    assert "capture_reproducible_real_commit_preserves_history" in commands[1]


@pytest.mark.parametrize("fault", [None, "consumer", "missing", "duplicate", "skipped"])
def test_managed_fleet_packet_requires_all_exact_consumers_without_wider_budgets(
    tmp_path, monkeypatch, capsys, fault,
):
    import os
    import runpy
    import shlex
    import subprocess
    import sys
    from types import SimpleNamespace
    import xml.etree.ElementTree as ET

    workflow = source_workflow(SCRIPT.parent.parent, "installation-context-full.yml")
    step = next(
        step for step in workflow["jobs"]["fleet-client-generations-linux"]["steps"]
        if step.get("name") == "Qualify explicit managed client installation and exact removal"
    )
    tests = tmp_path / "fleet-manager/tests"
    tests.mkdir(parents=True)
    for name in ("test_client_generation_cli.py", "test_unrelated.py"):
        (tests / name).touch()
    (tmp_path / ".test-venvs/fleet-client-consumers").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setenv("PYTEST_ADDOPTS", "--unsafe-inherited-selection")
    original = dict(os.environ)
    observed = []

    def run(command, *, cwd, timeout, max_stdout_bytes, max_stderr_bytes, env=None):
        assert cwd == tmp_path and max_stdout_bytes == max_stderr_bytes == 16 * 1024 * 1024
        if command[1] == "tools/run-standalone-tests.py":
            assert command == [
                sys.executable, "tools/run-standalone-tests.py", "fleet-manager", "--timeout", "120",
            ] and timeout == 180
            arguments = shlex.split(env["PYTEST_ADDOPTS"])
            assert arguments[0] == "--ignore=" + str(tests / "test_unrelated.py")
            assert "--unsafe-inherited-selection" not in arguments
            expression = arguments[arguments.index("-k") + 1]
            assert expression.startswith("actual_installed_console_managed_lifecycle and ")
            transition = expression.split()[-1]
            observed.append(transition)
            if fault == "consumer":
                raise subprocess.CalledProcessError(1, command)
            root = ET.Element("testsuites")
            suite = ET.SubElement(root, "testsuite")
            if fault != "missing" or transition != "higher":
                case = ET.SubElement(
                    suite, "testcase", name=f"test_actual_installed_console_managed_lifecycle[{transition}]",
                )
                if fault == "skipped" and transition == "higher":
                    ET.SubElement(case, "skipped")
                if fault == "duplicate" and transition == "higher":
                    ET.SubElement(suite, "testcase", name=case.attrib["name"])
            ET.ElementTree(root).write(arguments[arguments.index("--junitxml") + 1])
        else:
            assert command[1:3] == [
                "-I", "tools/clean-room/scenarios/fleet-manager-client-admission/verify.py",
            ] and command[-1] == "--managed" and timeout == 30
            verifier = SCRIPT.parent / "clean-room/scenarios/fleet-manager-client-admission/verify.py"
            with monkeypatch.context() as context:
                context.setattr(sys, "argv", [str(verifier), *command[-2:]])
                runpy.run_path(str(verifier), run_name="__main__")
        return SimpleNamespace(stdout="", stderr="", check_returncode=lambda: None)

    monkeypatch.setitem(sys.modules, "agent_procutil.owned_process", SimpleNamespace(run_caller_owned=run))
    body = compile(step["run"], "<managed-fleet-workflow>", "exec")
    if fault:
        expected = subprocess.CalledProcessError if fault == "consumer" else AssertionError
        with pytest.raises(expected):
            exec(body, {})
        assert "INSTALLED_MANAGED_CLIENT_LIFECYCLE_PASS" not in capsys.readouterr().out
    else:
        exec(body, {})
        assert observed == ["retry", "forward-missing", "forward-malformed", "higher", "uninstall", "foreign", "legacy", "installed-owner"]
        assert "INSTALLED_MANAGED_CLIENT_LIFECYCLE_PASS" in capsys.readouterr().out
    assert dict(os.environ) == original


@pytest.mark.parametrize("portfolio,version,digest", [
    ("pane-argv", "3.3.6", "a56a890ea0829567818b9a368f16dcbd39c087f27328573df17c10dd39618947"),
    ("mux-companion", "3.4.0", "a66b6be7c13c6fa0dc3660a267b9e34eaa91a4eb7f07fbd55a0468018bd0f721"),
])
def test_pane_argv_portfolio_is_manual_pinned_and_keeps_work_scoped(portfolio, version, digest):
    workflow = source_workflow(SCRIPT.parent.parent, "installation-context-full.yml")
    triggers = workflow["on"] if "on" in workflow else workflow[True]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert "pane-argv" in inputs["portfolio"]["options"]
    assert inputs["portfolio"]["default"] == "context"
    job = workflow["jobs"]["pane-argv-windows"]
    assert job["if"] == (
        "github.event_name == 'workflow_dispatch' && "
        "(inputs.portfolio == 'pane-argv' || inputs.portfolio == 'mux-companion')"
    )
    assert job["runs-on"] == "windows-latest"
    assert job["timeout-minutes"] == 10 and not job.get("strategy")
    prepare, qualify = [
        step for step in job["steps"]
        if "run" in step and step.get("if") != "inputs.portfolio == 'mux-companion'"
    ]
    assert prepare["env"] == {"PORTFOLIO": "${{ inputs.portfolio }}"}
    default, companion = prepare["run"].split("if ($env:PORTFOLIO -eq 'mux-companion') {")
    selection = default if portfolio == "pane-argv" else companion.split("}", 1)[0]
    assert f"$version = '{version}'" in selection
    assert f"$digest = '{digest}'" in selection
    assert (
        '"https://github.com/psmux/psmux/releases/download/v$version/'
        'psmux-v$version-windows-x64.zip"'
    ) in prepare["run"]
    assert "Get-FileHash" in prepare["run"] and "SHA256" in prepare["run"]
    assert ").Hash -ne $digest)" in prepare["run"]
    assert qualify["env"]["PSMUX_FILE_LAUNCH_E2E"] == "1"
    assert qualify["env"]["EXPECTED_SOURCE"] == "${{ github.sha }}"
    assert "actual == os.environ['EXPECTED_SOURCE']" in qualify["run"]
    assert qualify["run"].splitlines()[-1].split() == [
        "python", "tools/run-plugin-tests.py", "worktree-manager",
        "--test-file", "tests/test_pane_args_file.py",
        "-k", "real_psmux", "--allow-explicit-tiers",
    ]


def test_manager_uninstall_portfolio_is_manual_and_keeps_native_owner():
    workflow = source_workflow(SCRIPT.parent.parent, "installation-context-full.yml")
    triggers = workflow["on"] if "on" in workflow else workflow[True]
    inputs = triggers["workflow_dispatch"]["inputs"]
    assert "manager-uninstall" in inputs["portfolio"]["options"]
    assert inputs["portfolio"]["default"] == "context"
    job = workflow["jobs"]["manager-uninstall-windows"]
    assert job["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.portfolio == 'manager-uninstall'"
    )
    assert job["runs-on"] == "windows-latest" and job["timeout-minutes"] == 15
    assert not job.get("strategy")
    commands = [step for step in job["steps"] if "run" in step]
    assert commands[0]["env"]["EXPECTED_SOURCE"] == "${{ github.sha }}"
    assert "actual == os.environ['EXPECTED_SOURCE']" in commands[0]["run"]
    assert commands[1]["run"].split() == [
        "python", r"tools\run-manager-runtime-acceptance.py",
        "--root", ".validation", "--contract", "uninstall",
    ]


def test_chat_preparation_retains_both_canonical_build_resource_owners(tmp_path):
    assert runner.standalone_build_resources("services/chat", source_root=tmp_path) == (
        tmp_path / "libs" / "versioned-runtime",
        tmp_path / "libs" / "installer-engine",
    )


@pytest.mark.parametrize("suffix,host", [("linux", "ubuntu-latest"), ("windows", "windows-latest")])
def test_dispatch_fingerprint_lane_is_unfiltered_and_outside_actor_free_inventory(suffix, host):
    root = SCRIPT.parent.parent
    workflow = source_workflow(root, "installation-context-acceptance.yml")
    job = workflow["jobs"]["uv-deployment-compatibility-" + suffix]
    assert job["if"] == "${{ inputs.uv_deployment_compatibility }}"
    assert job["runs-on"] == host and job["timeout-minutes"] == 30
    target = "tests/test_installer_input_fingerprint.py"
    steps = [step for step in job["steps"] if target in step.get("run", "")]
    assert len(steps) == 1
    step = steps[0]
    assert step["run"].split() == [
        "python", "tools/run-plugin-tests.py", "agent-dispatch", "--allow-explicit-tiers",
        "--test-file", target,
    ]
    assert "if" not in step and not step.get("continue-on-error")
    for settings in (workflow, job, step):
        assert "PYTEST_ADDOPTS" not in settings.get("env", {})
    manifest = json.loads((root / "tools/promotion-unit-manifest.json").read_text(encoding="utf-8"))
    source = "plugins/agent-dispatch/" + target
    assert source not in manifest["sources"]
    assert all(node.split("::", 1)[0] != source for node in manifest["nodes"])


def test_chat_native_lanes_are_explicit_complete_and_bound_to_the_selected_python():
    workflow = source_workflow(SCRIPT.parent.parent, "installation-context-acceptance.yml")
    triggers = workflow["on"] if "on" in workflow else workflow[True]
    assert set(triggers) == {"workflow_dispatch"}
    selected = triggers["workflow_dispatch"]["inputs"]["chat_construction"]
    assert selected["type"] == "boolean" and selected["default"] is False
    jobs = workflow["jobs"]
    for suffix, host in (("linux", "ubuntu-latest"), ("windows", "windows-latest")):
        job = jobs["chat-construction-" + suffix]
        assert job["if"] == "${{ inputs.chat_construction }}"
        assert job["runs-on"] == host
        assert job["strategy"] == {
            "fail-fast": False, "matrix": {"python": ["3.10", "3.11"]},
        }
        setup = next(step for step in job["steps"] if step.get("id") == "chat-python")
        assert setup["with"]["python-version"] == "${{ matrix.python }}"
        acceptance, full = [step for step in job["steps"] if "run" in step]
        assert acceptance["env"]["UV_PYTHON"] == "${{ steps.chat-python.outputs.python-path }}"
        assert acceptance["run"].split() == [
            "python", "tools/run-standalone-tests.py", "services/chat", "--prepare",
            "--chat-suite", "acceptance", "--timeout", "180",
        ]
        assert full["run"].split() == [
            "python", "tools/run-standalone-tests.py", "services/chat",
            "--chat-suite", "full", "--timeout", "600",
        ]
    for name in ("derived-diagnostics-posix", "derived-diagnostics-windows"):
        assert "!inputs.chat_construction" in jobs[name]["if"]
        assert "!inputs.wheel_inputs" in jobs[name]["if"]
        assert "!inputs.runtime_profiles" in jobs[name]["if"]


@pytest.mark.parametrize("profile,timeout,explicit", [
    ("acceptance", 180, False), ("full", 600, False),
    ("acceptance", 45, True), ("full", 45, True),
])
@pytest.mark.parametrize("outcome", [0, 1])
def test_chat_qualification_lane_defaults_preserve_full_collection(
    tmp_path, monkeypatch, profile, timeout, explicit, outcome,
):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    root = tmp_path / "services" / "chat"
    (root / "tests").mkdir(parents=True)
    python = runner.default_python("services/chat")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(runner, "acquire", lambda *_: type("Lease", (), {"release": lambda _: None})())

    def run(command, **kwargs):
        selected = command[command.index("-q") + 1:command.index("--basetemp")]
        assert selected == [str(root / "tests")]
        assert kwargs["env"]["AGENT_CHAT_TEST_SUITE"] == profile
        assert kwargs["limits"].wall_seconds == timeout
        report = kwargs["sandbox"] / "pytest" / "chat-membership.json"
        report.parent.mkdir()
        report.write_text(
            '{"nodes":{"fixture":"acceptance"},"execution":{"status":"complete"}}',
            encoding="utf-8",
        )
        return outcome

    monkeypatch.setattr(runner, "run_contained", run)
    report = tmp_path / "report.json"
    args = ["services/chat", "--chat-suite", profile, "--chat-membership-report", str(report)]
    if explicit:
        args.extend(("--timeout", str(timeout)))
    assert runner.main(args) == outcome
    assert json.loads(report.read_text("utf-8")) == {
        "nodes": {"fixture": "acceptance"}, "membership_status": "available",
        "execution": {"status": "complete"},
        "test_exit_code": outcome, "exit_code": outcome,
    }
    with pytest.raises(SystemExit) as error:
        runner.main(args)
    assert error.value.code == 2
    assert json.loads(report.read_text("utf-8"))["exit_code"] == outcome


@pytest.mark.parametrize("outcome", [0, 4, 124])
@pytest.mark.parametrize("map_state", [
    "missing", "collected", "collection-only", "partial", "wrong-type", "execution-wrong-type",
])
@pytest.mark.parametrize("retain", [False, True])
def test_membership_validation_preserves_original_failure_without_terminal_summary(
    tmp_path, monkeypatch, capsys, outcome, map_state, retain,
):
    monkeypatch.setattr(runner, "REPO", tmp_path)
    (tmp_path / "services" / "chat" / "tests").mkdir(parents=True)
    python = runner.default_python("services/chat")
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(runner, "acquire", lambda *_: type("Lease", (), {"release": lambda _: None})())
    owned_sandboxes = []

    def run(command, **kwargs):
        sandbox = kwargs["sandbox"]
        owned_sandboxes.append(sandbox)
        if map_state != "missing":
            report = sandbox / "pytest" / "chat-membership.json"
            report.parent.mkdir()
            report.write_text({
                "collected": '{"nodes":{"fixture":"acceptance"},"execution":{"status":"complete"}}',
                "collection-only": '{"nodes":{"fixture":"acceptance"},"execution":{"status":"pending"}}',
                "execution-wrong-type": '{"nodes":{"fixture":"acceptance"},"execution":[]}',
                "partial": '{"nodes":',
                "wrong-type": '[]',
            }[map_state], encoding="utf-8")
        return outcome

    monkeypatch.setattr(runner, "run_contained", run)
    destination = tmp_path / "retained.json"
    expected = outcome if map_state == "collected" else outcome or 1
    args = ["services/chat"]
    if retain:
        args.extend(("--chat-membership-report", str(destination)))
    assert runner.main(args) == expected
    if retain:
        report = json.loads(destination.read_text("utf-8"))
        assert report["test_exit_code"] == outcome and report["exit_code"] == expected
        available = map_state in {"collected", "collection-only", "execution-wrong-type"}
        assert report["membership_status"] == ("available" if available else "unavailable")
        if not available:
            assert "nodes" not in report
    else:
        assert not destination.exists()
    if map_state in {"missing", "partial", "wrong-type"}:
        assert "membership unavailable" in capsys.readouterr().err
    elif outcome == 0 and map_state != "collected":
        assert "execution is incomplete" in capsys.readouterr().err
    assert all(not sandbox.exists() for sandbox in owned_sandboxes)


def test_membership_export_destination_failure_preserves_original_nonzero_result(tmp_path, capsys):
    sandbox = tmp_path / "sandbox"
    (sandbox / "pytest").mkdir(parents=True)
    (sandbox / "pytest" / "chat-membership.json").write_text('{"nodes":{}}', encoding="utf-8")
    destination = tmp_path / "occupied.json"
    destination.write_text("retain", encoding="utf-8")
    assert runner.export_chat_membership(sandbox, destination, "full", 124) == 124
    assert destination.read_text("utf-8") == "retain"
    assert "membership export failed" in capsys.readouterr().err


def test_chat_lane_refuses_other_consumers_before_admission(monkeypatch):
    monkeypatch.setattr(runner, "acquire", lambda *_: pytest.fail("unexpected admission"))
    with pytest.raises(SystemExit) as error:
        runner.main(["fleet-manager", "--chat-suite", "full"])
    assert error.value.code == 2


def test_ci_path_gates_smoke_and_promotion_keeps_exhaustive():
    root = SCRIPT.parent.parent
    ci = source_workflow(root, "ci.yml")["jobs"]
    promotion = source_workflow(root, "validate-and-promote.yml")["jobs"]
    if "unit-tests" in ci:
        assert set(ci) == {"checks", "unit-tests", "pr-gate"}
        assert set(ci["pr-gate"]["needs"]) == {"checks", "unit-tests"}
        for jobs, owner, producer in (
            (ci, "unit-tests", "tools/ci_pr_unit_reuse.py"),
            (promotion, "source-units", "tools/run-promotion-unit-tests.py"),
        ):
            commands = [
                step["run"] for job in jobs.values() for step in job.get("steps", [])
                if "run" in step
            ]
            assert not any("run-standalone-tests.py" in command for command in commands)
            assert not any("run-plugin-tests.py" in command for command in commands)
            packets = [
                step["run"] for step in jobs[owner]["steps"]
                if producer in step.get("run", "")
            ]
            assert len(packets) == 1
            assert "--source-sha" in packets[0] and "--output-dir" in packets[0]
        return
    _assert_ci_path_gates_smoke_and_promotion_keeps_exhaustive(ci, promotion)


def test_ssh_manual_lane_selects_only_bounded_disposable_native_acceptance():
    workflow = source_workflow(SCRIPT.parent.parent, "installation-context-acceptance.yml")
    events = workflow.get("on", workflow.get(True))
    assert events["workflow_dispatch"]["inputs"]["ssh_service"]["default"] is False
    for suffix in ("linux", "windows"):
        job = workflow["jobs"]["ssh-service-" + suffix]
        assert job["if"] == "${{ inputs.ssh_service }}"
        commands = [step for step in job["steps"] if "run-standalone-tests.py" in step.get("run", "")]
        assert len(commands) == 1
        assert "services/ssh" in commands[0]["run"] and "--timeout 180" in commands[0]["run"]
        if suffix == "linux":
            assert commands[0]["env"] == {"SSH_SERVICE_NATIVE_ACCEPTANCE": "1"}
            native = [step for step in job["steps"]
                      if "run-plugin-tests.py agent-ssh" in step.get("run", "")]
            assert len(native) == 1
            assert native[0]["env"] == {"AGENT_SSH_NATIVE_DISPOSABLE": "1"}
            assert "--allow-host-state --allow-explicit-tiers" in native[0]["run"]
            assert "--test-file tests/test_gateway_authorization_native.py" in native[0]["run"]
            assert "--test-file tests/test_gateway_sshd_native.py" in native[0]["run"]
        else:
            assert not commands[0].get("env")
    for suffix in ("posix", "windows"):
        assert "!inputs.ssh_service" in workflow["jobs"]["derived-diagnostics-" + suffix]["if"]


def _assert_ci_path_gates_smoke_and_promotion_keeps_exhaustive(ci, promotion):
    import shlex
    from packaging.requirements import Requirement

    discovery = next(step["run"] for step in ci["discover"]["steps"] if step.get("id") == "set")
    detector = next(
        step["run"] for step in ci["checks"]["steps"] if step.get("id") == "version-bump-engine"
    )
    assert r"standalone_consumers\.py" in detector
    for path in ("services/index/", "plugins/agent-index/", "libs/", "tools/"):
        assert path in discovery
    for name in ("test-runner-linux", "test-runner-windows"):
        command = next(
            step["run"] for step in ci[name]["steps"]
            if step.get("name") == "Test runner admission and isolation"
        )
        install = shlex.split(command.split("&&", 1)[0])
        assert "tools/seasoned_uv.py" in install and "--system" in install
        requirements = {
            requirement.name: requirement
            for requirement in map(Requirement, install[install.index("--system") + 1:])
        }
        assert {"pytest", "pytest-timeout", "pyyaml", "setuptools", "wheel"} <= requirements.keys()
        assert "83.0.0" in requirements["setuptools"].specifier
        assert "82.9.0" not in requirements["setuptools"].specifier
    for name in ("agent-index-service-linux", "agent-index-service-windows"):
        assert ci[name]["needs"] == "discover"
        assert "needs.discover.outputs.standalone_service == 'true'" in ci[name]["if"]
        command = next(step["run"] for step in ci[name]["steps"] if "run" in step)
        assert 'MODE="--smoke"' in command
        assert "workflow_dispatch" in command
        full = next(
            step["run"] for step in promotion[name]["steps"]
            if "run-standalone-tests" in step.get("run", "")
        )
        assert "--smoke" not in full
