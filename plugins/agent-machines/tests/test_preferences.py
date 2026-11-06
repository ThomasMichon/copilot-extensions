from __future__ import annotations

import json
from copy import deepcopy

import pytest

from agent_machines import preferences as prefs
from agent_machines.__main__ import main


def snapshot(revision=1, parent=None, **changes):
    return {
        "schema": prefs.SCHEMA, "version": 1, "publisher": "example-publisher",
        "revision": revision, "parent": parent,
        "domains": {
            "copilot": {"model": "example-model", "effortLevel": "high"},
            "repositories": {"knowledge": [
                {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-shared"},
                {"harness_repository_id": "repo-second", "knowledge_repository_id": "repo-shared"},
            ]},
        }, **changes,
    }


def preview(candidate, accepted=None, **changes):
    return prefs.preview(
        candidate, accepted, publisher="example-publisher", machine="example-worker",
        harness="repo-first", **changes,
    )


def local(domains, machine="example-worker"):
    return {"schema": prefs.LOCAL_SCHEMA, "version": 1, "machine": machine, "domains": domains}


def test_distinct_harnesses_share_knowledge_without_basename_resolution():
    candidate = snapshot()
    before = deepcopy(candidate)
    first = preview(candidate)
    second = prefs.preview(
        candidate, None, publisher="example-publisher", machine="example-worker",
        harness="repo-second",
    )
    assert first["knowledge_preference"] == second["knowledge_preference"] == {
        "status": "suggested", "knowledge_repository_id": "repo-shared",
    }
    assert candidate == before
    assert first["scope"]["identity_resolution"] == "not-performed"
    assert first["adoption"]["repositories"] == "native-owner-required"
    assert first["binding_changed"] is False


def test_named_machine_then_local_mapping_and_suppression_leave_other_defaults():
    candidate = snapshot(machines={
        "EXAMPLE-WORKER": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-machine"},
        ]}},
        "example-worker-wsl": {"copilot": {"model": "guest-model"}},
    })
    named = preview(candidate)
    assert named["knowledge_preference"]["knowledge_repository_id"] == "repo-machine"
    assert named["values"]["copilot"]["model"] == "example-model"
    chosen = preview(candidate, local=local({"repositories": {"knowledge": [
        {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-local"},
    ]}}))
    assert chosen["knowledge_preference"]["knowledge_repository_id"] == "repo-local"
    assert chosen["provenance"]["repositories.knowledge:repo-first"] == "local-machine"
    suppressed = preview(candidate, local=local({"repositories": {"knowledge": [
        {"harness_repository_id": "repo-first", "knowledge_repository_id": None},
    ]}}))
    assert suppressed["knowledge_preference"]["status"] == "suppressed"
    assert len(suppressed["values"]["repositories"]["knowledge"]) == 2
    with pytest.raises(prefs.PreferenceError, match="local-machine-mismatch"):
        preview(candidate, local=local({}, machine="example-worker-wsl"))


@pytest.mark.parametrize(("candidate", "reason"), [
    (snapshot(1, domains={}), "revision-conflict"),
    (snapshot(1), "older-revision"),
    (snapshot(4, 3), "parent-gap"),
    (snapshot(3, 2, publisher="other-publisher"), "publisher-mismatch"),
    (None, "unavailable"),
])
def test_rejected_or_unavailable_candidate_preserves_accepted_projection(candidate, reason):
    accepted = snapshot() if reason == "revision-conflict" else snapshot(2, 1)
    result = preview(candidate, accepted)
    assert result["status"] == reason
    assert result["candidate_eligible"] is False
    assert result["projected_revision"] == accepted["revision"]
    assert result["values"]["copilot"]["model"] == "example-model"


def test_duplicate_conflict_and_complete_snapshot_withdrawal():
    accepted = snapshot()
    assert preview(accepted, accepted)["status"] == "unchanged"
    assert preview(accepted, accepted, conflict=True)["status"] == "carrier-conflict"
    withdrawn = snapshot(2, 1, domains={})
    result = preview(withdrawn, accepted)
    assert result["status"] == "pending"
    assert result["values"] == {}
    assert result["knowledge_preference"]["status"] == "absent"
    assert preview(withdrawn)["status"] == "parent-gap"
    override = preview(withdrawn, accepted, local=local({"copilot": {"effortLevel": "low"}}))
    assert override["values"] == {"copilot": {"effortLevel": "low"}}


@pytest.mark.parametrize("raw", [
    b'{"schema":"one","schema":"two"}', b'{"x":NaN}', b'\xff', b'{"partial":',
    b'[' * 32 + b'0' + b']' * 32,
])
def test_strict_decoder_refuses_duplicate_invalid_partial_and_nested_documents(raw):
    with pytest.raises(prefs.PreferenceError):
        prefs.decode(raw)


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(version=True),
    lambda value: value.update(version=2),
    lambda value: value.update(revision=True),
    lambda value: value.update(parent=1),
    lambda value: value.update(publisher="path/to/publisher"),
    lambda value: value["domains"].update(unknown={}),
    lambda value: value["domains"].update(maintenance={"command": "run-this"}),
    lambda value: value["domains"].update(telemetry={"directory": "private-path"}),
    lambda value: value["domains"].update(copilot={"permissions": ["all"]}),
    lambda value: value.update(machines={"worker": {}, "WORKER": {}}),
    lambda value: value["domains"]["repositories"]["knowledge"].append(
        {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-other"},
    ),
])
def test_schema_never_admits_commands_paths_authority_or_ambiguous_relationships(mutate):
    value = snapshot()
    mutate(value)
    with pytest.raises(prefs.PreferenceError):
        prefs.validate(value)


def test_bounds_apply_to_the_whole_snapshot_not_each_machine_fragment(tmp_path):
    value = snapshot()
    value["machines"] = {f"worker-{index}": {} for index in range(prefs.MAX_MACHINES + 1)}
    with pytest.raises(prefs.PreferenceError, match="too-many-machines"):
        prefs.validate(value)
    relationships = [
        {"harness_repository_id": f"repo-{index}", "knowledge_repository_id": "repo-shared"}
        for index in range(prefs.MAX_RELATIONSHIPS)
    ]
    value = snapshot(machines={"worker": {"repositories": {"knowledge": relationships}}})
    with pytest.raises(prefs.PreferenceError, match="too-many-relationships"):
        prefs.validate(value)
    path = tmp_path / "oversize.json"
    path.write_bytes(b" " * (prefs.MAX_BYTES + 1))
    with pytest.raises(prefs.PreferenceError, match="document-too-large"):
        prefs.read(path)


def test_normal_cli_only_reads_explicit_files_and_retains_offline_accepted_defaults(tmp_path, capsys, monkeypatch):
    from agent_machines import __main__ as cli
    from agent_machines import configuration

    def forbidden(*args, **kwargs):
        raise AssertionError("preview invoked a native discovery/configuration effect")

    monkeypatch.setattr(cli, "_collect_reconcile_packages", forbidden)
    monkeypatch.setattr(cli, "_resolve_machine_identity", forbidden)
    monkeypatch.setattr(configuration, "owner_report", forbidden)
    candidate, accepted = tmp_path / "candidate.json", tmp_path / "accepted.json"
    candidate.write_text(json.dumps(snapshot(2, 1, domains={
        "telemetry": {"capture": "retained-local", "reporting": "local"},
        "maintenance": {"updates": "automatic"},
    })))
    accepted.write_text(json.dumps(snapshot()))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    args = [
        "preferences", "preview", "--input", str(candidate), "--accepted", str(accepted),
        "--publisher", "example-publisher", "--machine", "example-worker", "--harness", "repo-first",
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["values"]["maintenance"] == {"updates": "automatic"}
    assert result["configuration_applied"] is False
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    candidate.unlink()
    assert main(args) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "unavailable"
    assert result["knowledge_preference"]["knowledge_repository_id"] == "repo-shared"
    assert not candidate.exists()
    candidate.write_text('{"private-example":"never echo malformed input"')
    assert main(args) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err) == {"ok": False, "error": "invalid-json"}


def test_replaced_file_is_rejected_before_reading_other_identity(tmp_path, monkeypatch):
    path, replacement = tmp_path / "input.json", tmp_path / "replacement.json"
    path.write_text(json.dumps(snapshot()))
    replacement.write_text(json.dumps(snapshot(2, 1)))
    original_open = prefs.os.open

    def replaced(candidate, flags):
        path.rename(tmp_path / "old.json")
        replacement.replace(path)
        return original_open(candidate, flags)

    monkeypatch.setattr(prefs.os, "open", replaced)
    with pytest.raises(prefs.PreferenceError, match="input-changed"):
        prefs.read(path)


def test_cli_sanitizes_overlong_json_integer_on_runtimes_with_or_without_digit_limits(tmp_path, capsys):
    path = tmp_path / "overlong-integer.json"
    raw = json.dumps(snapshot()).replace('"revision": 1', '"revision": ' + "1" * 5000)
    path.write_text(raw, encoding="utf-8")
    assert main([
        "preferences", "preview", "--input", str(path),
        "--publisher", "example-publisher", "--machine", "example-worker", "--harness", "repo-first",
    ]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    diagnostic = json.loads(output.err)
    assert diagnostic["ok"] is False
    assert diagnostic["error"] in {"invalid-json", "invalid-revision"}
