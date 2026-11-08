"""Focused controls for typed selection, owner admission and request resumption."""

from __future__ import annotations

import copy
import base64
from contextlib import nullcontext
import importlib.util
import json
import os
import subprocess
import sys
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


@pytest.mark.parametrize("unsupported", ["version", "selector", "workflow", "unprotected"])
def test_actual_provider_support_refuses_before_staging_or_creating_request_state(
    tmp_path, monkeypatch, unsupported,
):
    client = object.__new__(client_module.Client)
    client.root, client.state_root, client.repository = tmp_path, tmp_path, "owner/repository"
    effects = []
    client.transport = SimpleNamespace(
        git=lambda *args: SimpleNamespace(
            stdout=(
                "b" * 40 if args[0] == "rev-parse"
                else "100644 blob " + "c" * 40 + "\tplugins/sample/tests/test_sample.py"
            ),
        ),
        scoped_credentials=lambda owner: effects.append("credential-selection"),
        append=lambda *args: effects.append("push"),
    )
    support = {
        "request_versions": [] if unsupported == "version" else [1],
        "result_versions": [1], "workflow": contract.WORKFLOW,
        "transport_ref": contract.TRANSPORT_REF, "transport_format": contract.TRANSPORT_MARKER,
        "selection_kinds": [] if unsupported == "selector" else ["plugin"],
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
    with pytest.raises(ValueError):
        client.submit(focused(), "HEAD")
    assert effects == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("visible", [True, False])
def test_interrupted_transport_resumes_only_original_confirmed_envelope(tmp_path, monkeypatch, visible):
    client, directory, state = fake_client(tmp_path, monkeypatch)
    state.update(phase="transport-unknown", envelope_sha="e" * 40)
    client.save(directory, state)
    monkeypatch.setattr(client, "preflight", lambda selected: state["provider"])
    client.transport = SimpleNamespace(
        scoped_credentials=lambda owner: None,
        tip=lambda: "e" * 40 if visible else None,
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
    monkeypatch.delitem(sys.modules, "plugin_test_environment")
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
    for job in value["jobs"].values():
        assert job["permissions"] == {"contents": "read"}
        assert "matrix" not in job.get("strategy", {})
        for step in job["steps"]:
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False
            assert "secrets." not in json.dumps(step)
