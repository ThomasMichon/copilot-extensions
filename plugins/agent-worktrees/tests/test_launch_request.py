"""Remote requests reuse target seed authority and stable creation identity."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import launch_request as request, launch_seed_state, tracking, tracking_write
from agent_worktrees import remote_seed_launch


def _intent(kind="resume", text="line one\n'\";$() \u2603"):
    return dict(version=1, request_id="a" * 32, kind=kind,
                worktree_id="wt-a" if kind == "resume" else None, text=text, no_mux=False)


def _stub_create_worktree_core_internals(monkeypatch, tmp_path, config):
    from test_worktree_creation_seed import _stub_create_worktree_core_internals as stub
    from agent_worktrees import worktree_creation

    stub(monkeypatch, tmp_path, config)
    monkeypatch.setattr(
        worktree_creation.git_ops, "create_worktree",
        lambda _anchor, path, *_args: Path(path).mkdir(),
    )


@pytest.fixture
def admission_config(tmp_path, monkeypatch):
    monkeypatch.setattr(request.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(request.cfg, "detect_platform", lambda: "windows")
    (tmp_path / "tree").mkdir()
    registry = tmp_path / ".agent-worktrees" / "machines.yaml"
    registry.parent.mkdir()
    registry.write_text("machines: {}\n", encoding="utf-8")
    for project in ("demo", "demo-repo"):
        directory = request.cfg.project_dir(project)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "config.yaml").write_text(json.dumps({
            "repo_name": project, "machine": "test",
            "repos": {project: {
                "anchor": str(tmp_path), "worktree_root": str(tmp_path / "trees"),
                "default_branch": "dev",
            }},
        }), encoding="utf-8")
    return request.cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="windows", repo_name="demo-repo",
        repos={"demo-repo": request.cfg.RepoConfig(
            anchor=str(tmp_path), worktree_root=str(tmp_path / "trees"), default_branch="dev",
        )},
    )


@pytest.fixture
def authority(admission_config, monkeypatch):
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    endpoint = tracking_write.rendezvous_fields(server)
    monkeypatch.setattr(
        launch_seed_state, "_dispatch",
        lambda verb, args, **kwargs: tracking_write.dispatch(
            verb, args, read_lock_data=lambda: endpoint, ensure_monitor=None, boot_wait_s=0,
            **kwargs,
        ),
    )
    try:
        yield admission_config
    finally:
        server.close()


def test_resume_admitted_once_via_real_daemon_then_finished_never_replays(tmp_path, authority):
    record = tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                       "demo", "test", "windows", tmp_path)
    intent = _intent()
    first = request.execute(authority, intent)
    second = request.execute(authority, intent)
    assert first == second
    seed = launch_seed_state.peek(record.yaml_path)
    assert seed.text == intent["text"] and seed.seed_id == intent["request_id"]
    assert request.inspect(intent["request_id"])["ready"]
    taken = launch_seed_state.take(record.yaml_path, seed_id=seed.seed_id)
    assert launch_seed_state.finish(record.yaml_path, taken)["finished"]
    with pytest.raises(ValueError, match="completed"):
        request.execute(authority, intent)
    assert launch_seed_state.peek(record.yaml_path) is None
    assert request.inspect(intent["request_id"])["state"] == "completed-or-superseded"


def test_resume_does_not_allocate_a_random_worktree_suffix(
    tmp_path: Path, authority: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "tree"), "demo", "test", "windows", tmp_path,
    )
    token_hex = request.secrets.token_hex

    def no_allocation(size: int) -> str:
        assert size != 2, "Resume attempted to allocate a new worktree suffix"
        return token_hex(size)

    monkeypatch.setattr(request.secrets, "token_hex", no_allocation)
    intent = _intent()
    first = request.execute(authority, intent)
    receipt = request._read_admission(tmp_path, intent["request_id"])
    assert receipt["suffix"] == intent["request_id"]
    assert first["worktree_id"] == intent["worktree_id"]
    assert request.execute(authority, intent) == first
    assert request._read_admission(tmp_path, intent["request_id"]) == receipt


@pytest.mark.parametrize("state", ["active", "unknown"])
def test_structured_resume_rejects_hosted_provider_via_real_daemon(
    tmp_path, authority, state,
):
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "tree"), "demo", "test", "windows", tmp_path,
    )
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp", state=state, binding_revision=1, blob={},
    )
    tracking.save_record(record, record.yaml_path)
    before = record.yaml_path.read_bytes()
    with pytest.raises(ValueError, match="native execution leg"):
        request.execute(authority, _intent())
    assert record.yaml_path.read_bytes() == before
    assert launch_seed_state.peek(record.yaml_path) is None


@pytest.mark.parametrize("kind,field,value", [
    ("resume", "worktree_id", "wt-b"),
    ("resume", "kind", "new"),
    ("new", "kind", "resume"),
])
@pytest.mark.parametrize("during_reload", [False, True])
def test_safe_but_mismatched_receipt_never_redirects_replay(
    tmp_path, admission_config, monkeypatch, kind, field, value, during_reload,
):
    import hashlib

    intent = _intent(kind)
    record = tracking.create_new_record("wt-b", "worktree/wt-b", str(tmp_path / "tree"),
                                       "demo", "test", "windows", tmp_path)
    if kind == "resume":
        tracking.create_new_record(
            "wt-a", "worktree/wt-a", str(tmp_path / "tree"), "demo", "test", "windows", tmp_path,
        )
    args = {
        "project": admission_config.repo_name,
        "tracking_dir": str(tmp_path), "request_id": intent["request_id"],
        "fingerprint": hashlib.sha256(
            json.dumps(intent, sort_keys=True, ensure_ascii=False).encode("utf-8"),
        ).hexdigest(),
        "kind": kind, "worktree_id": intent["worktree_id"] or "test-win-20261013-010000-" + "a" * 32,
        "timestamp": "20261013-010000",
    }
    receipt = request._apply_admit(args)
    receipt[field] = value
    path = request._admission_path(tmp_path, intent["request_id"])

    def damage():
        path.write_text(json.dumps(receipt), encoding="utf-8")

    if during_reload:
        def dispatch(verb, values):
            assert verb == "launch_request_admit"
            result = tracking_write.compute(tracking_write.KIND, {"verb": verb, "args": values})
            assert "error" not in result
            damage()
            return result

        monkeypatch.setattr(request, "_dispatch", dispatch)
        with pytest.raises(ValueError, match="different intent"):
            request.execute(admission_config, intent)
    else:
        damage()
        result = tracking_write.compute(
            tracking_write.KIND, {"verb": "launch_request_admit", "args": args},
        )
        assert "different intent" in result["error"]
    assert launch_seed_state.peek(record.yaml_path) is None
    assert path.read_text(encoding="utf-8") == json.dumps(receipt)


def test_resume_new_intent_can_replace_old_but_old_request_cannot_restore_it(tmp_path, authority):
    record = tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                       "demo", "test", "windows", tmp_path)
    first = _intent()
    request.execute(authority, first)
    replacement = {**first, "request_id": "b" * 32, "text": "replacement"}
    request.execute(authority, replacement)
    with pytest.raises(ValueError, match="superseded"):
        request.execute(authority, first)
    assert launch_seed_state.peek(record.yaml_path).text == "replacement"


@pytest.mark.parametrize("concurrent", [False, True])
def test_new_response_loss_reuses_allocated_worktree_not_new_creation(
    tmp_path, authority, monkeypatch, concurrent,
):
    from agent_worktrees import worktree_creation

    created = []
    def create(config, **kwargs):
        allocation = kwargs["admitted_allocation"]
        wid = allocation.worktree_id
        created.append(wid)
        Path(allocation.path).mkdir(parents=True)
        record = tracking.create_new_record(
            wid, allocation.branch, allocation.path, allocation.project,
            allocation.machine, allocation.platform, tmp_path,
        )
        launch_seed_state.stage(record.yaml_path, kind="new", text=kwargs["pending_seed"],
                                seed_id=kwargs["pending_seed_id"])
        return {"worktree": {"id": wid}}
    monkeypatch.setattr(worktree_creation, "_create_worktree_core", create)
    if concurrent:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: request.execute(authority, _intent("new")), range(2)))
        first, receipt = results
    else:
        first = request.execute(authority, _intent("new"))
        receipt = request.execute(authority, _intent("new"))
    assert first == receipt
    assert created == [receipt["worktree_id"]]
    assert launch_seed_state.peek(tmp_path / f"{created[0]}.yaml").kind == "new"


@pytest.mark.parametrize("machine,platform,prefix", [
    ("test", "windows", "test-win"),
    ("test-wsl", "wsl", "test-wsl"),
])
def test_new_admission_uses_real_creation_core_with_fixed_allocation(
    tmp_path, authority, monkeypatch, machine, platform, prefix,
):
    from test_worktree_creation_seed import _create_config

    config = replace(_create_config(tmp_path), machine=machine, platform=platform)
    monkeypatch.setattr(request.cfg, "detect_platform", lambda: platform)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    first = request.execute(config, _intent("new"))
    second = request.execute(config, _intent("new"))
    assert first == second
    records = tracking.list_records(tmp_path)
    assert len(records) == 1
    assert re.fullmatch(
        rf"{re.escape(prefix)}-\d{{8}}-\d{{6}}-[0-9a-f]{{4}}", records[0].worktree_id,
    )
    assert "a" * 32 not in records[0].worktree_id
    seed = launch_seed_state.peek(records[0].yaml_path)
    assert seed.seed_id == "a" * 32 and seed.kind == "new"
    launch_seed_state.remove(records[0].yaml_path, remove_record=True)
    with pytest.raises(ValueError, match="instead of recreating"):
        request.execute(config, _intent("new"))


def test_lost_admission_response_reuses_its_short_allocation(
    tmp_path: Path, authority: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_worktree_creation_seed import _create_config

    config = _create_config(tmp_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    dispatch = request._dispatch
    intent = _intent("new")

    def lose_response(verb: str, args: dict) -> dict:
        result = dispatch(verb, args)
        if verb == "launch_request_admit":
            raise OSError("admission response lost")
        return result

    monkeypatch.setattr(request, "_dispatch", lose_response)
    with pytest.raises(OSError, match="response lost"):
        request.execute(config, intent)
    admission = request._admission_path(tmp_path, intent["request_id"])
    original = json.loads(admission.read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{4}", original["suffix"])
    assert original["suffix"] != intent["request_id"]

    monkeypatch.setattr(request, "_dispatch", dispatch)
    result = request.execute(config, intent)
    assert result["worktree_id"] == original["worktree_id"]
    assert result["seed_id"] == intent["request_id"]
    assert len(tracking.list_records(tmp_path)) == 1


@pytest.mark.parametrize("already_created", [False, True])
def test_legacy_long_allocation_is_recovered_without_renaming(
    tmp_path: Path, authority: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    already_created: bool,
) -> None:
    import hashlib
    from test_worktree_creation_seed import _create_config

    config = replace(_create_config(tmp_path), machine="test-wsl", platform="wsl")
    monkeypatch.setattr(request.cfg, "detect_platform", lambda: "wsl")
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    intent = _intent("new")
    identity = "test-wsl-wsl-20261013-100000-" + intent["request_id"]
    request._apply_admit({
        "project": config.repo_name,
        "tracking_dir": str(tmp_path), "request_id": intent["request_id"],
        "fingerprint": hashlib.sha256(
            json.dumps(intent, sort_keys=True, ensure_ascii=False).encode("utf-8"),
        ).hexdigest(),
        "kind": "new", "worktree_id": identity, "timestamp": "20261013-100000",
    })
    if already_created:
        record = tracking.create_new_record(
            identity, f"worktree/{identity}", str(tmp_path / "tree"),
            config.repo_name, config.machine, config.platform, tmp_path,
        )
        receipt = request._read_admission(tmp_path, intent["request_id"])
        request._apply_creation_start({
            "tracking_dir": str(tmp_path), "request_id": intent["request_id"],
            "fingerprint": receipt["fingerprint"],
        })
        request._apply_creation_finish({
            "tracking_dir": str(tmp_path), "request_id": intent["request_id"],
            "fingerprint": receipt["fingerprint"],
        })
        launch_seed_state.stage(
            record.yaml_path, kind="new", text=intent["text"], seed_id=intent["request_id"],
        )

    result = request.execute(config, intent)
    assert result["worktree_id"] == identity
    assert len(tracking.list_records(tmp_path)) == 1
    assert launch_seed_state.peek(tmp_path / f"{identity}.yaml").seed_id == intent["request_id"]
    retained = request._read_admission(tmp_path, intent["request_id"])
    assert "allocation" not in retained and "record_identity" not in retained
    assert "creation_nonce" not in retained


def test_short_allocation_collision_does_not_adopt_an_existing_worktree(
    tmp_path: Path, authority: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_worktree_creation_seed import _create_config

    config = _create_config(tmp_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    first = request.execute(config, _intent("new"))
    allocation = request._read_admission(tmp_path, _intent()["request_id"])

    class FixedDateTime:
        @staticmethod
        def now() -> datetime:
            return datetime.strptime(allocation["timestamp"], "%Y%m%d-%H%M%S")

    monkeypatch.setattr(request, "datetime", FixedDateTime)
    token_hex = request.secrets.token_hex
    monkeypatch.setattr(
        request.secrets, "token_hex",
        lambda size: allocation["suffix"] if size == 2 else token_hex(size),
    )
    with pytest.raises(ValueError, match="creation is incomplete"):
        request.execute(config, {**_intent("new"), "request_id": "b" * 32, "text": "another task"})
    seed = launch_seed_state.peek(tmp_path / f"{first['worktree_id']}.yaml")
    assert seed.seed_id == _intent()["request_id"]
    assert seed.text == _intent()["text"]
    assert len(tracking.list_records(tmp_path)) == 1


@pytest.mark.parametrize("field", ["suffix", "timestamp", "worktree_id"])
def test_short_receipt_allocation_mismatch_refuses_before_record_or_seed_changes(
    tmp_path: Path, authority: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, field: str,
) -> None:
    from test_worktree_creation_seed import _create_config

    config = _create_config(tmp_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    intent = _intent("new")
    result = request.execute(config, intent)
    record_path = tmp_path / f"{result['worktree_id']}.yaml"
    record_before = record_path.read_bytes()
    seed_path = launch_seed_state.state_path(record_path)
    seed_before = seed_path.read_bytes()
    path = request._admission_path(tmp_path, intent["request_id"])
    receipt = request._read_admission(tmp_path, intent["request_id"])
    receipt[field] = {
        "suffix": "0000" if receipt["suffix"] != "0000" else "1111",
        "timestamp": "20000101-000000" if receipt["timestamp"] != "20000101-000000" else "20000102-000000",
        "worktree_id": "other-win-20000101-000000-abcd",
    }[field]
    path.write_text(json.dumps(receipt), encoding="utf-8")
    damaged = path.read_bytes()

    with pytest.raises(ValueError, match="Invalid stored launch admission allocation"):
        request.execute(config, intent)
    with pytest.raises(ValueError, match="Invalid stored launch admission allocation"):
        request.inspect(intent["request_id"])

    assert record_path.read_bytes() == record_before
    assert seed_path.read_bytes() == seed_before
    assert path.read_bytes() == damaged


def test_unaccepted_new_cannot_recreate_removed_worktree(tmp_path, authority, monkeypatch):
    from agent_worktrees import worktree_creation

    def interrupted(config, **kwargs):
        raise OSError("interrupted before record publication")
    monkeypatch.setattr(worktree_creation, "_create_worktree_core", interrupted)
    with pytest.raises(OSError):
        request.execute(authority, _intent("new"))
    with pytest.raises(ValueError, match="instead of recreating"):
        request.execute(authority, _intent("new"))


@pytest.mark.parametrize("changed", ["machine", "platform"])
def test_new_recovery_never_creates_a_different_persisted_allocation(
    tmp_path, authority, monkeypatch, changed,
):
    from agent_worktrees import worktree_creation

    dispatch = request._dispatch

    def lose_response(verb, args):
        result = dispatch(verb, args)
        if verb == "launch_request_admit":
            raise OSError("admission response lost before creation")
        return result

    def forbidden(*args, **kwargs):
        raise AssertionError("changed allocation reached creation side effects")

    monkeypatch.setattr(request, "_dispatch", lose_response)
    monkeypatch.setattr(worktree_creation, "_create_worktree_core", forbidden)
    intent = _intent("new")
    with pytest.raises(OSError, match="response lost"):
        request.execute(authority, intent)
    admission = request._admission_path(tmp_path, intent["request_id"])
    before = admission.read_bytes()
    monkeypatch.setattr(request, "_dispatch", dispatch)
    if changed == "machine":
        authority = replace(authority, machine="renamed")
    else:
        monkeypatch.setattr(request.cfg, "detect_platform", lambda: "wsl")
    with pytest.raises(ValueError, match="Stored New allocation"):
        request.execute(authority, intent)
    assert admission.read_bytes() == before
    assert not json.loads(before).get("creation_started")
    assert not tracking.list_records(tmp_path)
    assert not Path(authority.default_repo.worktree_root).exists()


@pytest.mark.parametrize("finished", [False, True])
def test_interrupted_replacement_seed_revision_fences_old_admission(tmp_path, authority, finished):
    record = tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                       "demo", "test", "windows", tmp_path)
    intent = _intent()
    fingerprint = __import__("hashlib").sha256(
        json.dumps(intent, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    request._dispatch("launch_request_admit", {
        "tracking_dir": str(tmp_path), "request_id": intent["request_id"],
        "fingerprint": fingerprint, "kind": "resume", "worktree_id": "wt-a",
        "timestamp": "20261010-120000",
    })
    launch_seed_state._write(record.yaml_path, launch_seed_state.LaunchSeed(
        "b" * 32, "resume", "replacement", 1,
    ))
    if finished:
        tracking._atomic_write(launch_seed_state.state_path(record.yaml_path), json.dumps({
            "version": 1, "finished": True, "seed_id": "b" * 32, "revision": 2,
        }))
    with pytest.raises(ValueError, match="changed after admission"):
        request.execute(authority, intent)
    if finished:
        assert launch_seed_state.peek(record.yaml_path) is None
    else:
        assert launch_seed_state.peek(record.yaml_path).text == "replacement"


def test_decode_round_trip_preserves_shell_metacharacters():
    intent = _intent()
    encoded = base64.b64encode(json.dumps(intent, ensure_ascii=False).encode()).decode()
    assert request.decode(encoded) == intent
    with pytest.raises(ValueError):
        request.decode(encoded + "!")


@pytest.mark.parametrize("worktree_id", ["wt-a", ""])
def test_new_protocol_request_rejects_an_existing_worktree_selector(worktree_id: str) -> None:
    intent = {**_intent("new"), "worktree_id": worktree_id}
    encoded = base64.b64encode(json.dumps(intent).encode("utf-8")).decode("ascii")
    with pytest.raises(ValueError, match="New launch request cannot name an existing worktree"):
        request.decode(encoded)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-32"])
def test_non_utf8_envelope_is_rejected_before_admission(encoding):
    encoded = base64.b64encode(json.dumps(_intent()).encode(encoding)).decode()
    with pytest.raises(UnicodeDecodeError):
        request.decode(encoded)


def test_invalid_utf8_returns_structured_cli_error():
    import argparse
    from agent_worktrees import output, resolve_cli

    parsers = argparse.ArgumentParser().add_subparsers()
    resolve_cli.add_parsers(parsers)
    encoded = base64.b64encode(b"\xff" + json.dumps(_intent()).encode()).decode()
    args = parsers.choices["resolve"].parse_args(["--json", "--launch-request-b64", encoded])
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(args) == 3
    assert "Launch request <invalid> failed" in json.loads(captured.getvalue())["error"]


def test_changed_request_cannot_reuse_an_admitted_identity(tmp_path, authority):
    tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                               "demo", "test", "windows", tmp_path)
    intent = _intent()
    request.execute(authority, intent)
    with pytest.raises(ValueError, match="different intent"):
        request.execute(authority, {**intent, "text": "changed"})


@pytest.mark.parametrize("kind", ["new", "resume"])
@pytest.mark.parametrize("handoff", [False, True])
def test_status_recovers_committed_seed_before_acceptance_receipt(
    tmp_path, authority, monkeypatch, kind, handoff,
):
    from test_worktree_creation_seed import _create_config

    config = _create_config(tmp_path)
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    if kind == "resume":
        tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                   config.repo_name, "test", "windows", tmp_path)
    intent = _intent(kind)
    admission = request._admission_path(tmp_path, intent["request_id"])
    write = tracking._atomic_write

    def interrupt_acceptance(path, text):
        if path == admission and json.loads(text).get("accepted"):
            raise OSError("interrupted after durable seed staging")
        return write(path, text)

    monkeypatch.setattr(tracking, "_atomic_write", interrupt_acceptance)
    with pytest.raises(ValueError, match="after durable seed staging"):
        request.execute(config, intent)
    receipt_before = admission.read_bytes()
    receipt = json.loads(receipt_before)
    assert not receipt.get("accepted")
    if kind == "new":
        assert receipt["creation_complete"]
    record = tmp_path / f"{receipt['worktree_id']}.yaml"
    seed = launch_seed_state.peek(record)
    assert seed.seed_id == intent["request_id"] and seed.kind == kind
    if handoff:
        launch_seed_state.take(record, seed_id=seed.seed_id)
    status = request.inspect(intent["request_id"])
    assert status["state"] == ("handoff-unconfirmed" if handoff else "staged")
    assert status["ready"] is not handoff
    assert admission.read_bytes() == receipt_before
    assert len(tracking.list_records(tmp_path)) == 1


def test_status_never_exposes_incomplete_new_creation_as_ready(tmp_path, authority):
    record = tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                       "demo", "test", "windows", tmp_path)
    intent = _intent("new")
    request._dispatch("launch_request_admit", {
        "project": authority.repo_name,
        "tracking_dir": str(tmp_path), "request_id": intent["request_id"],
        "fingerprint": "f" * 64, "kind": "new", "worktree_id": "wt-a",
        "timestamp": "20261010-120000",
    })
    launch_seed_state.stage(record.yaml_path, kind="new", text=intent["text"],
                            seed_id=intent["request_id"])
    status = request.inspect(intent["request_id"])
    assert not status["ready"] and status["state"] == "creation-incomplete"


def test_status_unstaged_resume_is_admission_incomplete(tmp_path, authority):
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "tree"), "demo", "test", "windows", tmp_path,
    )
    intent = _intent()
    request._dispatch("launch_request_admit", {
        "tracking_dir": str(tmp_path), "request_id": intent["request_id"],
        "fingerprint": "f" * 64, "kind": "resume", "worktree_id": "wt-a",
        "timestamp": "20261010-120000",
    })
    status = request.inspect(intent["request_id"])
    assert not status["ready"] and status["state"] == "admission-incomplete"


@pytest.mark.parametrize("verb", [
    "launch_request_admit", "launch_request_stage",
    "launch_request_creation_start", "launch_request_creation_finish",
])
@pytest.mark.parametrize("corruption", [
    "null", "[]", "{}", "missing-field", "bad-fingerprint",
    "bad-flags", "bad-worktree", "bad-suffix", "bad-short-allocation",
])
def test_corrupt_admission_is_structured_for_every_registered_handler(
    tmp_path, admission_config, verb, corruption,
):
    record = tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                                       "demo", "test", "windows", tmp_path)
    args = {
        "tracking_dir": str(tmp_path), "request_id": "a" * 32,
        "fingerprint": "f" * 64, "kind": "resume", "worktree_id": "wt-a",
        "timestamp": "20261010-120000", "text": "task",
    }
    tracking_write.compute(tracking_write.KIND, {"verb": "launch_request_admit", "args": args})
    admission = request._admission_path(tmp_path, args["request_id"])
    original = admission.read_text(encoding="utf-8")
    if corruption in ("null", "[]", "{}"):
        damaged = corruption
    else:
        receipt = json.loads(original)
        if corruption == "missing-field":
            del receipt["prior_revision"]
        elif corruption == "bad-fingerprint":
            receipt["fingerprint"] = None
        elif corruption == "bad-flags":
            receipt["accepted"] = "yes"
        elif corruption == "bad-suffix":
            receipt["suffix"] = "b" * 32
        elif corruption == "bad-short-allocation":
            receipt["suffix"] = "abcd"
        else:
            receipt["worktree_id"] = "../outside"
        damaged = json.dumps(receipt)
    admission.write_text(damaged, encoding="utf-8")
    error = tracking_write.compute(tracking_write.KIND, {"verb": verb, "args": args})
    assert "Invalid stored launch admission" in error["error"]
    with pytest.raises(ValueError, match="Invalid stored launch admission"):
        request.inspect(args["request_id"])
    assert launch_seed_state.peek(record.yaml_path) is None
    admission.write_text(original, encoding="utf-8")
    recovered = tracking_write.compute(
        tracking_write.KIND,
        {"verb": "launch_request_admit", "args": {**args, "timestamp": "20261010-120001"}},
    )
    assert recovered["seed_id"] == args["request_id"]
    assert recovered["timestamp"] == "20261010-120000"
    assert request.inspect(args["request_id"])["state"] == "admission-incomplete"


def test_corrupt_admission_returns_over_real_wire_and_daemon_remains_usable(tmp_path, authority):
    tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "tree"), "demo", "test", "windows", tmp_path,
    )
    args = {
        "tracking_dir": str(tmp_path), "request_id": "a" * 32,
        "fingerprint": "f" * 64, "kind": "resume", "worktree_id": "wt-a",
        "timestamp": "20261010-120000", "text": "task",
    }
    request._dispatch("launch_request_admit", args)
    request._admission_path(tmp_path, args["request_id"]).write_text("null", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid stored launch admission"):
        request._dispatch("launch_request_stage", args)
    tracking_write.register_verb(
        "test_launch_request_health", lambda args: {"healthy": True}, version=2,
    )
    assert request._dispatch("test_launch_request_health", {}) == {"healthy": True}


def test_structured_cli_uses_no_selector_and_returns_no_seed_text(tmp_path, authority, monkeypatch, capsys):
    import argparse
    from agent_worktrees import resolve_cli

    tracking.create_new_record("wt-a", "worktree/wt-a", str(tmp_path / "tree"),
                               "demo", "test", "windows", tmp_path)
    load_config = request.cfg.load_config
    monkeypatch.setattr(
        request.cfg, "load_config",
        lambda *a, **k: load_config(*a, **k) if k.get("project") else authority,
    )
    parsers = argparse.ArgumentParser().add_subparsers(dest="command")
    resolve_cli.add_parsers(parsers)
    encoded = base64.b64encode(json.dumps(_intent()).encode()).decode()
    args = parsers.choices["resolve"].parse_args(["--json", "--launch-request-b64", encoded])
    assert resolve_cli.cmd_resolve(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["seed_id"] == _intent()["request_id"]
    assert "text" not in receipt


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_remote_admission_sends_text_only_in_encoded_request(shell, monkeypatch):
    monkeypatch.setattr(remote_seed_launch.cfg, "project_name", lambda: "demo")
    seen = []
    def run(argv, **kwargs):
        seen.append(argv)
        command = argv[-1]
        if shell == "pwsh":
            command = base64.b64decode(command.split()[-1]).decode("utf-16le")
            encoded = command.split("'")[-2]
        else:
            import shlex
            inner = shlex.split(command)[-1]
            encoded = shlex.split(inner)[-1]
        intent = request.decode(encoded)
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "version": 1, "request_id": intent["request_id"], "seed_id": intent["request_id"],
            "worktree_id": "wt-a", "seed_kind": "resume",
        }))
    monkeypatch.setattr(remote_seed_launch.subprocess, "run", run)
    text = _intent()["text"]
    plan = remote_seed_launch.prepare(None, "target", shell, ["--worktree-id", "wt-a"], text)
    assert text not in seen[0][-1]
    assert text not in plan["remote_command"]
    assert plan["seed_pending"] and plan["seed_kind"] == "resume"


def test_real_powershell_transport_preserves_encoded_intent(tmp_path):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    intent = _intent()
    intent["text"] += "x" * (
        request.MAX_REQUEST_BYTES - len(json.dumps(intent, ensure_ascii=False).encode())
    )
    encoded = base64.b64encode(json.dumps(intent, ensure_ascii=False).encode()).decode()
    # Reconfigure stdout to UTF-8: a piped child otherwise encodes with the
    # platform's preferred encoding (cp1252 on many Windows hosts), which
    # cannot represent the intent's non-ASCII text.
    script = (
        "import base64,json,sys;"
        "assert sys.flags.utf8_mode == 1;"
        "sys.stdout.reconfigure(encoding='utf-8');"
        "print(json.dumps(json.loads(base64.b64decode(sys.argv[1])),ensure_ascii=False))"
    )
    command = remote_seed_launch.shell_command(
        "pwsh", [sys.executable, "-X", "utf8", "-c", script, encoded],
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-EncodedCommand", command.split()[-1]],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
        env={**os.environ, "PYTHONUTF8": "0"},
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == intent


def test_real_posix_transport_preserves_encoded_intent():
    if sys.platform == "win32":
        pytest.skip("Native POSIX shell invocation is validated in the SSH lane")
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash is unavailable")
    intent = _intent()
    intent["text"] += "x" * (
        request.MAX_REQUEST_BYTES - len(json.dumps(intent, ensure_ascii=False).encode())
    )
    encoded = base64.b64encode(json.dumps(intent, ensure_ascii=False).encode()).decode()
    script = (
        "import base64,json,sys;"
        "print(json.dumps(json.loads(base64.b64decode(sys.argv[1])),ensure_ascii=False))"
    )
    command = remote_seed_launch.shell_command("bash", [sys.executable, "-c", script, encoded])
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == intent


def test_remote_old_engine_refuses_without_interactive_fallback(monkeypatch):
    monkeypatch.setattr(remote_seed_launch.cfg, "project_name", lambda: "demo")
    commands = []
    def refused(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(returncode=2, stdout="", stderr="unrecognized arguments: --launch-request-b64")
    monkeypatch.setattr(remote_seed_launch.subprocess, "run", refused)
    with pytest.raises(RuntimeError, match="unrecognized arguments"):
        remote_seed_launch.prepare(None, "target", "bash", ["--new"], "task")
    assert len(commands) == 1


def test_remote_error_envelope_wins_over_creation_progress(monkeypatch):
    monkeypatch.setattr(remote_seed_launch.cfg, "project_name", lambda: "demo")
    monkeypatch.setattr(remote_seed_launch.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=3, stdout=json.dumps({"error": "seed storage failed"}),
        stderr="Creating worktree...",
    ))
    with pytest.raises(RuntimeError, match="seed storage failed"):
        remote_seed_launch.prepare(None, "target", "bash", ["--new"], "task")


@pytest.mark.parametrize("shell", ["fish", "ksh", "unknown"])
def test_unsupported_shell_is_rejected_before_remote_admission(shell):
    with pytest.raises(ValueError, match="supported explicit shell"):
        remote_seed_launch.shell_command(shell, ["demo", "resolve"])


@pytest.mark.parametrize("selector", [["--new"], ["--base"], ["--worktree-id", "wt-a"],
                                      ["--codename", "one-two"]])
def test_structured_request_rejects_ordinary_selector_before_mutation(selector):
    import argparse
    from agent_worktrees import output, resolve_cli

    parsers = argparse.ArgumentParser().add_subparsers()
    resolve_cli.add_parsers(parsers)
    encoded = base64.b64encode(json.dumps(_intent()).encode()).decode()
    args = parsers.choices["resolve"].parse_args(
        ["--json", "--launch-request-b64", encoded, *selector],
    )
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(args) == 3
    assert "Structured launch requests require" in json.loads(captured.getvalue())["error"]


@pytest.mark.parametrize("mode", ["admission", "status"])
@pytest.mark.parametrize("modifier", [
    ["--bridge"], ["--profile", "demo"], ["--parent-session", "session"],
    ["--caller-worktree", "wt-caller"], ["--owner-ref", "host/demo/wt-caller"],
    ["--environment", "Linux"], ["--target-no-mux"], ["--stage-launch-seed"],
    ["--defer-new-seed"], ["--no-mux"], ["--no-fast-forward"], ["--no-resume"],
    ["--recovery"], ["--seed", "task"], ["--seed-id", "intent"],
    ["--machine", "target"], ["--dry-run"], ["--auto"], ["--bare-resume"],
    ["--restore"], ["copilot-positional-argument"],
    ["--profile", ""], ["--seed", ""], ["--owner-ref", ""],
])
def test_structured_request_rejects_all_nonprotocol_modifiers(
    mode, modifier, monkeypatch,
):
    import argparse
    from agent_worktrees import output, resolve_cli

    def forbidden(*args, **kwargs):
        raise AssertionError("nonprotocol modifier reached admission or status")

    monkeypatch.setattr(request, "execute", forbidden)
    monkeypatch.setattr(request, "inspect", forbidden)
    parsers = argparse.ArgumentParser().add_subparsers(dest="command")
    resolve_cli.add_parsers(parsers)
    encoded = base64.b64encode(json.dumps(_intent("new")).encode()).decode()
    protocol = (
        ["--launch-request-b64", encoded] if mode == "admission"
        else ["--launch-request-status", "a" * 32]
    )
    args = parsers.choices["resolve"].parse_args(["--json", *protocol, *modifier])
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(args) == 3
    assert "Structured launch requests require" in json.loads(captured.getvalue())["error"]


@pytest.mark.parametrize("selector", [[], ["--new"]])
@pytest.mark.parametrize("protocol", [
    ["--launch-request-b64", ""],
    ["--launch-request-status", ""],
    ["--launch-request-b64", "", "--launch-request-status", ""],
    ["--launch-request-b64", "valid", "--launch-request-status", ""],
    ["--launch-request-b64", "", "--launch-request-status", "a" * 32],
])
def test_empty_protocol_options_never_fall_through_to_ordinary_resolve(
    selector, protocol, monkeypatch,
):
    import argparse
    from agent_worktrees import output, resolve_cli

    def forbidden(*args, **kwargs):
        raise AssertionError("malformed protocol reached ordinary resolve or target state")

    monkeypatch.setattr(resolve_cli.ResolveCommandState, "from_args", forbidden)
    monkeypatch.setattr(request, "execute", forbidden)
    monkeypatch.setattr(request, "inspect", forbidden)
    parsers = argparse.ArgumentParser().add_subparsers()
    resolve_cli.add_parsers(parsers)
    encoded = base64.b64encode(json.dumps(_intent("new")).encode()).decode()
    protocol = [encoded if value == "valid" else value for value in protocol]
    args = parsers.choices["resolve"].parse_args(["--json", *selector, *protocol])
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(args) == 3
    assert "Launch request <invalid> failed" in json.loads(captured.getvalue())["error"]


def test_malformed_base64_returns_structured_cli_error():
    import argparse
    from agent_worktrees import output, resolve_cli

    parsers = argparse.ArgumentParser().add_subparsers()
    resolve_cli.add_parsers(parsers)
    args = parsers.choices["resolve"].parse_args(["--json", "--launch-request-b64", "!invalid!"])
    with output.capture_json_output() as captured:
        assert resolve_cli.cmd_resolve(args) == 3
    assert "Launch request <invalid> failed" in json.loads(captured.getvalue())["error"]


@pytest.mark.parametrize("selectors", [[], ["--codename", "one-two"], ["--worktree-id"],
                                       ["--new", "--worktree-id", "wt-a"]])
def test_remote_selector_refuses_before_admission(selectors):
    with pytest.raises(ValueError, match="exactly one"):
        remote_seed_launch.prepare(None, "target", "bash", selectors, "task")


@pytest.mark.parametrize("kind", ["new", "resume"])
@pytest.mark.parametrize("shell", ["bash", "powershell.exe"])
@pytest.mark.parametrize("extra", [0, 1])
def test_envelope_boundary_fits_both_shells_before_admission(kind, shell, extra, monkeypatch):
    monkeypatch.setattr(remote_seed_launch.cfg, "project_name", lambda: "p" * 64)
    monkeypatch.setattr(remote_seed_launch.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    intent = {**_intent(kind, ""), "no_mux": True}
    intent["text"] = "x" * (
        request.MAX_REQUEST_BYTES - len(json.dumps(intent, ensure_ascii=False).encode()) + extra
    )
    encoded = base64.b64encode(json.dumps(intent, ensure_ascii=False).encode()).decode()
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert len(argv[-1]) <= remote_seed_launch.MAX_REMOTE_COMMAND_CHARS
        return SimpleNamespace(returncode=0, stdout=json.dumps({
            "version": 1, "request_id": intent["request_id"], "seed_id": intent["request_id"],
            "worktree_id": intent["worktree_id"] or "wt-new", "seed_kind": kind,
        }))

    monkeypatch.setattr(remote_seed_launch.subprocess, "run", run)
    args = ["--new"] if kind == "new" else ["--worktree-id", "wt-a"]
    if extra:
        with pytest.raises(ValueError, match="transport limit"):
            request.decode(encoded)
        with pytest.raises(ValueError, match="transport limit"):
            remote_seed_launch.prepare(None, "target", shell, [*args, "--no-mux"], intent["text"])
        assert not calls
    else:
        assert request.decode(encoded) == intent
        plan = remote_seed_launch.prepare(None, "target", shell, [*args, "--no-mux"], intent["text"])
        assert len(calls) == 1
        assert plan["seed_kind"] == kind


@pytest.mark.parametrize("kind", ["new", "resume"])
@pytest.mark.parametrize("receipt", [
    "not-json", "null", "[]", "{}", '{"request_id": "wrong"}',
    json.dumps(dict(version=1, request_id="a" * 32, seed_id="a" * 32,
                    seed_kind="resume", worktree_id="another-worktree")),
])
def test_uncertain_receipt_requires_same_request_recovery(kind, receipt, monkeypatch):
    monkeypatch.setattr(remote_seed_launch.cfg, "project_name", lambda: "demo")
    monkeypatch.setattr(remote_seed_launch.uuid, "uuid4", lambda: SimpleNamespace(hex="a" * 32))
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=receipt)

    monkeypatch.setattr(remote_seed_launch.subprocess, "run", run)
    args = ["--new"] if kind == "new" else ["--worktree-id", "wt-a"]
    with pytest.raises(RuntimeError) as exc:
        remote_seed_launch.prepare(None, "target", "bash", args, "task")
    message = str(exc.value)
    assert "unconfirmed on target" in message
    assert f"Do not resubmit this {kind} prompt" in message
    assert f"demo resolve --json --launch-request-status {'a' * 32} on the target" in message
    assert len(calls) == 1
