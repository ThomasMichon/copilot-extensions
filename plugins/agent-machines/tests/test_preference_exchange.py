from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_machines import preference_exchange as exchange
from agent_machines import preferences as codec
from agent_machines.__main__ import main


def snapshot(revision=1, parent=None, **changes):
    return {
        "schema": codec.SCHEMA, "version": 1, "publisher": "example-publisher",
        "revision": revision, "parent": parent,
        "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-first", "knowledge_repository_id": "repo-shared"},
            {"harness_repository_id": "repo-second", "knowledge_repository_id": "repo-shared"},
        ]}}, **changes,
    }


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def inputs(tmp_path):
    state = tmp_path / "local-state"
    state.mkdir()
    write(state / "policy.json", {
        "schema": "agent-machines.preferences.policy", "version": 1,
        "publisher": "example-publisher", "participant": "example-publisher",
        "domains": ["repositories", "copilot", "maintenance", "telemetry"],
    })
    source = write(tmp_path / "candidate.json", snapshot())
    return {"state": state, "source": source, "machine": "example-worker", "harness": "repo-first"}


def accept(inputs, **changes):
    return exchange.exchange("accept", **{**inputs, **changes})


def apply(inputs, **changes):
    plan = accept(inputs, **changes)
    return accept(inputs, apply=True, expected=plan["preview_token"], **changes)


def publish(inputs, output, **changes):
    args = {"state": inputs["state"], "source": inputs["source"], "output": output}
    plan = exchange.exchange("publish", **args, **changes)
    return exchange.exchange("publish", **args, **changes, apply=True, expected=plan["preview_token"])


@pytest.mark.parametrize(("harness", "local_target", "status", "target", "origin"), [
    ("repo-first", "repo-local", "suggested", "repo-local", "local-machine"),
    ("repo-first", None, "suppressed", None, "local-machine"),
    ("repo-second", "repo-local", "suggested", "repo-shared", "shared"),
    ("repo-missing", "repo-local", "absent", None, None),
])
def test_accepted_repository_projection_is_scoped_read_only_and_preserves_local_precedence(
    inputs, harness, local_target, status, target, origin,
):
    from agent_machines.preference_repositories import project

    state = inputs["state"]
    write(state / "accepted.json", snapshot())
    local = write(state.parent / "local.json", {
        "schema": codec.LOCAL_SCHEMA, "version": 1, "machine": inputs["machine"],
        "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-first", "knowledge_repository_id": local_target},
        ]}},
    })
    before = {path.name: path.read_bytes() for path in state.iterdir()}
    result = project(state, machine=inputs["machine"], harness=harness, local=local)
    assert result["knowledge_preference"] == {"status": status, "knowledge_repository_id": target}
    assert result["accepted_revision"] == 1 and result["provenance"] == origin
    assert result["read_only"] and not result["configuration_applied"] and not result["binding_changed"]
    assert {path.name: path.read_bytes() for path in state.iterdir()} == before
    assert sorted(before) == ["accepted.json", "policy.json"]
    original = result["input_revision"]
    local.write_bytes(local.read_bytes() + b"\n")
    assert project(state, machine=inputs["machine"], harness=harness, local=local)["input_revision"] != original


def test_accepted_repository_projection_handles_withdrawal_absence_and_narrowed_policy(inputs):
    from agent_machines.preference_repositories import project

    args = {"machine": inputs["machine"], "harness": inputs["harness"]}
    state = inputs["state"]
    assert project(state, **args)["knowledge_preference"]["status"] == "absent"
    write(state / "accepted.json", snapshot(2, 1, domains={}))
    assert project(state, **args)["accepted_revision"] == 2
    assert project(state, **args)["knowledge_preference"]["status"] == "absent"
    write(state / "accepted.json", snapshot())
    policy_path = state / "policy.json"
    policy = json.loads(policy_path.read_bytes())
    write(policy_path, {**policy, "publisher": "another-publisher"})
    with pytest.raises(codec.PreferenceError, match="accepted-publisher-mismatch"):
        project(state, **args)
    write(policy_path, {**policy, "domains": ["copilot"]})
    with pytest.raises(codec.PreferenceError, match="domain-not-permitted"):
        project(state, **args)


def test_preview_is_read_only_acceptance_is_complete_atomic_and_duplicate_is_idempotent(inputs):
    state = inputs["state"]
    before = {path.name: path.read_bytes() for path in state.iterdir()}
    plan = accept(inputs)
    assert plan["eligible"] and not plan["applied"]
    assert {path.name: path.read_bytes() for path in state.iterdir()} == before
    result = accept(inputs, apply=True, expected=plan["preview_token"])
    assert result["applied"] and not result["configuration_applied"] and not result["binding_changed"]
    assert codec.read(state / "accepted.json") == snapshot()
    before = (state / "accepted.json").stat().st_mtime_ns
    assert apply(inputs)["status"] == "unchanged"
    assert (state / "accepted.json").stat().st_mtime_ns == before
    assert sorted(path.name for path in state.iterdir()) == ["accepted.json", "exchange.lock", "policy.json"]


def test_accept_withdrawal_and_offline_keep_local_override_and_never_rollback(inputs):
    apply(inputs)
    local = write(inputs["source"].parent / "overrides.json", {
        "schema": codec.LOCAL_SCHEMA, "version": 1, "machine": "example-worker",
        "domains": {"copilot": {"model": "local-model"}},
    })
    write(inputs["source"], snapshot(2, 1, domains={}))
    withdrawn = apply(inputs, local=local)
    assert withdrawn["projection"]["values"] == {"copilot": {"model": "local-model"}}
    inputs["source"].unlink()
    offline = accept(inputs, local=local)
    assert offline["status"] == "unavailable"
    assert offline["projection"]["projected_revision"] == 2
    assert offline["projection"]["values"] == {"copilot": {"model": "local-model"}}
    unavailable_folder = accept(
        inputs, source=inputs["source"].parent / "unavailable-carrier" / "snapshot.json", local=local,
    )
    assert unavailable_folder["status"] == "unavailable"
    assert unavailable_folder["projection"]["projected_revision"] == 2
    write(inputs["source"], snapshot())
    assert accept(inputs, full_snapshot=True)["status"] == "older-revision"
    assert codec.read(inputs["state"] / "accepted.json")["domains"] == {}


@pytest.mark.parametrize(("candidate", "flags", "reason"), [
    (snapshot(domains={}), {}, "revision-conflict"),
    (snapshot(2, 1, publisher="other"), {}, "publisher-mismatch"),
    (snapshot(2, 1), {"conflict": True}, "carrier-conflict"),
    (snapshot(4, 3), {}, "parent-gap"),
])
def test_full_snapshot_never_bypasses_conflicts_or_publisher_and_missing_parent_is_explicit(
    inputs, candidate, flags, reason,
):
    apply(inputs)
    write(inputs["source"], candidate)
    plan = accept(inputs, **flags)
    assert plan["status"] == reason and not plan["eligible"]
    with pytest.raises(codec.PreferenceError, match=reason):
        accept(inputs, **flags, apply=True, expected=plan["preview_token"])
    if reason == "parent-gap":
        reconciled = apply(inputs, full_snapshot=True)
        assert reconciled["status"] == "full-snapshot"
        assert reconciled["projection"]["projected_revision"] == 4
        assert reconciled["projection"]["values"] == candidate["domains"]
        assert codec.read(inputs["state"] / "accepted.json") == candidate
    else:
        assert not accept(inputs, **flags, full_snapshot=True)["eligible"]
        assert codec.read(inputs["state"] / "accepted.json") == snapshot()


@pytest.mark.parametrize("change", ["candidate", "accepted", "policy", "machine", "harness", "local", "flags"])
def test_apply_rechecks_every_preview_input(inputs, change):
    local = write(inputs["source"].parent / "local.json", {
        "schema": codec.LOCAL_SCHEMA, "version": 1, "machine": "example-worker", "domains": {},
    })
    inputs["local"] = local
    token = accept(inputs)["preview_token"]
    if change == "candidate":
        write(inputs["source"], snapshot(domains={}))
    elif change == "accepted":
        write(inputs["state"] / "accepted.json", snapshot())
    elif change == "policy":
        policy = json.loads((inputs["state"] / "policy.json").read_text())
        policy["participant"] = "receiver"
        write(inputs["state"] / "policy.json", policy)
    elif change == "local":
        value = json.loads(local.read_text())
        value["domains"] = {"copilot": {"model": "changed"}}
        write(local, value)
    elif change == "flags":
        inputs["full_snapshot"] = True
    else:
        inputs[change] = "other"
        if change == "machine":
            value = json.loads(local.read_text())
            value["machine"] = "other"
            write(local, value)
    with pytest.raises(codec.PreferenceError, match="preview-changed"):
        accept(inputs, apply=True, expected=token)


def test_policy_domain_and_participant_admission_is_not_native_authority(inputs, tmp_path):
    policy_path = inputs["state"] / "policy.json"
    policy = json.loads(policy_path.read_text())
    policy["domains"] = ["copilot"]
    write(policy_path, policy)
    assert accept(inputs)["status"] == "domain-not-permitted"
    policy["domains"] = ["repositories"]
    policy["participant"] = "receiver"
    write(policy_path, policy)
    assert accept(inputs)["eligible"]
    output = tmp_path / "output.json"
    plan = exchange.exchange("publish", state=inputs["state"], source=inputs["source"], output=output)
    assert plan["status"] == "not-publisher"
    with pytest.raises(codec.PreferenceError, match="not-publisher"):
        publish(inputs, output)
    assert not output.exists()
    assert not (inputs["state"] / "publication.json").exists()


def test_publication_is_separate_from_acceptance_and_repairs_missing_output_without_rollback(inputs, tmp_path):
    output = tmp_path / "published.json"
    result = publish(inputs, output)
    assert result["local_published"] and result["remote_receipt"] == "not-observed"
    assert codec.read(output) == snapshot()
    assert not (inputs["state"] / "accepted.json").exists()
    write(inputs["source"], snapshot(2, 1, domains={}))
    publish(inputs, output)
    assert codec.read(output)["domains"] == {}
    output.unlink()
    write(inputs["source"], snapshot())
    with pytest.raises(codec.PreferenceError, match="older-revision"):
        publish(inputs, output)
    assert not output.exists()
    write(inputs["source"], snapshot(2, 1, domains={}))
    publish(inputs, output)
    assert codec.read(output) == codec.read(inputs["state"] / "publication.json")


def test_failed_output_replacement_retains_intent_and_allows_exact_retry(inputs, tmp_path, monkeypatch):
    output = tmp_path / "published.json"
    replace = exchange.os.replace

    def fail(source, target):
        if Path(target) == output:
            raise OSError("private diagnostic must not escape")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(exchange.os, "replace", fail)
        with pytest.raises(codec.PreferenceError, match="publication-pending"):
            publish(inputs, output)
    assert not output.exists()
    assert codec.read(inputs["state"] / "publication.json") == snapshot()
    assert not list(tmp_path.glob("*.tmp.*"))
    assert publish(inputs, output)["local_published"]


@pytest.mark.parametrize("operation", ["accept", "publish"])
def test_state_directory_is_synced_before_output_or_success_including_unchanged_retries(
    inputs, tmp_path, monkeypatch, operation,
):
    output = tmp_path / "published.json"
    events = []
    original_write = exchange._write

    def synced(path):
        assert path == inputs["state"]
        watermark = "accepted.json" if operation == "accept" else "publication.json"
        assert codec.read(path / watermark) == snapshot()
        events.append("sync")

    def written(path, value):
        if path == output:
            assert events[-1] == "sync"
            events.append("output")
        return original_write(path, value)

    monkeypatch.setattr(exchange, "_sync_directory", synced)
    monkeypatch.setattr(exchange, "_write", written)
    for _ in range(2):
        result = apply(inputs) if operation == "accept" else publish(inputs, output)
        assert result["applied"]
        assert events[-1] == ("sync" if operation == "accept" else "output")
    assert events == (["sync", "sync"] if operation == "accept" else ["sync", "output"] * 2)


def test_failed_directory_sync_never_publishes_and_identical_intent_can_retry(inputs, tmp_path, monkeypatch):
    output = tmp_path / "published.json"

    def fail(_path):
        raise OSError("directory sync failed")

    with monkeypatch.context() as patch:
        patch.setattr(exchange, "_sync_directory", fail)
        with pytest.raises(codec.PreferenceError, match="state-sync-pending"):
            publish(inputs, output)
    assert not output.exists()
    assert codec.read(inputs["state"] / "publication.json") == snapshot()
    assert publish(inputs, output)["local_published"]


def test_posix_directory_sync_closes_descriptor_even_when_fsync_fails(tmp_path, monkeypatch):
    from types import SimpleNamespace

    events = []

    def opened(path, flags):
        assert path == tmp_path and flags == 3
        events.append("open")
        return 42

    def sync(descriptor):
        assert descriptor == 42
        events.append("sync")
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(exchange, "os", SimpleNamespace(
        name="posix", O_RDONLY=1, O_DIRECTORY=2, open=opened, fsync=sync,
        close=lambda descriptor: events.append(("close", descriptor)),
    ))
    with pytest.raises(OSError):
        exchange._sync_directory(tmp_path)
    assert events == ["open", "sync", ("close", 42)]


@pytest.mark.parametrize(("current", "reason"), [
    (snapshot(publisher="other"), "output-publisher-mismatch"),
    (snapshot(domains={}), "output-revision-conflict"),
    (snapshot(3, 2), "newer-output"),
])
def test_publisher_does_not_overwrite_foreign_conflicting_or_newer_output(inputs, tmp_path, current, reason):
    output = tmp_path / "published.json"
    publish(inputs, output)
    write(output, current)
    with pytest.raises(codec.PreferenceError, match=reason):
        publish(inputs, output)
    assert codec.read(output) == current
    if reason == "output-revision-conflict":
        write(inputs["source"], snapshot(2, 1))
        with pytest.raises(codec.PreferenceError, match=reason):
            publish(inputs, output)


def test_publication_apply_refuses_changed_output_and_overlapping_files(inputs, tmp_path):
    output = tmp_path / "published.json"
    args = {"state": inputs["state"], "source": inputs["source"], "output": output}
    plan = exchange.exchange("publish", **args)
    write(output, snapshot())
    with pytest.raises(codec.PreferenceError, match="preview-changed"):
        exchange.exchange("publish", **args, apply=True, expected=plan["preview_token"])
    with pytest.raises(codec.PreferenceError, match="overlapping-paths"):
        exchange.exchange("publish", **{**args, "output": inputs["source"]})
    with pytest.raises(codec.PreferenceError, match="overlapping-paths"):
        accept(inputs, source=inputs["state"] / "accepted.json")


def test_hardlink_aliases_cannot_turn_publication_into_a_local_state_overwrite(inputs, tmp_path):
    import os

    output = tmp_path / "alias.json"
    try:
        os.link(inputs["state"] / "policy.json", output)
    except OSError:
        pytest.skip("fixture filesystem cannot create hardlinks")
    with pytest.raises(codec.PreferenceError, match="overlapping-paths"):
        publish(inputs, output)


@pytest.mark.parametrize("selected", ["state", "output", "source", "local"])
def test_parent_traversal_is_rejected_before_path_normalization_or_locking(inputs, tmp_path, monkeypatch, selected):
    args = {"state": inputs["state"], "source": inputs["source"], "output": tmp_path / "output.json"}
    operation = "publish"
    if selected == "local":
        args.pop("output")
        args.update(machine=inputs["machine"], harness=inputs["harness"], local=tmp_path / "local.json")
        operation = "accept"
    path = args[selected]
    args[selected] = path.parent / "link" / ".." / path.name
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    def forbidden(*args, **kwargs):
        raise AssertionError("parent traversal reached normalization or a lock")

    monkeypatch.setattr(exchange, "mutation_lock", forbidden)
    original = exchange.os.path.abspath

    def normalize(path):
        if ".." in Path(path).parts:
            forbidden()
        return original(path)

    monkeypatch.setattr(exchange.os.path, "abspath", normalize)
    with pytest.raises(codec.PreferenceError, match="parent-traversal"):
        exchange.exchange(operation, **args, apply=True, expected="0" * 64)
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("selected", ["state", "output"])
def test_real_symlink_parent_traversal_never_redirects_publication_or_the_writer_lock(inputs, tmp_path, selected):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    leaf = foreign / "leaf"
    leaf.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(leaf, target_is_directory=True)
    except OSError:
        pytest.skip("fixture filesystem cannot create directory symlinks")
    target = write(foreign / "published.json", snapshot(domains={}))
    args = {"state": inputs["state"], "source": inputs["source"], "output": tmp_path / "published.json"}
    if selected == "state":
        args["state"] = link / ".." / inputs["state"].name
    else:
        args["output"] = link / ".." / "published.json"
    before = target.read_bytes()
    with pytest.raises(codec.PreferenceError, match="parent-traversal"):
        exchange.exchange("publish", **args, apply=True, expected="0" * 64)
    assert target.read_bytes() == before
    assert not (foreign / inputs["state"].name).exists()
    assert not (inputs["state"] / "exchange.lock").exists()
    assert not (inputs["state"] / "publication.json").exists()


def test_failed_acceptance_preserves_previous_complete_snapshot_and_cleans_temp(inputs, monkeypatch):
    apply(inputs)
    write(inputs["source"], snapshot(2, 1, domains={}))

    def fail(*args):
        raise OSError("replacement failed")

    monkeypatch.setattr(exchange.os, "replace", fail)
    with pytest.raises(OSError):
        apply(inputs)
    assert codec.read(inputs["state"] / "accepted.json") == snapshot()
    assert not list(inputs["state"].glob("*.tmp.*"))


def test_normal_cli_refuses_unconfirmed_apply_without_effects_or_unsanitized_errors(inputs, capsys, monkeypatch):
    from agent_machines import __main__ as cli
    from agent_machines import configuration

    def forbidden(*args, **kwargs):
        raise AssertionError("exchange invoked native discovery/configuration")

    monkeypatch.setattr(cli, "_collect_reconcile_packages", forbidden)
    monkeypatch.setattr(cli, "_resolve_machine_identity", forbidden)
    monkeypatch.setattr(configuration, "owner_report", forbidden)
    args = [
        "preferences", "accept", "--input", str(inputs["source"]),
        "--state-dir", str(inputs["state"]), "--machine", inputs["machine"], "--harness", inputs["harness"],
    ]
    assert main(args) == 0
    token = json.loads(capsys.readouterr().out)["preview_token"]
    assert main([*args, "--apply"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "preview-token-required"
    assert not (inputs["state"] / "accepted.json").exists()
    assert main([*args, "--apply", "--expect", token]) == 0
    assert json.loads(capsys.readouterr().out)["applied"]
    inputs["source"].write_text('{"private-example":"malformed"', encoding="utf-8")
    assert main(args) == 2
    assert json.loads(capsys.readouterr().err) == {"ok": False, "error": "invalid-json"}


@pytest.mark.parametrize("change", [
    {"version": True}, {"participant": "path/to/participant"}, {"domains": ["unknown"]},
    {"domains": ["copilot", "copilot"]}, {"domains": []}, {"command": "run"},
])
def test_local_policy_is_bounded_strict_non_executable_data(inputs, change):
    path = inputs["state"] / "policy.json"
    policy = json.loads(path.read_text())
    write(path, {**policy, **change})
    with pytest.raises(codec.PreferenceError):
        accept(inputs)
