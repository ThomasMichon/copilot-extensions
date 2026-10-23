"""The real doctor parser/consumer, restricted to explicit synthetic resources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

import pytest

from agent_worktrees import doctor_cli_parser, maintenance_cli, recovery_doctor
from agent_worktrees.recovery_facts import MAX_BYTES
from agent_worktrees.recovery_markers import marker_value

REPO = str(UUID(int=1))
RESOURCE = str(UUID(int=2))
OPERATION = str(UUID(int=3))


def encode(value):
    return json.dumps(value).encode()


def fact(operation=OPERATION):
    return {
        "schema": "agent-worktrees.recovery-fact", "version": 1,
        "repository_id": REPO, "resource_id": RESOURCE, "operation_id": operation,
        "writer_incarnation": str(UUID(int=4)), "revision": 1,
        "kind": "fixture.observation", "required_capabilities": {"read": [], "patch": []},
        "payload": {"future_optional": {"unknown": [1, "retain"]}},
    }


def fixture(tmp_path, *, markers=True, facts=True):
    worktree, common = tmp_path / "worktree", tmp_path / "common"
    worktree.mkdir()
    common.mkdir()
    marker_dir = worktree / ".agent-worktrees-recovery"
    journal = common / "agent-worktrees-recovery"
    if markers:
        for folder, marker in ((marker_dir, marker_value(REPO, RESOURCE)),
                               (journal, marker_value(REPO))):
            folder.mkdir()
            (folder / "identity.json").write_bytes(encode(marker))
    if facts:
        journal.mkdir(exist_ok=True)
        (journal / f"{OPERATION}.json").write_bytes(encode(fact()))
    # These stand for protected source bytes; report must not even open them.
    for name in ("index", "stash", "untracked", "ignored", "checkpoint", "credentials"):
        (worktree / name).write_bytes(b"synthetic protected bytes")
    request = {
        "schema": "agent-worktrees.recovery-report-request", "version": 1,
        "repository_id": REPO, "resource_id": RESOURCE,
        "worktree_root": str(worktree), "git_common_dir": str(common),
        "marker_ignored": True, "central_record_path": str(common / "central.yaml"),
        "fact_files": [f"{OPERATION}.json"] if facts else [],
    }
    return request, marker_dir, journal


def run_report(tmp_path, request, capsys, *extra):
    source = tmp_path / "request.json"
    source.write_bytes(encode(request))
    parser = argparse.ArgumentParser()
    doctor_cli_parser.add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["doctor", "--recovery-report", str(source), "--json", *extra])
    code = maintenance_cli.cmd_doctor(args)
    return code, json.loads(capsys.readouterr().out)


def assert_no_authority(report):
    assert report["migration_completeness"] == "unknown"
    assert report["migration_reason"] == "g2_manifest_unavailable"
    assert report["obligations"] == "unknown"
    assert report["writer_authorized"] is False
    assert report["effects_admitted"] is False
    assert report["outcome"] != "complete"


@pytest.fixture(autouse=True)
def no_live_dependencies(monkeypatch):
    from agent_worktrees import tracking_yaml_repair
    def forbidden(*_args, **_kwargs):
        pytest.fail("report entered live discovery, provisioning or repair")
    monkeypatch.setattr(maintenance_cli, "_core", forbidden)
    monkeypatch.setattr(maintenance_cli.cfg, "project_name", forbidden)
    monkeypatch.setattr(maintenance_cli.inst, "read_projects_registry", forbidden)
    monkeypatch.setattr(maintenance_cli.tracking, "list_records", forbidden)
    monkeypatch.setattr(maintenance_cli.sessions, "_session_state_dir", forbidden)
    monkeypatch.setattr(tracking_yaml_repair, "apply_repair", forbidden)
    monkeypatch.setattr(maintenance_cli.subprocess, "run", forbidden)


def test_missing_cache_with_surviving_anchors_stays_partial(tmp_path, capsys):
    request, _, _ = fixture(tmp_path)
    code, report = run_report(tmp_path, request, capsys)
    assert code == 0
    assert report["central_record"] == {"status": "missing", "reason": "source_missing"}
    assert report["outcome"] == "partial"
    assert report["facts"][0]["status"] == "candidate"
    assert_no_authority(report)


@pytest.mark.parametrize("asserted_complete", [False, True])
def test_markers_do_not_certify_migration(tmp_path, capsys, asserted_complete):
    request, _, _ = fixture(tmp_path, facts=False)
    request["migration_complete"] = asserted_complete
    request["g2_manifest"] = {"all_migrated": True}
    code, report = run_report(tmp_path, request, capsys)
    assert code == 0
    assert report["anchors"]["status"] == "candidate"
    assert report["facts"] == []
    assert_no_authority(report)


def test_absent_markers_are_not_empty_obligations(tmp_path, capsys):
    request, _, _ = fixture(tmp_path, markers=False, facts=False)
    code, report = run_report(tmp_path, request, capsys)
    assert code == 0
    assert report["anchors"]["status"] == "missing"
    assert report["outcome"] == "unknown"
    assert_no_authority(report)


@pytest.mark.parametrize(("mutation", "status", "reason"), [
    ("foreign", "conflict", "identity_conflict"),
    ("unsupported", "unsupported", "unsupported_schema"),
    ("capability", "unsupported", "required_capability_unsupported"),
    ("malformed", "unknown", "malformed_json"),
    ("truncated", "unknown", "malformed_json"),
    ("missing", "missing", "source_missing"),
    ("optional", "candidate", "envelope_readable"),
])
def test_fact_candidate_outcomes_preserve_bytes(tmp_path, capsys, mutation, status, reason):
    request, _, journal = fixture(tmp_path)
    path = journal / f"{OPERATION}.json"
    value = fact()
    if mutation == "foreign":
        value["resource_id"] = str(UUID(int=8))
    elif mutation == "unsupported":
        value["version"] = 2
    elif mutation == "capability":
        value["required_capabilities"]["read"] = ["unqualified.semantic.reader"]
    elif mutation == "optional":
        value["future"] = {"keep": ["all", "data"]}
    path.write_bytes(encode(value))
    if mutation in {"malformed", "truncated"}:
        path.write_bytes(b'{"truncated":' if mutation == "truncated" else b"\xff")
    if mutation == "missing":
        path.unlink()  # Only this synthetic fact; no projection-loss claim.
    before = path.read_bytes() if path.exists() else None
    code, report = run_report(tmp_path, request, capsys)
    assert code == 0
    assert report["facts"][0] == {"name": path.name, "status": status, "reason": reason}
    assert (path.read_bytes() if path.exists() else None) == before
    assert_no_authority(report)


def test_foreign_marker_is_conflicting_evidence_not_a_foreign_owner(tmp_path, capsys):
    request, directory, _ = fixture(tmp_path)
    path = directory / "identity.json"
    path.write_bytes(encode(marker_value(REPO, str(UUID(int=5)))))
    before = path.read_bytes()
    _, report = run_report(tmp_path, request, capsys)
    assert report["anchors"]["status"] == "conflict"
    assert report["facts_checked"] == 0
    assert report["facts_omitted"] == 1
    assert path.read_bytes() == before
    assert_no_authority(report)


@pytest.mark.parametrize("budget", [0, 1, 2])
def test_explicit_discovery_is_bounded_and_labeled(tmp_path, capsys, budget):
    request, _, journal = fixture(tmp_path)
    request["fact_files"] = []
    for index in range(10, 13):
        operation = str(UUID(int=index))
        name = f"{operation}.json"
        (journal / name).write_bytes(encode(fact(operation)))
        request["fact_files"].append(name)
    request["fact_budget"] = budget
    _, report = run_report(tmp_path, request, capsys)
    assert report["discovery"] == "explicit-bounded-file-selection"
    assert report["facts_checked"] == budget
    assert report["facts_omitted"] == 3 - budget
    assert_no_authority(report)


@pytest.mark.parametrize("flag", [
    "--fix", "--gc-sessions", "--prune-pivots", "--apply-daemon-health",
    "--repair-record", "--repair-machine-record", "--repair-creation",
    "--guest-distribution", "--seed-id", "--from-machine", "--to-machine",
])
def test_mixed_repair_options_refuse_before_input_reads(tmp_path, capsys, monkeypatch, flag):
    request, _, _ = fixture(tmp_path)
    def forbidden(*_args):
        pytest.fail("mixed report options must refuse before reading")
    monkeypatch.setattr(recovery_doctor, "read_bounded", forbidden)
    extra = [flag] if flag in {"--fix", "--gc-sessions", "--prune-pivots",
                              "--apply-daemon-health"} else [flag, "synthetic"]
    code, report = run_report(tmp_path, request, capsys, *extra)
    assert code == 1
    assert report["error"] == "report_only_option_conflict"
    assert_no_authority(report)


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("fact_budget", 33, "invalid_selection"),
    ("fact_budget", True, "invalid_selection"),
    ("fact_files", ["../escape.json"], "malformed_identity"),
    ("fact_files", [f"{OPERATION}.json"] * 2, "invalid_selection"),
    ("version", 2, "unsupported_schema"),
    ("required_capabilities", ["unknown"], "required_capability_unsupported"),
    ("marker_ignored", False, "marker_not_ignored"),
    ("worktree_root", "relative", "invalid_root"),
])
def test_invalid_request_is_scoped(tmp_path, capsys, field, value, reason):
    request, _, _ = fixture(tmp_path)
    request[field] = value
    code, report = run_report(tmp_path, request, capsys)
    assert code == 1
    assert report["error"] == reason
    assert_no_authority(report)


def test_protected_content_and_present_cache_are_never_opened(tmp_path, capsys, monkeypatch):
    request, _, _ = fixture(tmp_path)
    cache = Path(request["central_record_path"])
    cache.write_bytes(b"not a parsed tracking record")
    protected = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                 for path in tmp_path.rglob("*") if path.is_file()}
    original = Path.open
    def observed(path, *args, **kwargs):
        assert path.name in {"request.json", "identity.json", f"{OPERATION}.json"}
        assert args[0] == "rb"
        return original(path, *args, **kwargs)
    # Prepare request before forbidding all writes by the real consumer.
    source = tmp_path / "request.json"
    source.write_bytes(encode(request))
    parser = argparse.ArgumentParser()
    doctor_cli_parser.add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["doctor", "--recovery-report", str(source), "--json"])
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", observed)
        assert maintenance_cli.cmd_doctor(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["central_record"]["reason"] == "central_record_unexamined"
    for path, before in protected.items():
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert_no_authority(report)


@pytest.mark.parametrize("history", [0, 100, 1000])
def test_ordinary_report_cost_does_not_depend_on_history(tmp_path, capsys, monkeypatch, history):
    request, _, journal = fixture(tmp_path, facts=False)
    for index in range(history):
        (journal / f"historical-{index}").write_bytes(b"unread history")
    source = tmp_path / "request.json"
    source.write_bytes(encode(request))
    parser = argparse.ArgumentParser()
    doctor_cli_parser.add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["doctor", "--recovery-report", str(source), "--json"])
    opens = []
    lstats = []
    original = Path.open
    original_lstat = Path.lstat
    def observed(path, *arguments, **kwargs):
        opens.append(path.name)
        return original(path, *arguments, **kwargs)
    def observe_lstat(path, *arguments, **kwargs):
        lstats.append(path)
        return original_lstat(path, *arguments, **kwargs)
    def forbidden(*_args, **_kwargs):
        pytest.fail("ordinary report must not enumerate history")
    monkeypatch.setattr(Path, "open", observed)
    monkeypatch.setattr(Path, "lstat", observe_lstat)
    for method in ("iterdir", "glob", "rglob"):
        monkeypatch.setattr(Path, method, forbidden)
    assert maintenance_cli.cmd_doctor(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert opens == ["request.json", "identity.json", "identity.json"]
    assert len(lstats) == 6 * (len(tmp_path.parents) + 1) + 11
    assert not any(path.name.startswith("historical-") for path in lstats)
    assert report["discovery"] == "none"
    assert report["facts_checked"] == 0
    assert_no_authority(report)


def test_existing_targeted_report_dispatch_is_unchanged(monkeypatch):
    from agent_worktrees import tracking_yaml_repair
    called = []
    monkeypatch.setattr(tracking_yaml_repair, "targeted_doctor",
                        lambda args: called.append(args.repair_record) or 17)
    parser = argparse.ArgumentParser()
    doctor_cli_parser.add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["doctor", "--repair-record", "fixture-record"])
    assert maintenance_cli.cmd_doctor(args) == 17
    assert called == ["fixture-record"]


def test_request_file_byte_limit(tmp_path, capsys):
    source = tmp_path / "request.json"
    source.write_bytes(b" " * (MAX_BYTES + 1))
    args = argparse.Namespace(recovery_report=str(source), json=True)
    assert recovery_doctor.targeted_report(args) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"] == "record_limit"
    assert_no_authority(report)


@pytest.mark.parametrize("raw", [b'{"unfinished":', b'{"x":1,"x":2}', b"\xff"])
def test_invalid_request_bytes_return_typed_reports(tmp_path, capsys, raw):
    source = tmp_path / "request.json"
    source.write_bytes(raw)
    assert recovery_doctor.targeted_report(
        argparse.Namespace(recovery_report=str(source), json=True),
    ) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error"] in {"malformed_request", "duplicate_field"}
    assert source.read_bytes() == raw
    assert_no_authority(report)


def test_text_report_is_not_a_readiness_signal(tmp_path, capsys):
    request, _, _ = fixture(tmp_path)
    request["future_optional"] = {"retain": ["unrecognized"]}
    source = tmp_path / "request.json"
    raw = encode(request)
    source.write_bytes(raw)
    assert recovery_doctor.targeted_report(
        argparse.Namespace(recovery_report=str(source), json=False),
    ) == 0
    output = capsys.readouterr().out
    assert "migration=unknown" in output
    assert "effects=not-admitted" in output
    assert source.read_bytes() == raw


@pytest.mark.parametrize("field", ["worktree_root", "git_common_dir", "central_record_path"])
def test_unencodable_input_paths_are_typed(tmp_path, capsys, field):
    request, _, _ = fixture(tmp_path)
    request[field] = str(tmp_path / "\ud800")
    code, report = run_report(tmp_path, request, capsys)
    assert code == 1
    assert report["error"] == "invalid_root"
    assert_no_authority(report)


def test_unencodable_request_filename_is_typed(tmp_path, capsys):
    args = argparse.Namespace(recovery_report=str(tmp_path / "\ud800"), json=True)
    assert recovery_doctor.targeted_report(args) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_root"


@pytest.mark.parametrize("project", [
    ["--project", "fixture"], ["--project=fixture"], ["-p", "fixture"], [],
])
@pytest.mark.parametrize("fix", [False, True])
@pytest.mark.parametrize("option", ["--rec", "--recovery-rep", "--recovery-report"])
@pytest.mark.parametrize("equals", [False, True])
def test_actual_front_door_preserves_cwd_and_skips_project_discovery(
    tmp_path, capsys, monkeypatch, project, fix, option, equals,
):
    from agent_worktrees import __main__ as main
    request, _, _ = fixture(tmp_path)
    source = tmp_path / "request.json"
    source.write_bytes(encode(request))
    monkeypatch.chdir(tmp_path)
    def forbidden(*_args, **_kwargs):
        pytest.fail("exact recovery report entered project discovery or CWD mutation")
    monkeypatch.setattr(main, "_resolve_active_project", forbidden)
    monkeypatch.setattr(main, "_cwd_is_inside_project", forbidden)
    monkeypatch.setattr(main.os, "chdir", forbidden)
    selector = [f"{option}=request.json"] if equals else [option, "request.json"]
    argv = [*project, "doctor", *selector, "--json"]
    if fix:
        argv.append("--fix")
    assert main.main(argv) == int(fix)
    report = json.loads(capsys.readouterr().out)
    if fix:
        assert report["error"] == "report_only_option_conflict"
    else:
        assert report["anchors"]["status"] == "candidate"
    assert_no_authority(report)
