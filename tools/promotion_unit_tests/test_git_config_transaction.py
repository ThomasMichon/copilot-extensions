"""No-actor contracts for declared Git configuration transaction admission."""

from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "plugins/agent-machines/src/agent_machines/git_config_transaction.py"


@pytest.fixture
def engine(monkeypatch):
    process = ModuleType("agent_procutil")
    process.no_window_kwargs = lambda: {}
    monkeypatch.setitem(sys.modules, "agent_procutil", process)
    spec = importlib.util.spec_from_file_location(
        "promotion_git_config_transaction", ROOT / SOURCE
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_declarations_preserve_order_and_refuse_ambiguous_or_credential_keys(engine):
    assert engine.validate_entries(
        [
            {
                "key": "Credential.https://Example.com.Helper",
                "values": ["", "!first", "!last"],
            },
            {"key": "User.Name", "state": "absent"},
        ]
    ) == {
        "credential.https://Example.com.helper": ["", "!first", "!last"],
        "user.name": None,
    }
    for invalid in (
        None,
        {},
        [],
        [None],
        [{"key": "no-section", "values": ["x"]}],
        [{"key": "user.name", "values": []}],
        [{"key": "user.name", "values": [False]}],
        [{"key": "user.name", "values": ["\0"]}],
        [{"key": "user.name", "state": "absent", "values": []}],
        [{"key": "http.extraHeader", "values": ["not-accepted"]}],
        [{"key": "user.name", "values": ["x"], "unknown": True}],
        [{"key": "User.Name", "values": ["x"]}, {"key": "user.name", "values": ["y"]}],
    ):
        with pytest.raises(ValueError):
            engine.validate_entries(invalid)


def test_plan_consent_binds_existence_identity_intent_target_and_bytes(engine):
    plan = engine.Plan(
        Path("/declared/.gitconfig"), None, b"[user]\nname = New\n", (), "{}"
    )
    variants = [
        replace(plan, before=b""),
        replace(plan, identity=(1, 2, 3, 4, 5)),
        replace(plan, intent='{"changed":"declaration"}'),
        replace(plan, target=Path("/other/.gitconfig")),
        replace(plan, after=None),
    ]
    assert all(item.plan_sha256 != plan.plan_sha256 for item in variants)
    assert len(plan.plan_sha256) == 64
    before = b"[unrelated]\nsecret = synthetic-retained\n[user]\nname = Old\n"
    after = before.replace(b"name = Old", b"name = New")
    parsed = engine._config_values(
        b"private.value\nsynthetic-retained\0user.name\nOld\0core.filemode\0"
        b"core.empty\n\0user.name\nOther\nLine\0"
    )
    assert parsed[b"core.filemode"] == [None] and parsed[b"core.empty"] == [b""]
    assert parsed[b"user.name"] == [b"Old", b"Other\nLine"]
    changes = tuple(engine._value_changes("user.name", parsed[b"user.name"], [b"New"]))
    displayed = replace(plan, before=before, after=after, changes=changes).summary()
    assert "synthetic-retained" not in json.dumps(displayed)
    assert '- user.name = "Old"' in displayed["diff"]
    assert '+ user.name = "New"' in displayed["diff"]
    rollback = replace(plan, before=before, after=after, intent="rollback:fixture").summary()
    assert "synthetic-retained" not in json.dumps(rollback)
    assert "Old" not in rollback["diff"] and "New" not in rollback["diff"]
    assert "<valueless>" in "".join(engine._value_changes("core.filemode", [None], [b""]))


def test_wrong_stale_and_read_only_plans_refuse_before_file_effects(
    engine, monkeypatch
):
    plan = engine.Plan(
        Path("/declared/.gitconfig"), b"old", b"new", (1, 2, 3, 4, 0o100600), "{}"
    )
    calls = []
    monkeypatch.setattr(engine, "_check_directory_chain", lambda _path: None)

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("file effect attempted")

    monkeypatch.setattr(engine, "os", SimpleNamespace(open=forbidden))
    for selected, snapshot, consent, reason in (
        (plan, (plan.before, plan.identity), "wrong", "consent"),
        (plan, (b"concurrent", plan.identity), plan.plan_sha256, "changed"),
        (
            replace(plan, identity=(1, 2, 3, 4, 0o100400)),
            (plan.before, (1, 2, 3, 4, 0o100400)),
            replace(plan, identity=(1, 2, 3, 4, 0o100400)).plan_sha256,
            "read-only",
        ),
    ):
        monkeypatch.setattr(engine, "_snapshot", lambda _path, value=snapshot: value)
        with pytest.raises(engine.TransactionError, match=reason):
            engine.apply(
                selected, expected_plan_sha256=consent, backup_root=Path("/backups")
            )
    assert calls == []


def test_replaced_or_reparse_lock_is_not_owned(engine):
    for mode, inode, attributes, expected in (
        (stat.S_IFREG, 2, 0, True),
        (stat.S_IFREG, 3, 0, False),
        (stat.S_IFLNK, 2, 0, False),
        (stat.S_IFREG, 2, 0x400, False),
    ):
        info = SimpleNamespace(
            st_mode=mode, st_dev=1, st_ino=inode, st_file_attributes=attributes
        )
        assert (
            engine._owns_lock(SimpleNamespace(lstat=lambda: info), (1, 2)) is expected
        )


def test_receipt_shape_backup_and_applied_state_are_all_required(engine, monkeypatch):
    target, receipt = (
        Path("/declared/.gitconfig").absolute(),
        Path("/backups/one/receipt.json"),
    )
    original, applied = b"original", b"applied"
    payload = {
        "schema": "agent-machines.git-config-receipt/v1",
        "target": str(target),
        "before_exists": True,
        "before_sha256": engine._sha(original),
        "after_exists": True,
        "after_sha256": engine._sha(applied),
        "plan_sha256": "1" * 64,
    }
    monkeypatch.setattr(engine, "_check_directory_chain", lambda _path: None)
    for changed in (
        {"schema": "unknown"},
        {"target": "other"},
        {"before_exists": 1},
        {"before_sha256": "bad"},
        {"after_sha256": "2" * 64},
        {"before_sha256": "3" * 64},
        {"unexpected": True},
    ):
        raw = json.dumps({**payload, **changed}).encode()
        snapshots = {
            receipt: (raw, ()),
            receipt.parent / "before": (original, ()),
            target: (applied, ()),
        }
        monkeypatch.setattr(
            engine, "_snapshot", lambda path, data=snapshots: data[path]
        )
        with pytest.raises(engine.TransactionError):
            engine.rollback_preview(target, receipt)
    for raw in (
        json.dumps(list(payload.items())).encode(),
        json.dumps(payload).encode()[:-1] + b',"schema":"duplicate"}',
    ):
        monkeypatch.setattr(engine, "_snapshot", lambda _path, data=raw: (data, ()))
        with pytest.raises(engine.TransactionError, match="receipt"):
            engine.rollback_preview(target, receipt)


def test_git_adapter_uses_only_the_selected_file_and_remaining_budget(
    engine, monkeypatch
):
    calls = []
    monkeypatch.setattr(engine.shutil, "which", lambda _name: "selected-git")
    monkeypatch.setattr(engine.time, "monotonic", lambda: 10)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/not-selected")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "redirected.key")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"selected", stderr=b"")

    monkeypatch.setattr(engine.subprocess, "run", run)
    target = Path("/declared/candidate")
    assert engine._git(target, "--null", "--list", deadline=12) == b"selected"
    argv, options = calls[0]
    assert argv == [
        "selected-git",
        "config",
        "--file",
        str(target),
        "--no-includes",
        "--null",
        "--list",
    ]
    assert options["timeout"] == 2 and options["cwd"] == target.parent
    assert not any(key.startswith("GIT_CONFIG") for key in options["env"])
    assert options["env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_git_errors_and_expired_budget_do_not_expose_output_or_retry(
    engine, monkeypatch
):
    monkeypatch.setattr(engine.shutil, "which", lambda _name: "selected-git")
    monkeypatch.setattr(engine.time, "monotonic", lambda: 10)
    calls = []
    for outcome in (
        SimpleNamespace(
            returncode=1, stdout=b"sensitive-output", stderr=b"sensitive-error"
        ),
        subprocess.TimeoutExpired("selected-git", 1, output="sensitive-output"),
        OSError("sensitive-error"),
    ):

        def run(*_args, value=outcome, **_kwargs):
            calls.append(True)
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(engine.subprocess, "run", run)
        with pytest.raises(engine.TransactionError) as error:
            engine._git(Path("/declared/candidate"), "--list", deadline=12)
        assert "sensitive-" not in str(error.value)
    assert len(calls) == 3
    with pytest.raises(engine.TransactionError, match="deadline"):
        engine._git(Path("/declared/candidate"), "--list", deadline=9)
    assert len(calls) == 3


def test_real_file_transaction_and_rollback_use_only_the_audited_temp_scope(
    engine, tmp_path, monkeypatch
):
    target = tmp_path / ".gitconfig"
    original = b"# preserve bytes\r\n[user]\r\n\tname = Before\r\n"
    proposed = b"# preserve bytes\r\n[user]\r\n\tname = After\r\n"
    target.write_bytes(original)
    before, identity = engine._snapshot(target)
    plan = engine.Plan(target, before, proposed, identity, '{"user.name":["After"]}')
    root = tmp_path / "new-parent" / "backups"
    synced = []
    sync_directory = engine._sync_directory

    def sync(path):
        synced.append(path)
        sync_directory(path)

    monkeypatch.setattr(engine, "_sync_directory", sync)
    with pytest.raises(engine.TransactionError, match="consent"):
        engine.apply(
            plan, expected_plan_sha256="wrong", backup_root=root
        )
    assert target.read_bytes() == original
    receipt = engine.apply(
        plan, expected_plan_sha256=plan.plan_sha256, backup_root=root
    )
    assert synced[1:4] == [root, root.parent, tmp_path]
    assert target.read_bytes() == proposed
    assert not target.with_name(target.name + ".lock").exists()
    rollback = engine.rollback_preview(target, receipt)
    assert rollback.plan_sha256 != plan.plan_sha256
    engine.apply(
        rollback,
        expected_plan_sha256=rollback.plan_sha256,
        backup_root=root,
    )
    assert target.read_bytes() == original


def test_cleanup_error_preserves_real_committed_bytes_and_receipt(
    engine, tmp_path, monkeypatch
):
    target = tmp_path / ".gitconfig"
    proposed = b"[user]\n\tname = After\n"
    before, identity = engine._snapshot(target)
    plan = engine.Plan(target, before, proposed, identity, '{"user.name":["After"]}')
    owns = engine._owns_lock
    lock = target.with_name(target.name + ".lock")
    consumed = []
    replace_file = engine.os.replace

    def replace_and_create_successor(source, destination):
        info = source.lstat()
        replace_file(source, destination)
        lock.write_bytes(b"successor writer")
        consumed.append((info.st_dev, info.st_ino))

    def same_identity(path, owner):
        if consumed:
            assert owner == consumed[0]
            return True
        return owns(path, owner)

    with monkeypatch.context() as patch:
        patch.setattr(engine.os, "replace", replace_and_create_successor)
        patch.setattr(engine, "_owns_lock", same_identity)
        receipt = engine.apply(
            plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups",
        )
    assert lock.read_bytes() == b"successor writer"
    lock.unlink()
    plan = engine.rollback_preview(target, receipt)

    def refuse_after_commit(path, owner):
        if not target.exists():
            raise PermissionError("synthetic cleanup observation failure")
        return owns(path, owner)

    monkeypatch.setattr(engine, "_owns_lock", refuse_after_commit)
    with pytest.raises(engine.TransactionError, match="cleanup") as error:
        engine.apply(
            plan,
            expected_plan_sha256=plan.plan_sha256,
            backup_root=tmp_path / "backups",
        )
    assert error.value.changed and error.value.receipt.is_file()
    assert not target.exists()


def test_lock_identity_failure_closes_the_owned_descriptor(
    engine, tmp_path, monkeypatch
):
    target = tmp_path / ".gitconfig"
    plan = engine.Plan(target, None, b"[user]\n\tname = After\n", (), "{}")
    opened = []
    original_open, original_fstat, original_close = (
        engine.os.open,
        engine.os.fstat,
        engine.os.close,
    )

    def open_lock(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        if Path(path).name == ".gitconfig.lock":
            opened.append(descriptor)
        return descriptor

    def refuse_identity(descriptor):
        if descriptor in opened:
            raise OSError("synthetic descriptor identity failure")
        return original_fstat(descriptor)

    monkeypatch.setattr(engine.os, "open", open_lock)
    monkeypatch.setattr(engine.os, "fstat", refuse_identity)
    try:
        with pytest.raises((engine.TransactionError, OSError)):
            engine.apply(
                plan,
                expected_plan_sha256=plan.plan_sha256,
                backup_root=tmp_path / "backups",
            )
        assert len(opened) == 1
        with pytest.raises(OSError):
            original_fstat(opened[0])
        assert not target.exists()
    finally:
        for descriptor in opened:
            try:
                original_close(descriptor)
            except OSError:
                pass
