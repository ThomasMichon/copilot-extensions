"""Controls for the protected two-application packet, not native acceptance."""

from __future__ import annotations

import json
import platform
from pathlib import Path
import runpy
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import windows_qualification_contract as contract
import windows_qualification_manager_source as adapter
import windows_qualification_worker as worker


def selected() -> dict[str, Any]:
    return {
        "kind": "packet", "name": contract.SOURCE_PACKET,
        "main_sha": "c" * 40, "dev_sha": "d" * 40,
    }


def evidence() -> dict[str, Any]:
    return {
        "runner_sha": "b" * 40, "main_sha": "c" * 40, "dev_sha": "d" * 40,
        "stages": dict.fromkeys(("prepare", "exercise", "assertions", "settle"), 0),
        "errors": [], "observer": {
            "schema": "worktree-manager.native-process-trace.v1",
            "stop_observed": True, "scan_errors": 0, "truncated": False, "window_armed": True,
        },
    }


@pytest.mark.parametrize("change", [
    {"main_sha": None}, {"dev_sha": "dev"}, {"dev_sha": "c" * 40},
    {"command": "ignored"}, {"name": "native-worktrees-manager-no-mux"},
    {"kind": "tools"},
])
def test_frozen_inputs_are_required_only_for_the_named_packet(change: dict[str, Any]) -> None:
    value = selected()
    value.update(change)
    with pytest.raises(ValueError):
        contract.selection(value)


@pytest.mark.parametrize("change", [
    {"stages": {"prepare": 0, "exercise": 0, "assertions": 0}},
    {"stages": {"prepare": 0, "exercise": 0, "assertions": 0, "settle": 1}},
    {"main_sha": "f" * 40}, {"dev_sha": "e" * 40},
    {"observer": {}}, {"errors": ["settlement unknown"]},
])
def test_actual_stage_input_and_observer_failure_cannot_qualify(change: dict[str, Any]) -> None:
    value = evidence()
    value.update(change)
    with pytest.raises(ValueError, match="incomplete, foreign or unsettled"):
        adapter.validate_evidence(value, selected())


@pytest.mark.parametrize("change", [
    {}, {"manager_source": {**evidence(), "runner_sha": "f" * 40}},
    {"tests": []},
    {"tests": [{"class": contract.SOURCE_MODULE, "name": contract.SOURCE_CASE, "outcome": "skipped"}]},
    {"tests": [{"class": "other", "name": contract.SOURCE_CASE, "outcome": "passed"}]},
])
def test_source_result_requires_exact_runner_and_one_real_assertion(change: dict[str, Any]) -> None:
    request = {
        "version": 1, "request_id": "a" * 32, "source_sha": "b" * 40,
        "selection": selected(), "envelope_sha": None,
    }
    value = {
        "version": 1, "request": request, "run_id": 1, "run_attempt": 1,
        "control_sha": "e" * 40, "actual_source_sha": "b" * 40,
        "platform": {"system": "Windows"}, "outcome": "qualified",
        "manager_source": evidence(),
        "tests": [{"class": contract.SOURCE_MODULE, "name": contract.SOURCE_CASE, "outcome": "passed"}],
        "phases": [{"name": name, "status": "passed"} for name in (
            "source-and-selection", "governed-feed", contract.SOURCE_PACKET, "test-runtime",
        )],
    }
    value.update(change)
    if change:
        with pytest.raises(ValueError):
            contract.validate_result(value, request, run_id=1, control_sha="e" * 40, run_attempt=1)
    else:
        contract.validate_result(value, request, run_id=1, control_sha="e" * 40, run_attempt=1)


@pytest.mark.parametrize("code", [0, 1, "timeout"])
def test_adapter_uses_existing_runner_and_exports_failure_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int | str,
) -> None:
    root, output = tmp_path / "candidate", tmp_path / "evidence"
    root.mkdir()
    output.mkdir()
    monkeypatch.setattr(adapter, "os", SimpleNamespace(name="nt", environ={
        "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_RUN_ID": "1", "RUNNER_TEMP": str(tmp_path),
    }))

    def run(argv: list[str], **options: Any) -> SimpleNamespace:
        assert argv[1] == str(root / "tools/run-manager-suite-acceptance.py")
        assert argv[argv.index("--mode") + 1] == "source"
        assert argv[argv.index("--main-commit") + 1] == "c" * 40
        assert argv[argv.index("--dev-commit") + 1] == "d" * 40
        assert options["timeout"] == 3500
        workspace = Path(argv[argv.index("--root") + 1])
        (workspace / "source").mkdir(parents=True)
        (workspace / "source-runner.json").write_text(
            json.dumps({"stages": evidence()["stages"], "errors": []}), encoding="utf-8",
        )
        (workspace / "source-process-trace.json").write_text(
            json.dumps(evidence()["observer"]), encoding="utf-8",
        )
        (workspace / "source/setup.json").write_text(json.dumps({
            "runner_source": "b" * 40,
            "frozen": {"main": {"commit": "c" * 40}, "dev": {"commit": "d" * 40}},
        }), encoding="utf-8")
        (workspace / "source/commands.jsonl").write_text('{"url":"https://example.invalid/"}\n')
        (workspace / "private-home").mkdir()
        (workspace / "private-home/never-export.json").write_text("{}")
        if code == "timeout":
            raise subprocess.TimeoutExpired(argv, 3500, output="https://example.invalid/")
        return SimpleNamespace(returncode=code, stdout="https://example.invalid/", stderr="")

    if code:
        with pytest.raises((ValueError, subprocess.TimeoutExpired)):
            adapter.qualify(root, output, selected(), run=run, redact=worker.redact_feed)
    else:
        assert adapter.qualify(root, output, selected(), run=run, redact=worker.redact_feed) == evidence()
    worker.sanitize_native_files(output)
    assert (output / "packet/source/commands.jsonl").is_file()
    assert not list(output.rglob("never-export.json"))
    assert all("https://" not in path.read_text("utf-8") for path in output.rglob("*") if path.is_file())


def test_export_rejects_redirected_or_oversized_evidence(tmp_path: Path) -> None:
    (tmp_path / "source").mkdir()
    target = tmp_path / "source/commands.jsonl"
    with target.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="size bound"):
        adapter.export_evidence(tmp_path, tmp_path / "out")


def test_sanitization_preserves_nested_command_json_and_omits_malformed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = "https://packages.example.invalid/simple"
    monkeypatch.setenv("UV_DEFAULT_INDEX", index)
    record = {
        "argv": ["--source-descriptor", json.dumps({
            "kind": "git", "canonical": "git:https://repository.example.invalid/runtime", "ref": "",
        })],
        "stdout": {"index": index, "diagnostic": "packages.example.invalid is unavailable"},
    }
    for suffix in ("json", "jsonl"):
        (tmp_path / ("commands." + suffix)).write_text(json.dumps(record) + "\n", encoding="utf-8")
    malformed = tmp_path / "partial.jsonl"
    malformed.write_text(json.dumps(record) + "\n" + '{"partial": "https://packages.example.invalid', encoding="utf-8")
    assert worker.sanitize_native_files(tmp_path) is True
    assert not malformed.exists()
    for suffix in ("json", "jsonl"):
        raw = (tmp_path / ("commands." + suffix)).read_text("utf-8")
        actual = json.loads(raw)
        descriptor = json.loads(actual["argv"][1])
        assert descriptor["canonical"] == "git:[redacted-url]"
        assert actual["stdout"]["index"] == "[redacted-url]"
        assert actual["stdout"]["diagnostic"] == "[configured-feed] is unavailable"
        assert "https://" not in raw and "example.invalid" not in raw
    assert worker.sanitize_native_files(tmp_path) is False


def test_source_worker_does_not_acquire_unrelated_native_binaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, output = tmp_path / "candidate", tmp_path / "evidence"
    for file in contract.SOURCE_FILES:
        path = root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    calls: list[tuple[str, ...]] = []

    def git(*args: str) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(stdout="b" * 40)

    monkeypatch.setattr(worker, "Transport", lambda *_args: SimpleNamespace(git=git))
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(worker, "native_prerequisites", lambda *_args, **_kw: pytest.fail("unrelated binaries"))
    monkeypatch.setattr(worker, "governed_feed", lambda: None)
    monkeypatch.setattr(adapter, "require_hosted_owner", lambda: None)
    import agent_procutil.owned_process as processes

    def metadata(*_args: Any, **_options: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout='{"system":"Windows"}', stderr="")

    monkeypatch.setattr(processes, "run_caller_owned", metadata)

    def qualify(*_args: Any, **_options: Any) -> dict[str, Any]:
        (output / "junit").mkdir()
        (output / "junit/source.xml").write_text(
            f'<testsuite><testcase classname="{contract.SOURCE_MODULE}" name="{contract.SOURCE_CASE}"/></testsuite>',
            encoding="utf-8",
        )
        return evidence()

    monkeypatch.setattr(adapter, "qualify", qualify)
    request = {
        "version": 1, "request_id": "a" * 32, "source_sha": "b" * 40,
        "selection": selected(), "envelope_sha": None,
    }
    result = worker.execute(root, request, output)
    assert result["outcome"] == "qualified"
    assert ("merge-base", "--is-ancestor", "c" * 40, "refs/remotes/origin/main") in calls
    assert ("merge-base", "--is-ancestor", "d" * 40, "refs/remotes/origin/dev") in calls
    assert "native_prerequisites" not in result


def test_source_packet_refuses_local_owner_before_feed_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "candidate"
    for file in contract.SOURCE_FILES:
        path = root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(worker, "Transport", lambda *_args: SimpleNamespace(
        git=lambda *_argv: SimpleNamespace(stdout="b" * 40),
    ))
    monkeypatch.setattr(adapter, "os", SimpleNamespace(name="nt", environ={}))
    monkeypatch.setattr(worker, "governed_feed", lambda: pytest.fail("feed touched before hosted admission"))
    request = {
        "version": 1, "request_id": "a" * 32, "source_sha": "b" * 40,
        "selection": selected(), "envelope_sha": None,
    }
    result = worker.execute(root, request, tmp_path / "evidence")
    assert result["outcome"] == "failed"
    assert "disposable hosted Windows owner" in result["error"]
    assert len(result["phases"]) == 1


@pytest.mark.parametrize("missing", [False, True])
def test_source_client_requires_candidate_files_and_published_controller_before_effects(
    tmp_path: Path, missing: bool,
) -> None:
    client_type = runpy.run_path(str(Path(__file__).with_name("windows-qualification.py")))["Client"]
    client = client_type.__new__(client_type)
    client.root = tmp_path
    client.state_root = tmp_path / "requests"
    calls: list[str] = []

    def git(*args: str) -> SimpleNamespace:
        if args[0] == "rev-parse":
            return SimpleNamespace(stdout="b" * 40)
        assert args[0] == "ls-tree"
        files = contract.SOURCE_FILES[:-1] if missing else contract.SOURCE_FILES
        return SimpleNamespace(stdout="\n".join(f"100644 blob {'a' * 40}\t{file}" for file in files))

    def preflight(value: dict[str, Any]) -> None:
        calls.append("preflight")
        assert value == selected()
        raise ValueError("protected controller does not support this request/result contract")

    client.transport = SimpleNamespace(git=git)
    client.preflight = preflight
    with pytest.raises(ValueError, match="missing" if missing else "protected controller"):
        client.submit(selected(), "HEAD")
    assert calls == ([] if missing else ["preflight"])
    assert not client.state_root.exists()


def test_workflow_keeps_source_feed_and_sanitation_inside_protected_packet() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/windows-qualification.yml").read_text("utf-8"))
    windows = workflow["jobs"]["windows"]
    assert windows["timeout-minutes"] == "${{ needs.admit.outputs.packet == 'manager-worktrees-source' && 60 || 40 }}"
    steps = {step.get("id", step.get("name")): step for step in windows["steps"]}
    assert steps["native"]["if"] == "needs.admit.outputs.native == 'true'"
    sanitize = steps["sanitize"]
    assert "always()" in sanitize["if"] and "manager-worktrees-source" in sanitize["if"]
    feed_steps = [step for step in windows["steps"] if "UV_DEFAULT_INDEX" in step.get("env", {})]
    assert len(feed_steps) == 2
    assert all("needs.admit.outputs.packet == 'manager-worktrees-source'" in step["env"]["UV_DEFAULT_INDEX"] for step in feed_steps)
    upload = next(step for step in windows["steps"] if "actions/upload-artifact" in step.get("uses", ""))
    assert "needs.admit.outputs.packet != 'manager-worktrees-source'" in upload["if"]
    assert "steps.sanitize.outcome == 'success'" in upload["if"]
