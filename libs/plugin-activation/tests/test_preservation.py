"""Durable originals and witnessed restoration, never command replay."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NoReturn

import pytest

from plugin_activation import PluginStateError, activation_custody, preserve_activation_durably
from plugin_activation import preservation, state

IDENTITY = "agent-example@market"
PEER = "agent-peer@market"


def initialize(home: Path, value: bool = False) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.json").write_text(json.dumps({
        "installedPlugins": [{"name": IDENTITY, "enabled": value, "version": "old"}],
    }), encoding="utf-8")
    (home / "settings.json").write_text(json.dumps({
        "enabledPlugins": {IDENTITY: value}, "unrelated": "original",
    }), encoding="utf-8")


def install(home: Path) -> None:
    (home / "config.json").write_text(json.dumps({
        "installedPlugins": [
            {"name": IDENTITY, "enabled": True, "version": "new"},
            {"name": PEER, "enabled": True},
        ],
    }), encoding="utf-8")
    (home / "settings.json").write_text(json.dumps({
        "enabledPlugins": {IDENTITY: True, PEER: True}, "unrelated": "new",
    }), encoding="utf-8")


def journal(home: Path) -> Path:
    return home / "activation-preservation" / "transaction.json"


def assert_restored(home: Path, value: bool = False) -> None:
    assert state.inspect_plugin_state(IDENTITY, home)["userActivation"] == str(value).lower()
    config = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert config["installedPlugins"][0] == {
        "name": IDENTITY, "enabled": value, "version": "new",
    }
    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert settings["unrelated"] == "new"
    assert settings["enabledPlugins"][PEER] is True


@pytest.mark.parametrize("value", [False, True])
def test_original_is_persisted_before_command_and_verified_before_clear(
    tmp_path: Path, value: bool,
) -> None:
    initialize(tmp_path, value)
    with preserve_activation_durably(IDENTITY, tmp_path) as transaction:
        record = json.loads(journal(tmp_path).read_text(encoding="utf-8"))
        assert record["phase"] == "running" and record["witness"] is None
        assert record["snapshot"]["user_value"] is value
        assert set(record) == preservation._FIELDS
        install(tmp_path)
        transaction.confirm_quiescent()
        assert json.loads(journal(tmp_path).read_text())["phase"] == "quiescent"
    assert_restored(tmp_path, value)
    assert not journal(tmp_path).exists()


def test_absent_original_state_is_not_promoted(tmp_path: Path) -> None:
    with preserve_activation_durably(IDENTITY, tmp_path) as transaction:
        install(tmp_path)
        transaction.confirm_quiescent()
    settings = json.loads((tmp_path / "settings.json").read_text())
    assert IDENTITY not in settings["enabledPlugins"]
    assert settings["enabledPlugins"][PEER] is True
    assert state.inspect_plugin_state(IDENTITY, tmp_path)["inventoryEnabled"] is False


def test_update_custody_never_captures_a_new_activation_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize(tmp_path)

    def unexpected_capture(*args: object) -> NoReturn:
        pytest.fail("update-only custody must not capture a new install snapshot")

    monkeypatch.setattr(state, "capture", unexpected_capture)
    with activation_custody(tmp_path):
        assert not journal(tmp_path).exists()
    assert not journal(tmp_path).exists()


def test_read_only_fence_requires_no_lock_or_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin_activation import activation_preservation_pending

    initialize(tmp_path)
    assert activation_preservation_pending(tmp_path) is False
    with preserve_activation_durably(IDENTITY, tmp_path) as transaction:
        monkeypatch.setattr(
            preservation, "OperationLock",
            lambda *args, **kwargs: pytest.fail("read-only fence acquired a lock"),
        )
        assert activation_preservation_pending(tmp_path) is True
        transaction.confirm_not_launched()
        assert activation_preservation_pending(tmp_path) is True
    assert activation_preservation_pending(tmp_path) is False


def test_read_only_fence_clears_only_after_witnessed_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin_activation import activation_preservation_pending

    initialize(tmp_path)
    restore = state.restore

    def blocked_restore(*args: object) -> NoReturn:
        raise OSError("fixture restoration unavailable")

    monkeypatch.setattr(state, "restore", blocked_restore)
    with pytest.raises(OSError, match="restoration unavailable"):
        with preserve_activation_durably(IDENTITY, tmp_path) as transaction:
            install(tmp_path)
            transaction.confirm_posix_quiescent()
    assert activation_preservation_pending(tmp_path) is True
    monkeypatch.setattr(state, "restore", restore)
    with activation_custody(tmp_path):
        assert activation_preservation_pending(tmp_path) is False
        assert_restored(tmp_path)
    assert activation_preservation_pending(tmp_path) is False


def test_unproven_command_retains_original_and_blocks_any_identity(tmp_path: Path) -> None:
    initialize(tmp_path)
    with pytest.raises(PluginStateError, match="settlement is unproven"):
        with preserve_activation_durably(IDENTITY, tmp_path):
            install(tmp_path)
    original_record = journal(tmp_path).read_bytes()
    for identity in (IDENTITY, PEER):
        with pytest.raises(PluginStateError, match="settlement is unproven"):
            with preserve_activation_durably(identity, tmp_path):
                pytest.fail("an unproven writer must block a second command")
    assert journal(tmp_path).read_bytes() == original_record
    assert state.inspect_plugin_state(IDENTITY, tmp_path)["userActivation"] == "true"


def test_unproven_body_failure_retains_evidence_without_masking_the_cause(tmp_path: Path) -> None:
    initialize(tmp_path)
    error = OSError("original command or witness-publication failure")
    with pytest.raises(OSError) as raised:
        with preserve_activation_durably(IDENTITY, tmp_path):
            install(tmp_path)
            raise error
    assert raised.value is error
    record = json.loads(journal(tmp_path).read_text())
    assert record["phase"] == "running" and record["snapshot"]["user_value"] is False
    assert state.inspect_plugin_state(IDENTITY, tmp_path)["userActivation"] == "true"


@pytest.mark.parametrize("phase", ["running", "quiescent"])
@pytest.mark.parametrize("code,failures", [(32, 1), (33, 1), (32, 2), (5, 1)])
def test_publication_contention_retries_the_same_original_without_command_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, code: int, failures: int,
) -> None:
    initialize(tmp_path)
    write = state.write_json_object_atomic
    attempted: list[dict[str, Any]] = []
    commands: list[str] = []
    waits: list[float] = []

    def publish(path: Path, record: dict[str, Any], *args: str) -> None:
        if path == journal(tmp_path) and record["phase"] == phase:
            attempted.append(json.loads(json.dumps(record)))
            if len(attempted) <= failures:
                error = OSError("synthetic journal publication refusal")
                setattr(error, "winerror", code)
                raise error
        write(path, record, *args)

    monkeypatch.setattr(state, "write_json_object_atomic", publish)
    monkeypatch.setattr(preservation, "sleep", waits.append)
    recovered = code in (32, 33) and failures == 1

    def run() -> None:
        with preserve_activation_durably(IDENTITY, tmp_path) as transaction:
            commands.append("one install")
            install(tmp_path)
            transaction.confirm_quiescent()

    if recovered:
        run()
        assert_restored(tmp_path)
        assert not journal(tmp_path).exists()
    else:
        with pytest.raises(OSError):
            run()
        assert journal(tmp_path).exists() is (phase == "quiescent")
    assert len(commands) == (1 if recovered or phase == "quiescent" else 0)
    assert len(attempted) == (2 if code in (32, 33) else 1)
    assert all(record == attempted[0] for record in attempted)
    assert attempted[0]["snapshot"]["user_value"] is False
    assert waits == ([state.RESTORE_RETRY_DELAY_S] if code in (32, 33) else [])


@pytest.mark.parametrize("failure", ["restore", "verify", "clear"])
def test_failed_witnessed_restoration_reuses_original_before_next_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    initialize(tmp_path)
    with monkeypatch.context() as patch:
        if failure == "restore":
            patch.setattr(state, "restore", lambda *_: (_ for _ in ()).throw(OSError("denied")))
        elif failure == "verify":
            patch.setattr(state, "restore", lambda *_: None)
        else:
            original = Path.unlink

            def unlink(path: Path, missing_ok: bool = False) -> None:
                if path == journal(tmp_path):
                    raise PermissionError("journal clear denied")
                return original(path, missing_ok=missing_ok)

            patch.setattr(Path, "unlink", unlink)
        with pytest.raises((OSError, PluginStateError)):
            with preserve_activation_durably(IDENTITY, tmp_path) as transaction:
                install(tmp_path)
                transaction.confirm_quiescent()
    record = json.loads(journal(tmp_path).read_text())
    assert record["phase"] == "quiescent"
    assert record["snapshot"]["user_value"] is False
    with preserve_activation_durably(PEER, tmp_path) as transaction:
        assert_restored(tmp_path)
        assert json.loads(journal(tmp_path).read_text())["snapshot"]["identity"] == PEER
        transaction.confirm_not_launched()
    assert not journal(tmp_path).exists()


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("schema", "foreign"), ("home", "elsewhere"),
    ("transaction", "unknown"), ("phase", "unknown"), ("witness", "pid-gone"),
    ("snapshot", {}), ("snapshot", {"identity": IDENTITY}),
])
def test_invalid_or_foreign_transaction_never_mutates_state(
    tmp_path: Path, field: str, value: object,
) -> None:
    initialize(tmp_path)
    with pytest.raises(PluginStateError):
        with preserve_activation_durably(IDENTITY, tmp_path):
            pass
    record = json.loads(journal(tmp_path).read_text())
    record[field] = value
    journal(tmp_path).write_text(json.dumps(record), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.glob("*.json")}
    raw = journal(tmp_path).read_bytes()
    with pytest.raises(PluginStateError):
        with preserve_activation_durably(IDENTITY, tmp_path):
            pytest.fail("invalid transaction was admitted")
    assert raw == journal(tmp_path).read_bytes()
    assert before == {path.name: path.read_bytes() for path in tmp_path.glob("*.json")}


def test_record_drift_and_missing_inventory_are_not_settled(tmp_path: Path) -> None:
    initialize(tmp_path)
    with pytest.raises(PluginStateError, match="transaction changed"):
        with preserve_activation_durably(IDENTITY, tmp_path) as transaction:
            journal(tmp_path).unlink()
            transaction.confirm_quiescent()
    initialize(tmp_path)
    with pytest.raises(PluginStateError, match="inventory disappeared"):
        with preserve_activation_durably(IDENTITY, tmp_path) as transaction:
            (tmp_path / "config.json").write_text('{"installedPlugins":[]}', encoding="utf-8")
            transaction.confirm_quiescent()
    assert journal(tmp_path).exists()


def test_inventory_presence_is_not_confused_with_an_absent_activation_flag(tmp_path: Path) -> None:
    initialize(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({
        "installedPlugins": [{"name": IDENTITY, "version": "old"}],
    }), encoding="utf-8")
    with pytest.raises(PluginStateError, match="inventory disappeared"):
        with preserve_activation_durably(IDENTITY, tmp_path) as transaction:
            (tmp_path / "config.json").write_text('{"installedPlugins":[]}', encoding="utf-8")
            transaction.confirm_quiescent()
    record = json.loads(journal(tmp_path).read_text())
    assert record["snapshot"]["inventory_enabled"] is None
    assert record["snapshot"]["inventory_record_existed"] is True


def test_linked_journal_does_not_authorize_recovery(tmp_path: Path) -> None:
    initialize(tmp_path)
    area = tmp_path / "activation-preservation"
    area.mkdir()
    target = tmp_path / "foreign.json"
    target.write_text("foreign", encoding="utf-8")
    try:
        journal(tmp_path).symlink_to(target)
    except OSError as error:
        pytest.skip(f"native symlinks unavailable: {error}")
    with pytest.raises(PluginStateError, match="not plain"):
        with preserve_activation_durably(IDENTITY, tmp_path):
            pytest.fail("a foreign linked transaction was admitted")
    assert target.read_text() == "foreign"
