"""Git configuration transactions operate only on an isolated declared file."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest
from agent_procutil import no_window_kwargs

from agent_machines import git_config_transaction as transaction

HELPER = "credential.https://github.example.com.helper"


def git_values(path, key):
    result = subprocess.run(
        ["git", "config", "--file", str(path), "--no-includes", "--null", "--get-all", key],
        capture_output=True,
        check=False,
        timeout=5,
        **no_window_kwargs(),
    )
    assert result.returncode in (0, 1)
    return result.stdout.decode("utf-8").removesuffix("\0").split("\0") if result.stdout else []


@pytest.mark.parametrize("repetitions,terminated", [(0, False), (240, False), (240, True)])
def test_owned_preview_never_discloses_retained_unowned_lines(tmp_path, repetitions, terminated):
    target = tmp_path / ".gitconfig"
    sentinel = "retained-private-fixture"
    original = (
        "[user]\n\tname = Before\n"
        + "# repeated\n" * repetitions
        + "[private]\n\tvalue = " + sentinel + ("\n" if terminated else "")
    ).encode()
    target.write_bytes(original)
    plan = transaction.preview(target, {"user.name": ["After"], "fetch.prune": ["true"]})
    assert plan.changed
    assert sentinel not in plan.diff
    assert sentinel not in json.dumps(plan.summary())
    assert "Before" in plan.diff and "After" in plan.diff
    assert target.read_bytes() == original
    receipt = transaction.apply(
        plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
    )
    rollback = transaction.rollback_preview(target, receipt)
    assert sentinel not in json.dumps(rollback.summary())
    assert rollback.after == original


def test_valueless_boolean_is_not_an_explicit_empty_string(tmp_path):
    target = tmp_path / ".gitconfig"
    original = b"[core]\n\tfilemode\n"
    target.write_bytes(original)
    plan = transaction.preview(target, {"core.filemode": [""]})
    assert plan.changed
    transaction.apply(
        plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
    )
    observed = subprocess.run(
        ["git", "config", "--file", str(target), "--bool", "core.filemode"],
        capture_output=True, check=True, timeout=5, **no_window_kwargs(),
    )
    assert observed.stdout.strip() == b"false"
    assert not transaction.preview(target, {"core.filemode": [""]}).changed


def test_preview_apply_and_rollback_preserve_exact_original_bytes(tmp_path):
    target = tmp_path / ".gitconfig"
    original = b"# keep this spelling\r\n[user]\r\n\tname = Example\r\n\r\n"
    target.write_bytes(original)
    desired = {HELPER: ["", "!example-helper"]}

    plan = transaction.preview(target, desired)
    assert target.read_bytes() == original
    assert plan.changed and plan.diff
    assert plan.before_sha256 != plan.after_sha256
    with pytest.raises(transaction.TransactionError, match="consent"):
        transaction.apply(plan, expected_plan_sha256="wrong", backup_root=tmp_path / "backups")
    assert target.read_bytes() == original

    receipt = transaction.apply(
        plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
    )
    assert git_values(target, HELPER) == ["", "!example-helper"]
    assert b"# keep this spelling\r\n" in target.read_bytes()
    assert git_values(target, "user.name") == ["Example"]
    assert not transaction.preview(target, desired).changed

    rollback = transaction.rollback_preview(target, receipt)
    transaction.apply(
        rollback,
        expected_plan_sha256=rollback.plan_sha256,
        backup_root=tmp_path / "backups",
    )
    assert target.read_bytes() == original


def test_concurrent_edit_and_git_lock_are_not_overwritten(tmp_path):
    target = tmp_path / ".gitconfig"
    target.write_bytes(b"[user]\n\tname = First\n")
    plan = transaction.preview(target, {HELPER: ["", "!example-helper"]})
    external = b"[user]\n\tname = Concurrent\n"
    target.write_bytes(external)
    with pytest.raises(transaction.TransactionError, match="changed"):
        transaction.apply(
            plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
        )
    assert target.read_bytes() == external
    plan = transaction.preview(target, {HELPER: ["", "!example-helper"]})
    lock = target.with_name(target.name + ".lock")
    lock.write_bytes(b"another Git writer")
    with pytest.raises(transaction.TransactionError, match="locked"):
        transaction.apply(
            plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
        )
    assert target.read_bytes() == external
    assert lock.read_bytes() == b"another Git writer"


def test_rollback_restores_absence_and_preserves_intervening_edits(tmp_path):
    target = tmp_path / ".gitconfig"
    plan = transaction.preview(target, {HELPER: ["", "!example-helper"]})
    assert not target.exists()
    receipt = transaction.apply(
        plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
    )
    target.write_bytes(b"[user]\n\tname = Concurrent\n")
    with pytest.raises(transaction.TransactionError, match="changed"):
        transaction.rollback_preview(target, receipt)
    target.write_bytes(plan.after)
    rollback = transaction.rollback_preview(target, receipt)
    transaction.apply(
        rollback, expected_plan_sha256=rollback.plan_sha256, backup_root=tmp_path / "backups"
    )
    assert not target.exists()


def test_absent_key_and_include_are_not_confused_with_effective_values(tmp_path):
    target = tmp_path / ".gitconfig"
    included = tmp_path / "included.gitconfig"
    included.write_bytes(b"[user]\n\tname = Included\n")
    target.write_text(
        f'[include]\n\tpath = "{included.as_posix()}"\n[user]\n\tname = Local\n',
        encoding="utf-8",
    )
    included_before = included.read_bytes()
    plan = transaction.preview(target, {"user.name": None})
    transaction.apply(
        plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
    )
    assert git_values(target, "user.name") == []
    assert included.read_bytes() == included_before
    assert b"[include]" in target.read_bytes()


@pytest.mark.parametrize("original", [b"", b"[user]\n\tname = Before\n"])
def test_empty_and_existing_files_have_distinct_recoverable_snapshots(tmp_path, original):
    target = tmp_path / ".gitconfig"
    target.write_bytes(original)
    plan = transaction.preview(target, {"user.name": ["After"]})
    receipt = transaction.apply(
        plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
    )
    rollback = transaction.rollback_preview(target, receipt)
    transaction.apply(
        rollback, expected_plan_sha256=rollback.plan_sha256, backup_root=tmp_path / "backups"
    )
    assert target.exists() and target.read_bytes() == original


def test_a_replaced_lock_is_neither_committed_nor_removed(tmp_path, monkeypatch):
    target = tmp_path / ".gitconfig"
    target.write_bytes(b"[user]\n\tname = Before\n")
    plan = transaction.preview(target, {"user.name": ["After"]})
    lock = target.with_name(target.name + ".lock")
    backup = transaction._backup

    def replace_lock(*args):
        receipt = backup(*args)
        lock.rename(tmp_path / "displaced-lock")
        lock.write_bytes(b"another writer")
        return receipt

    monkeypatch.setattr(transaction, "_backup", replace_lock)
    with pytest.raises(transaction.TransactionError, match="ownership"):
        transaction.apply(
            plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
        )
    assert target.read_bytes() == plan.before
    assert lock.read_bytes() == b"another writer"


@pytest.mark.parametrize("verification_failure", [False, True])
def test_consumed_lock_never_deletes_same_identity_successor(tmp_path, monkeypatch, verification_failure):
    target = tmp_path / ".gitconfig"
    target.write_bytes(b"[user]\n\tname = Before\n")
    plan = transaction.preview(target, {"user.name": ["After"]})
    lock = target.with_name(target.name + ".lock")
    replace = os.replace
    lstat = Path.lstat
    original = None

    def replace_and_reuse(source, destination):
        nonlocal original
        info = lstat(source)
        replace(source, destination)
        lock.write_bytes(b"successor writer")
        original = info

    def same_identity(path, *args, **kwargs):
        info = lstat(path, *args, **kwargs)
        if path == lock and original is not None:
            return SimpleNamespace(
                st_mode=info.st_mode, st_ino=original.st_ino, st_dev=original.st_dev,
                st_file_attributes=getattr(info, "st_file_attributes", 0),
            )
        return info

    def sync(_path):
        if verification_failure and original is not None:
            raise OSError("synthetic postcommit sync failure")

    monkeypatch.setattr(transaction.os, "replace", replace_and_reuse)
    monkeypatch.setattr(Path, "lstat", same_identity)
    monkeypatch.setattr(transaction, "_sync_directory", sync)
    if verification_failure:
        with pytest.raises(transaction.TransactionError, match="verification") as error:
            transaction.apply(
                plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups",
            )
        assert error.value.changed and error.value.receipt.is_file()
    else:
        assert transaction.apply(
            plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups",
        ).is_file()
    assert target.read_bytes() == plan.after
    assert lock.read_bytes() == b"successor writer"


def test_backup_failure_does_not_write_and_cleans_only_its_lock(tmp_path, monkeypatch):
    target = tmp_path / ".gitconfig"
    target.write_bytes(b"[user]\n\tname = Before\n")
    plan = transaction.preview(target, {"user.name": ["After"]})

    def fail(*_args):
        raise OSError("synthetic backup failure")

    monkeypatch.setattr(transaction, "_backup", fail)
    with pytest.raises(OSError, match="backup"):
        transaction.apply(
            plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
        )
    assert target.read_bytes() == plan.before
    assert not target.with_name(target.name + ".lock").exists()


@pytest.mark.parametrize("depth", [1, 3])
@pytest.mark.parametrize("sync_failure", [False, True])
def test_new_backup_ancestors_are_synced_before_commit(tmp_path, monkeypatch, depth, sync_failure):
    target = tmp_path / ".gitconfig"
    original = b"[user]\n\tname = Before\n"
    target.write_bytes(original)
    plan = transaction.preview(target, {"user.name": ["After"]})
    root = tmp_path.joinpath(*(f"new-{index}" for index in range(depth)), "backups")
    sync_directory = transaction._sync_directory
    replace = os.replace
    observed = []
    committed = []
    required = [root, *list(root.parents)[:depth + 1]]

    def sync(path):
        observed.append(path)
        if sync_failure and path == tmp_path:
            raise OSError("synthetic backup ancestor sync failure")
        sync_directory(path)

    def commit(source, destination):
        assert observed[-len(required):] == required
        committed.append(True)
        replace(source, destination)

    monkeypatch.setattr(transaction, "_sync_directory", sync)
    monkeypatch.setattr(transaction.os, "replace", commit)
    if sync_failure:
        with pytest.raises(OSError, match="ancestor sync"):
            transaction.apply(
                plan, expected_plan_sha256=plan.plan_sha256, backup_root=root,
            )
        assert not committed and target.read_bytes() == original
    else:
        assert transaction.apply(
            plan, expected_plan_sha256=plan.plan_sha256, backup_root=root,
        ).is_file()
        assert committed and target.read_bytes() == plan.after
    assert not target.with_name(target.name + ".lock").exists()


def test_read_only_configuration_is_refused_without_side_effects(tmp_path):
    target = tmp_path / ".gitconfig"
    target.write_bytes(b"[user]\n\tname = Before\n")
    plan = transaction.preview(target, {"user.name": ["After"]})
    target.chmod(0o444)
    try:
        plan = transaction.preview(target, {"user.name": ["After"]})
        with pytest.raises(transaction.TransactionError, match="read-only"):
            transaction.apply(
                plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
            )
        assert not (tmp_path / "backups").exists()
        assert not target.with_name(target.name + ".lock").exists()
    finally:
        target.chmod(0o600)


def test_post_write_verification_failure_retains_receipt_and_reports_mutation(
    tmp_path, monkeypatch
):
    target = tmp_path / ".gitconfig"
    target.write_bytes(b"[user]\n\tname = Before\n")
    plan = transaction.preview(target, {"user.name": ["After"]})
    snapshot = transaction._snapshot

    def fail_after_write(path):
        result = snapshot(path)
        if path == target and result[0] == plan.after:
            raise transaction.TransactionError("synthetic verification failure")
        return result

    monkeypatch.setattr(transaction, "_snapshot", fail_after_write)
    with pytest.raises(transaction.TransactionError, match="verification") as error:
        transaction.apply(
            plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
        )
    assert error.value.changed
    assert error.value.receipt.is_file()
    assert target.read_bytes() == plan.after


def test_cleanup_failure_after_commit_cannot_hide_the_mutation(tmp_path, monkeypatch):
    target = tmp_path / ".gitconfig"
    created = transaction.preview(target, {"user.name": ["After"]})
    receipt = transaction.apply(
        created, expected_plan_sha256=created.plan_sha256, backup_root=tmp_path / "backups",
    )
    plan = transaction.rollback_preview(target, receipt)
    owns = transaction._owns_lock

    def fail_after_commit(path, identity):
        if not target.exists():
            raise PermissionError("synthetic cleanup observation failure")
        return owns(path, identity)

    monkeypatch.setattr(transaction, "_owns_lock", fail_after_commit)
    with pytest.raises(transaction.TransactionError, match="cleanup") as error:
        transaction.apply(
            plan,
            expected_plan_sha256=plan.plan_sha256,
            backup_root=tmp_path / "backups",
        )
    assert error.value.changed and error.value.receipt.is_file()
    assert not target.exists()


def test_rollback_rejects_array_shaped_and_duplicate_field_receipts(tmp_path):
    target = tmp_path / ".gitconfig"
    plan = transaction.preview(target, {"user.name": ["After"]})
    receipt = transaction.apply(
        plan, expected_plan_sha256=plan.plan_sha256, backup_root=tmp_path / "backups"
    )
    original = receipt.read_bytes()
    payload = json.loads(original)
    for forged in (
        json.dumps(list(payload.items())).encode("utf-8"),
        original[:-1] + b',"schema":"agent-machines.git-config-receipt/v1"}',
    ):
        receipt.write_bytes(forged)
        with pytest.raises(transaction.TransactionError, match="receipt"):
            transaction.rollback_preview(target, receipt)
        assert target.read_bytes() == plan.after


def test_malformed_config_and_unrelated_values_are_not_printed(tmp_path):
    target = tmp_path / ".gitconfig"
    sentinel = b"unrelated-sensitive-fixture"
    target.write_bytes(b"[private]\n\tvalue = " + sentinel + b"\n")
    preview = transaction.preview(target, {"user.name": ["Example"]})
    assert sentinel.decode() not in json.dumps(preview.summary())
    target.write_bytes(b"[invalid\n" + sentinel)
    with pytest.raises(transaction.TransactionError) as error:
        transaction.preview(target, {"user.name": ["Example"]})
    assert sentinel.decode() not in str(error.value)


def test_git_configuration_environment_cannot_redirect_the_candidate(tmp_path, monkeypatch):
    target = tmp_path / ".gitconfig"
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"retain")
    monkeypatch.setenv("GIT_CONFIG", str(unrelated))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(unrelated))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Ambient")
    preview = transaction.preview(target, {"user.name": ["Example"]})
    assert b"Example" in preview.after and b"Ambient" not in preview.after
    assert unrelated.read_bytes() == b"retain"


def test_linked_backup_directory_is_not_followed(tmp_path):
    target = tmp_path / ".gitconfig"
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "backups"
    try:
        root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("the host does not permit creating this symbolic-link fixture")
    plan = transaction.preview(target, {"user.name": ["After"]})
    with pytest.raises(transaction.TransactionError, match="link"):
        transaction.apply(plan, expected_plan_sha256=plan.plan_sha256, backup_root=root)
    assert not target.exists()
    assert not list(outside.iterdir())


@pytest.mark.parametrize(
    "entries",
    [
        None,
        {},
        [],
        [{"key": "missing-section", "values": ["x"]}],
        [{"key": "section.\nkey", "values": ["x"]}],
        [{"key": "section.key", "values": ["\0"]}],
        [{"key": "section.key", "values": [False]}],
        [{"key": "section.key", "state": "absent", "values": []}],
        [{"key": "section.key", "state": "unknown", "values": ["x"]}],
        [{"key": "section.key", "values": ["x"], "extra": True}],
        [{"key": "User.Name", "values": ["x"]}, {"key": "user.name", "values": ["y"]}],
        [{"key": "credential.password", "values": ["x"]}],
        [{"key": "http.https://example.com.extraHeader", "values": ["x"]}],
    ],
)
def test_invalid_declarations_are_rejected_without_an_actor(entries):
    with pytest.raises(ValueError):
        transaction.validate_entries(entries)


def test_key_normalization_preserves_subsection_case_and_ordered_resets():
    assert transaction.validate_entries(
        [
            {"key": "Credential.https://Example.com.Helper", "values": ["", "!first", "!second"]},
            {"key": "User.Name", "state": "absent"},
        ]
    ) == {
        "credential.https://Example.com.helper": ["", "!first", "!second"],
        "user.name": None,
    }


def test_expired_planning_budget_does_not_start_a_process(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        transaction.subprocess, "run", lambda *_args, **_kwargs: calls.append(True)
    )
    monkeypatch.setattr(transaction.time, "monotonic", lambda: 10)
    with pytest.raises(transaction.TransactionError, match="deadline"):
        transaction._git(tmp_path / "candidate", "--list", deadline=9)
    assert calls == []
