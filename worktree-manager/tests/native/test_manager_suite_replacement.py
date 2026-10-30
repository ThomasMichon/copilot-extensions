"""Prepared Windows whole-suite replacement evidence, never ordinary unit evidence.

The native owner invokes --prepare/--exercise with a finally-owned --settle in a
disposable hosted Windows VM. Selecting these tests without its evidence is a
failure, not a skipped or reduced-portfolio success.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import shutil
import subprocess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manager_suite_fixture import SCHEMA, exercise, prepare, require_venue, settle
from manager_suite_fixture import _record
import manager_suite_fixture as fixture
import manager_suite_diagnostics as diagnostics


def test_fullsuite_refuses_execution_outside_the_disposable_venue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="disposable GitHub-hosted Windows VM"):
        require_venue()


@pytest.mark.parametrize("name,routing_name,lock_name", (
    ("worktree-manager", "mux-daemon-routing", "mux-daemon.lock"),
    ("agent-worktrees", "status-monitor-routing", "status-monitor.lock"),
    ("agent-bridge", "", "running-version.json"),
    ("agent-dispatch", "", "running-version.json"),
    ("agent-index", "", "running-version.json"),
))
def test_observer_follows_successor_before_incumbent_releases_work_lease(
    tmp_path: Path, name: str, routing_name: str, lock_name: str,
) -> None:
    from zdd import routing

    (tmp_path / lock_name).write_text(json.dumps({"pid": 101}), encoding="utf-8")
    routing.publish_active(
        tmp_path / routing_name, bind="127.0.0.1", port=12345, pid=os.getpid(), version="successor",
    )
    assert _record(tmp_path, name)[0]["pid"] == os.getpid()
    assert json.loads((tmp_path / lock_name).read_text(encoding="utf-8"))["pid"] == 101


@pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Native PowerShell capture")
@pytest.mark.parametrize("owned_count", (0, 1, 2))
def test_task_inventory_captures_only_owned_unicode_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owned_count: int,
) -> None:
    (tmp_path / "project").mkdir()
    execute = fixture._run
    captured: list[str] = []
    prefix = (
        "function Get-ScheduledTask {"
        "foreach($i in 1..10){[pscustomobject]@{TaskName=\"foreign-$i\";TaskPath='\\';State='Ready'}};"
        f"for($i=0;$i -lt {owned_count};$i++){{"
        "[pscustomobject]@{TaskName=\"owned-$i\";TaskPath='\\';State='Ready'}}};"
        "function Export-ScheduledTask {param($TaskName,$TaskPath);"
        "if($TaskName.StartsWith('owned-')){"
        "\"<Task><Home>$caseHome\\owned</Home><Description>$([char]0x2014)</Description></Task>\""
        "}else{'<Task>foreign'+('x'*65536)+'</Task>'}};"
    )

    def run(argv: list[str], case: Path, **options: object) -> object:
        result = execute([*argv[:-1], prefix + argv[-1]], case, **options)
        captured.append(result.stdout)
        return result

    monkeypatch.setattr(fixture, "_run", run)
    rows = fixture._tasks(tmp_path)
    assert {row["name"] for row in rows} == {f"owned-{i}" for i in range(owned_count)}
    assert len(captured[0]) < 4096 and "foreign" not in captured[0]
    assert all("\u2014" in row["xml"] for row in rows)


def test_diagnostics_copy_only_advertised_request_logs(tmp_path: Path) -> None:
    root = tmp_path / "home" / ".copilot-extensions" / "update-driver"
    root.mkdir(parents=True)
    identifier = "a" * 32
    (root / (identifier + ".log")).write_text("actual failure", encoding="utf-8")
    (root / ("b" * 32 + ".log")).write_text("unrelated", encoding="utf-8")
    (root / "witness.json").write_text("opaque witness", encoding="utf-8")
    diagnostics.retain_driver_logs(tmp_path, f"Update request {identifier}: interrupted")
    copied = list((tmp_path / "driver-logs").iterdir())
    assert [path.name for path in copied] == [identifier + ".log"]
    assert copied[0].read_text(encoding="utf-8") == "actual failure"
    assert (root / "witness.json").read_text(encoding="utf-8") == "opaque witness"


def test_diagnostics_bound_logs_without_mutating_the_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "home" / ".copilot-extensions" / "update-driver"
    root.mkdir(parents=True)
    identifier = "a" * 32
    source = root / (identifier + ".log")
    source.write_bytes(b"old lines\nactual failure")
    monkeypatch.setattr(diagnostics, "MAX_LOG_BYTES", 14)
    diagnostics.retain_driver_logs(tmp_path, f"Update request {identifier}; log:")
    assert source.read_bytes() == b"old lines\nactual failure"
    assert (tmp_path / "driver-logs" / source.name).read_bytes() == b"actual failure"
    report = json.loads((tmp_path / "driver-diagnostics.json").read_text(encoding="utf-8"))
    assert report[0]["truncated"] is True


def test_missing_driver_log_does_not_replace_the_primary_error(tmp_path: Path) -> None:
    diagnostics.retain_driver_logs(tmp_path, f"Update request {'a' * 32}: interrupted")
    report = json.loads((tmp_path / "driver-diagnostics.json").read_text(encoding="utf-8"))
    assert report[0]["error"].startswith("FileNotFoundError:")


@pytest.mark.parametrize("check", (False, True))
def test_failed_commands_retain_driver_evidence_without_changing_their_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, check: bool,
) -> None:
    from agent_procutil import _caller_owned

    identifier = "a" * 32
    root = tmp_path / "home" / ".copilot-extensions" / "update-driver"
    root.mkdir(parents=True)
    (root / (identifier + ".log")).write_text("inner failure", encoding="utf-8")
    result = subprocess.CompletedProcess(["fixture"], 1, f"Update request {identifier}", "failure")
    monkeypatch.setattr(_caller_owned, "run", lambda *args, **kwargs: result)
    if check:
        with pytest.raises(AssertionError, match="failure"):
            fixture._run(["fixture"], tmp_path, check=True)
    else:
        assert fixture._run(["fixture"], tmp_path, check=False) is result
    assert (tmp_path / "driver-logs" / (identifier + ".log")).read_text(encoding="utf-8") == "inner failure"


@pytest.mark.parametrize("mode", ("normal", "repair"))
def test_installed_manager_replaces_the_whole_applicable_suite(mode: str) -> None:
    raw = os.environ.get("MANAGER_SUITE_ACCEPTANCE_DATA")
    assert raw, "Prepare/exercise both modes on a disposable GitHub-hosted Windows runner"
    directory = Path(raw).resolve()
    preparation_error = directory / mode / "preparation-error.json"
    assert not preparation_error.exists(), (
        preparation_error.read_text(encoding="utf-8") if preparation_error.exists() else ""
    )
    report = json.loads((directory / mode / "result.json").read_text(encoding="utf-8"))
    assert report["schema"] == SCHEMA and report["mode"] == mode
    assert report["venue"] == "github-hosted-windows"
    assert not report["errors"], json.dumps(report["errors"], indent=2)
    assert report["update"]["returncode"] == 0, (
        report["update"]["stdout"] + report["update"]["stderr"]
    )
    arguments = report["update"]["arguments"]
    if mode == "normal":
        assert arguments == ["update", "--project", "example"]
    else:
        assert arguments == [
            "update", "--repair-engine", report["source"], "--force", "--project", "example",
        ]

    declared = set(report["declared"])
    assert declared == set(report["before"]["portfolio"]) == set(report["after"]["portfolio"])
    assert len(declared) > 2, "A component/subset fixture is not whole-suite acceptance"
    assert set(report["applicability"]) == declared
    assert all(value["eligible"] for value in report["applicability"].values())
    assert report["before"]["engine"]["prefix"] != report["after"]["engine"]["prefix"]
    for generation in ("before", "after"):
        engine = report[generation]["engine"]
        assert Path(engine["module"]).is_relative_to(Path(engine["prefix"]))
        assert Path(engine["root"]) == directory / mode / "home" / ".agent-worktrees"
        assert set(report[generation]["versions"]) == declared | {"worktree-manager"}
    for name in declared | {"worktree-manager"}:
        assert report["before"]["versions"][name] != report["after"]["versions"][name]
        assert report["retained"][name], f"{name}: V1 sealed content changed or disappeared"
        expected = {
            entry["command"] + ("." + extension if extension else "")
            for entry in report["after"]["commands"][name] for extension in entry["variants"]
        }
        assert set(report["commands"][name]) == expected
        assert all(row["returncode"] == 0 and row["routes_current"] and row["proof"]
                   for row in report["commands"][name].values())
        before_origin = report["before"]["origins"][name]
        after_origin = report["after"]["origins"][name]
        assert before_origin["prefix"] != after_origin["prefix"]
        assert Path(after_origin["module"]).is_relative_to(Path(after_origin["prefix"]))
        assert report["exclusive_writers"][name], f"{name}: publication lease was not exclusive"

    assert report["durable_before"] == report["durable_after"]
    assert report["witness"] == {"rows": [["retained"]], "schema": 7}
    assert report["registrations"], "No actual scheduled-registration evidence"
    assert all(row["routes_current"] for row in report["registrations"])
    assert all(row["resolver_proofs"] for row in report["registrations"])
    assert report["index_engine_retained"], "The separate V1 embedding-engine slot changed"
    assert report["exclusive_work"], "The resident Manager did not retain exclusive-work custody"
    assert set(report["residents"]) == set(report["required_residents"])
    for name, row in report["residents"].items():
        assert row["old_pid"] != row["new_pid"], name
        assert row["old_version"].replace("-dev", ".dev") == (
            report["before"]["resident_targets"][name].replace("-dev", ".dev")
        ), name
        assert row["old_exit_code"] == 0, name
        assert row["old_exit_code_at_ready"] == 259, name
        assert row["new_exit_code_at_ready"] == row["new_exit_code"] == 259, name
        assert row["new_ready_observed_ns"] <= row["old_exit_ns"], name
        assert row["old_created_ns"] < row["new_created_ns"], name
        assert row["new_version"].replace("-dev", ".dev") == (
            report["after"]["resident_targets"][name].replace("-dev", ".dev")
        ), name
        if name == "worktree-manager":
            assert row["old_runtime_generation"] == report["before"]["versions"][name]
            assert row["new_runtime_generation"] == report["after"]["versions"][name]
    assert not report["cleanup_errors"], json.dumps(report["cleanup_errors"], indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    stages = parser.add_mutually_exclusive_group(required=True)
    stages.add_argument("--prepare", type=Path)
    stages.add_argument("--exercise", type=Path)
    stages.add_argument("--settle", type=Path)
    parser.add_argument("--mode", choices=("normal", "repair"), required=True)
    options = parser.parse_args()
    if options.prepare is not None:
        prepare(options.prepare, options.mode)
    elif options.exercise is not None:
        exercise(options.exercise, options.mode)
    else:
        settle(options.settle, options.mode)
