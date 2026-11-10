from __future__ import annotations

import json
import os

import pytest

from agent_machines import preference_carrier as carrier
from agent_machines import preference_exchange as local_exchange
from agent_machines import preferences as codec
from agent_machines.__main__ import main


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def snapshot(revision=1, parent=None, **changes):
    return {
        "schema": codec.SCHEMA, "version": 1, "publisher": "example-publisher",
        "revision": revision, "parent": parent,
        "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-shared"},
            {"harness_repository_id": "repo-second", "knowledge_repository_id": "repo-shared"},
        ]}}, **changes,
    }


@pytest.fixture
def inputs(tmp_path):
    state, replica = tmp_path / "state", tmp_path / "replica"
    state.mkdir()
    replica.mkdir()
    write(state / "policy.json", {
        "schema": "agent-machines.preferences.policy", "version": 1,
        "publisher": "example-publisher", "participant": "example-publisher",
        "domains": ["repositories", "copilot", "maintenance", "telemetry"],
    })
    write(state / "carrier.json", {
        "schema": "agent-machines.preferences.carrier", "version": 1,
        "provider": "onedrive", "account": "example-account", "directory": str(replica),
        "participants": ["example-publisher", "example-receiver"],
    })
    source = write(tmp_path / "candidate.json", snapshot())
    return {"state": state, "source": source, "replica": replica}


def call(inputs, operation="receive", *, apply=False, expected="", **changes):
    arguments = (
        {"source": inputs["source"]} if operation == "publish"
        else {"machine": "example-worker", "harness": "repo-first"}
    )
    return carrier.exchange(
        operation, state=inputs["state"], apply=apply, expected=expected, **{**arguments, **changes},
    )


def commit(inputs, operation="receive", **changes):
    plan = call(inputs, operation, **changes)
    return call(inputs, operation, apply=True, expected=plan["preview_token"], **changes)


def test_carrier_publish_and_receive_reuse_existing_watermarks_without_native_adoption(inputs):
    replica = inputs["replica"]
    before = sorted(path.name for path in inputs["state"].iterdir())
    plan = call(inputs, "publish")
    assert plan["eligible"] and not plan["local_published"]
    assert not list(replica.iterdir())
    assert sorted(path.name for path in inputs["state"].iterdir()) == before
    result = call(inputs, "publish", apply=True, expected=plan["preview_token"])
    assert result["local_published"] and result["remote_receipt"] == "not-observed"
    assert not (inputs["state"] / "accepted.json").exists()
    assert codec.read(replica / carrier.DOCUMENT) == snapshot()
    assert codec.read(inputs["state"] / "publication.json") == snapshot()
    plan = call(inputs)
    assert not plan["configuration_applied"] and not plan["binding_changed"]
    assert plan["carrier"] == {
        "provider": "onedrive", "binding": "configured-not-provider-verified",
        "namespace": "available", "conflict": False, "synchronization": "not-observed",
        "local_replica_observed": True, "peer_receipt": "not-observed",
    }
    assert commit(inputs)["applied"]
    assert codec.read(inputs["state"] / "accepted.json") == snapshot()
    assert sorted(path.name for path in replica.iterdir()) == [carrier.DOCUMENT]


def test_carrier_withdrawal_offline_reorder_and_machine_override_never_rebind(inputs):
    commit(inputs, "publish")
    commit(inputs)
    local = write(inputs["source"].parent / "overrides.json", {
        "schema": codec.LOCAL_SCHEMA, "version": 1, "machine": "example-worker",
        "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-first", "knowledge_repository_id": None},
        ]}},
    })
    write(inputs["source"], snapshot(2, 1, domains={}))
    commit(inputs, "publish")
    received = commit(inputs, local=local)
    assert received["projection"]["knowledge_preference"]["status"] == "suppressed"
    assert not received["binding_changed"]
    (inputs["replica"] / carrier.DOCUMENT).unlink()
    inputs["replica"].rmdir()
    unavailable = call(inputs, local=local)
    assert unavailable["status"] == "carrier-unavailable" and not unavailable["eligible"]
    assert unavailable["projection"]["projected_revision"] == 2
    assert unavailable["projection"]["knowledge_preference"]["status"] == "suppressed"
    with pytest.raises(codec.PreferenceError, match="carrier-unavailable"):
        commit(inputs, "publish")
    inputs["replica"].mkdir()
    write(inputs["replica"] / carrier.DOCUMENT, snapshot())
    stale = call(inputs, full_snapshot=True, local=local)
    assert stale["status"] == "older-revision" and not stale["eligible"]
    assert codec.read(inputs["state"] / "accepted.json")["revision"] == 2
    with pytest.raises(codec.PreferenceError, match="local-machine-mismatch"):
        call(inputs, machine="example-worker-wsl", local=local)


@pytest.mark.parametrize("operation", ["receive", "publish"])
def test_unexpected_entries_are_conflicts_never_read_or_selected_by_mtime(inputs, operation):
    write(inputs["replica"] / carrier.DOCUMENT, snapshot())
    foreign = inputs["replica"] / "preferences-conflicted-copy.json"
    foreign.write_bytes(b"not JSON, never read")
    result = call(inputs, operation, full_snapshot=True)
    assert result["status"] == "carrier-conflict" and not result["eligible"]
    assert result["carrier"]["conflict"]
    with pytest.raises(codec.PreferenceError, match="carrier-conflict"):
        commit(inputs, operation)
    assert not (inputs["state"] / "accepted.json").exists()
    assert not (inputs["state"] / "publication.json").exists()
    assert foreign.read_bytes() == b"not JSON, never read"


@pytest.mark.parametrize("change", ["account", "directory", "participants", "entry", "candidate", "watermark"])
def test_carrier_and_inventory_are_rechecked_under_the_existing_writer_lock(inputs, monkeypatch, change):
    token = call(inputs, "publish")["preview_token"]
    original_lock = local_exchange.mutation_lock

    def lock(path):
        config_path = inputs["state"] / "carrier.json"
        config = json.loads(config_path.read_bytes())
        if change == "account":
            config["account"] = "changed-account"
        elif change == "directory":
            other = inputs["replica"].parent / "other-replica"
            other.mkdir()
            config["directory"] = str(other)
        elif change == "participants":
            config["participants"].append("another-receiver")
        elif change == "entry":
            (inputs["replica"] / "unexpected").mkdir()
        elif change == "candidate":
            write(inputs["source"], snapshot(domains={}))
        elif change == "watermark":
            write(inputs["state"] / "publication.json", snapshot())
        write(config_path, config)
        return original_lock(path)

    monkeypatch.setattr(local_exchange, "mutation_lock", lock)
    with pytest.raises(codec.PreferenceError, match="preview-changed"):
        call(inputs, "publish", apply=True, expected=token)
    assert not (inputs["replica"] / carrier.DOCUMENT).exists()


@pytest.mark.parametrize(("changes", "reason"), [
    ({"version": True}, "unsupported-carrier"),
    ({"version": 2}, "unsupported-carrier"),
    ({"provider": "unknown"}, "unsupported-carrier"),
    ({"directory": "relative"}, "invalid-carrier-directory"),
    ({"directory": "bad\x00directory"}, "invalid-carrier-directory"),
    ({"participants": ["example-publisher", "EXAMPLE-PUBLISHER"]}, "duplicate-carrier-participant"),
    ({"participants": ["example-receiver"]}, "carrier-participant-not-permitted"),
    ({"participants": []}, "invalid-carrier-participants"),
    ({"account": "https://example.com"}, "invalid-identifier"),
    ({"command": "execute"}, "invalid-fields"),
])
def test_local_carrier_schema_is_strict_and_not_received_authority(inputs, changes, reason):
    path = inputs["state"] / "carrier.json"
    write(path, {**json.loads(path.read_bytes()), **changes})
    with pytest.raises(codec.PreferenceError, match=reason):
        call(inputs, "publish")
    assert not list(inputs["replica"].iterdir())


def test_bounded_census_does_not_recurse_or_accept_a_polluted_namespace(inputs):
    for index in range(carrier.MAX_ENTRIES + 1):
        (inputs["replica"] / str(index)).mkdir()
    with pytest.raises(codec.PreferenceError, match="carrier-namespace-too-large"):
        call(inputs)


@pytest.mark.parametrize("overlap", ["state-inside", "replica-inside", "source", "local"])
def test_local_policy_watermarks_candidates_and_overrides_stay_outside_replica(inputs, overlap):
    path = inputs["state"] / "carrier.json"
    config = json.loads(path.read_bytes())
    changes = {}
    if overlap == "state-inside":
        config["directory"] = str(inputs["state"].parent)
    elif overlap == "replica-inside":
        config["directory"] = str(inputs["state"] / "replica")
    elif overlap == "source":
        changes["source"] = write(inputs["replica"] / "candidate.json", snapshot())
    else:
        changes["local"] = write(inputs["replica"] / "overrides.json", {})
    write(path, config)
    with pytest.raises(codec.PreferenceError, match="carrier-local-state-overlap"):
        call(inputs, "publish", **changes)


def test_carrier_hardlink_cannot_alias_local_policy_or_watermark(inputs):
    os.link(inputs["state"] / "policy.json", inputs["replica"] / carrier.DOCUMENT)
    with pytest.raises(codec.PreferenceError, match="overlapping-paths"):
        call(inputs)


def test_carrier_enforces_policy_domains_and_publisher_role(inputs):
    policy_path = inputs["state"] / "policy.json"
    policy = json.loads(policy_path.read_bytes())
    policy["domains"] = ["copilot"]
    write(policy_path, policy)
    assert call(inputs, "publish")["status"] == "domain-not-permitted"
    policy["domains"] = ["repositories"]
    policy["participant"] = "example-receiver"
    write(policy_path, policy)
    assert call(inputs, "publish")["status"] == "not-publisher"
    assert not list(inputs["replica"].iterdir())
    write(inputs["replica"] / carrier.DOCUMENT, snapshot())
    assert commit(inputs)["applied"]


def test_unknown_snapshot_schema_never_becomes_a_carrier_configuration(inputs):
    config = json.loads((inputs["state"] / "carrier.json").read_bytes())
    write(inputs["replica"] / carrier.DOCUMENT, config)
    with pytest.raises(codec.PreferenceError, match="invalid-fields"):
        commit(inputs)
    assert not (inputs["state"] / "accepted.json").exists()


def test_cli_refusal_is_bounded_and_stdout_failure_reports_unknown_ack(inputs, monkeypatch, capsys):
    from agent_machines import cli_preferences

    args = [
        "preferences", "carrier", "publish", "--state-dir", str(inputs["state"]),
        "--input", str(inputs["source"]),
    ]
    assert main(args) == 0
    token = json.loads(capsys.readouterr().out)["preview_token"]
    real_print = print

    def failed_print(*values, **kwargs):
        if kwargs.get("file") is None:
            raise BrokenPipeError("private output detail")
        real_print(*values, **kwargs)

    monkeypatch.setattr(cli_preferences, "print", failed_print, raising=False)
    assert main([*args, "--apply", "--expect", token]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["error"] == "response-output-unavailable" and error["applied"] is None
    assert codec.read(inputs["replica"] / carrier.DOCUMENT) == snapshot()
    assert error["configuration_applied"] is False and error["binding_changed"] is False
