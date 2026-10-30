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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manager_suite_fixture import SCHEMA, exercise, prepare, require_venue, settle
from manager_suite_fixture import _record


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
