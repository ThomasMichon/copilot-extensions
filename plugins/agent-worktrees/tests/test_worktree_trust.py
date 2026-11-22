"""Native folder trust is explicit, serialized and scoped to admitted entry."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import replace
from pathlib import Path
from typing import NoReturn

import pytest
from machine_transport import detect_platform

from agent_worktrees import (
    config_authority, permissions, session_binding_cli, tracking, tracking_write, worktree_trust,
    worktree_identity,
)
from agent_worktrees.execution_spaces import ExecutionSpaceError
from agent_worktrees.launch_preflight import LaunchPreflight

NativeCheckout = tuple[Path, Path]


@pytest.fixture
def native_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> NativeCheckout:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    home = tmp_path / ".copilot"
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    monkeypatch.setattr(permissions, "_copilot_dir", lambda: home)
    return checkout, home


def test_first_trust_creates_native_config_without_tool_approvals(native_checkout: NativeCheckout) -> None:
    checkout, home = native_checkout
    assert permissions.ensure_trusted_folder(str(checkout)) is True
    config = home / "config.json"
    assert json.loads(config.read_text()) == {"trustedFolders": [str(checkout.resolve())]}
    before = config.stat().st_mtime_ns
    assert permissions.ensure_trusted_folder(str(checkout)) is False
    assert config.stat().st_mtime_ns == before
    assert not (home / "permissions-config.json").exists()


def test_ensure_preserves_header_existing_trust_and_unrelated_settings(native_checkout: NativeCheckout) -> None:
    checkout, home = native_checkout
    home.mkdir()
    header = "// Managed settings.\n\n"
    initial = {"trustedFolders": ["/existing"], "endpoint": "https://example.test/path"}
    (home / "config.json").write_text(header + json.dumps(initial), encoding="utf-8")
    assert permissions.ensure_trusted_folder(str(checkout)) is True
    text = (home / "config.json").read_text()
    assert text.startswith(header)
    assert json.loads(text[len(header):]) == {
        **initial, "trustedFolders": ["/existing", str(checkout.resolve())],
    }


@pytest.mark.parametrize("content", ['{', '[]', '{"trustedFolders": {}}', '{"trustedFolders": [3]}'])
def test_invalid_config_is_explicit_and_preserved(native_checkout: NativeCheckout, content: str) -> None:
    checkout, home = native_checkout
    home.mkdir()
    config = home / "config.json"
    config.write_text(content)
    with pytest.raises(permissions.FolderTrustError, match="Could not ensure"):
        permissions.ensure_trusted_folder(str(checkout))
    assert config.read_text() == content


@pytest.mark.parametrize("kind", ["relative", "missing", "file", "root"])
def test_non_checkout_path_cannot_create_trust(native_checkout: NativeCheckout, kind: str) -> None:
    checkout, home = native_checkout
    target = {
        "relative": Path("relative"),
        "missing": checkout / "missing",
        "file": checkout / "ordinary.txt",
        "root": Path(checkout.anchor),
    }[kind]
    if kind == "file":
        target.write_text("not a checkout")
    with pytest.raises(permissions.FolderTrustError, match="native checkout"):
        permissions.ensure_trusted_folder(str(target))
    assert not home.exists()


def test_strict_lock_failure_is_not_already_trusted(native_checkout: NativeCheckout) -> None:
    checkout, home = native_checkout
    home.mkdir()
    (home / "config.lock").mkdir()
    with pytest.raises(permissions.FolderTrustError, match="Could not lock"):
        permissions.ensure_trusted_folder(str(checkout))
    assert not (home / "config.json").exists()


def test_missing_lock_backend_is_explicit(
    native_checkout: NativeCheckout, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    checkout, home = native_checkout
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", None)
    with pytest.raises(permissions.FolderTrustError, match="Could not lock"):
        permissions.ensure_trusted_folder(str(checkout))
    assert not (home / "config.json").exists()


def test_failed_atomic_replace_keeps_original(
    native_checkout: NativeCheckout, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, home = native_checkout
    home.mkdir()
    config = home / "config.json"
    config.write_text('{"unrelated": true}')

    def refuse(*args: object) -> NoReturn:
        raise OSError("fixture write refusal")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(permissions.FolderTrustError, match="fixture write refusal"):
        permissions.ensure_trusted_folder(str(checkout))
    assert config.read_text() == '{"unrelated": true}'
    assert not list(home.glob("*.tmp"))


def test_removed_checkout_clears_canonical_trust_for_lexical_path(native_checkout: NativeCheckout) -> None:
    checkout, home = native_checkout
    alias = checkout / ".." / checkout.name
    assert permissions.ensure_trusted_folder(str(alias))
    checkout.rmdir()
    assert permissions.remove_trusted_folder(str(alias))
    assert json.loads((home / "config.json").read_text())["trustedFolders"] == []


def test_refused_receiving_authority_does_not_trust(
    native_checkout: NativeCheckout, sample_record: tracking.WorktreeRecord,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout, home = native_checkout
    record = replace(sample_record, worktree_path=str(checkout))

    def refuse(record: tracking.WorktreeRecord) -> NoReturn:
        raise ExecutionSpaceError("different execution space")

    monkeypatch.setattr(config_authority, "registration_record_config", refuse)
    with pytest.raises(ExecutionSpaceError, match="different execution space"):
        worktree_trust.ensure_record_trusted(record)
    assert not home.exists()


@pytest.mark.parametrize("foreign", ["machine", "platform"])
def test_real_execution_space_fence_precedes_trust(
    native_checkout: NativeCheckout, sample_record: tracking.WorktreeRecord,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign: str,
) -> None:
    from agent_worktrees import config as cfg

    checkout, home = native_checkout
    (tmp_path / "machines.yaml").write_text(
        "machines:\n  local-space:\n    execution_platform: linux\n"
        "  other-space:\n    execution_platform: windows\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    config = cfg.Config(
        srcroot=str(tmp_path), machine="local-space", platform="linux", repo_name="test-repo",
        repos={"test-repo": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))},
    )
    record = replace(
        sample_record, worktree_path=str(checkout),
        machine="other-space" if foreign == "machine" else "local-space",
        platform="windows",
    )
    with pytest.raises(ExecutionSpaceError, match="execution space|platform"):
        worktree_trust.ensure_record_trusted(record, config)
    assert not home.exists()


def test_terminal_managed_record_does_not_trust(
    native_checkout: NativeCheckout, sample_record: tracking.WorktreeRecord,
    monkeypatch_config: None,
) -> None:

    checkout, home = native_checkout
    record = replace(
        sample_record, worktree_path=str(checkout), platform=detect_platform(),
        kind="bridge", status="finalized",
    )
    with pytest.raises(permissions.FolderTrustError, match="terminal managed"):
        worktree_trust.ensure_record_trusted(record)
    assert not home.exists()


@pytest.mark.parametrize("mode", ["cold", "invalid-config", "dry-run", "live"])
def test_embody_establishes_trust_before_spawning_copilot(
    native_checkout: NativeCheckout, sample_record: tracking.WorktreeRecord,
    tmp_tracking_dir: Path, monkeypatch_config: None, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], mode: str,
) -> None:
    _assert_embody_trust(
        native_checkout, sample_record, tmp_tracking_dir, monkeypatch, capfd, mode,
    )


@pytest.mark.parametrize("source", ["overlay", "relative-overlay", "account-overlay", "inherited"])
@pytest.mark.parametrize("mode", ["cold", "invalid-config"])
def test_embody_establishes_trust_in_effective_launch_home(
    sample_record: tracking.WorktreeRecord, tmp_path: Path, tmp_tracking_dir: Path,
    monkeypatch_config: None, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], source: str, mode: str,
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    account_home = tmp_path / "account"
    default_home = account_home / ".copilot"
    default_home.mkdir(parents=True)
    default_config = default_home / "config.json"
    default_config.write_text("malformed default", encoding="utf-8")
    launch_home = checkout / "selected-copilot" if source == "relative-overlay" else tmp_path / "selected-copilot"
    if source == "account-overlay":
        launch_home = tmp_path / "selected-account" / ".copilot"
    inherited_home = tmp_path / "inherited-copilot"
    monkeypatch.setenv("USERPROFILE", str(account_home))
    monkeypatch.setenv("HOME", str(account_home))
    monkeypatch.setenv("COPILOT_HOME", str(launch_home if source == "inherited" else inherited_home))
    home_value = "selected-copilot" if source == "relative-overlay" else str(launch_home)
    environment = {} if source == "inherited" else {"COPILOT_HOME": home_value}
    if source == "account-overlay":
        environment = {
            "COPILOT_HOME": "", "HOME": str(launch_home.parent),
            "USERPROFILE": str(launch_home.parent),
        }
    before = dict(os.environ)
    _assert_embody_trust(
        (checkout, launch_home), sample_record, tmp_tracking_dir, monkeypatch, capfd, mode,
        launch_environment=environment,
    )
    assert dict(os.environ) == before
    assert default_config.read_text(encoding="utf-8") == "malformed default"
    assert not (default_home / "config.lock").exists()
    assert (launch_home / "config.lock").is_file()
    assert not inherited_home.exists()


def _assert_embody_trust(
    native_checkout: NativeCheckout, sample_record: tracking.WorktreeRecord,
    tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str], mode: str,
    *, launch_environment: dict[str, str] | None = None,
) -> None:
    import argparse

    from agent_worktrees import (
        __main__ as command, agent_policy, config as cfg, launch_seed_exec, sessions,
    )

    checkout, home = native_checkout
    config = replace(cfg.load_project_config(sample_record.repo), machine=socket.gethostname())
    config.repos[sample_record.repo] = replace(
        config.repos[sample_record.repo], worktree_root=str(checkout.parent),
    )
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_tracking_dir.parent)
    record = replace(
        sample_record, worktree_id=checkout.name, branch=f"worktree/{checkout.name}",
        worktree_path=str(checkout), machine=config.machine, machine_identity=None,
        platform=config.platform, kind="session", status="active",
    )
    tracking.save_record(record, tmp_tracking_dir / f"{record.worktree_id}.yaml")
    if mode == "invalid-config":
        home.mkdir()
        (home / "config.json").write_text("malformed", encoding="utf-8")
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    monkeypatch.setattr(worktree_identity, "_resolve_worktree_id", lambda value: record.worktree_id)
    monkeypatch.setattr(agent_policy, "require", lambda *args, **kwargs: None)
    monkeypatch.setattr(command, "_preflight_launch", lambda *args: LaunchPreflight())
    monkeypatch.setattr(command, "_build_launch_cmd", lambda *args, **kwargs: ["copilot"])
    monkeypatch.setattr(command, "_build_env", lambda *args, **kwargs: dict(launch_environment or {}))
    monkeypatch.setattr(command, "_repo_session_env", lambda *args, **kwargs: {})
    monkeypatch.setattr(sessions, "has_mux_session", lambda value: mode == "live")
    monkeypatch.setattr(sessions, "resolve_resume_target", lambda value: None)
    monkeypatch.setattr(launch_seed_exec, "deferred_command", lambda command, *args, **kwargs: command)
    spawned: list[str] = []

    def spawn(
        worktree_id: str, work_dir: str, argv: list[str], environment: dict[str, str],
    ) -> dict[str, object]:
        assert mode == "cold", "inspection or failed trust must never start Copilot"
        assert environment["COPILOT_HOME"] == str(home)
        assert json.loads((home / "config.json").read_text()) == {
            "trustedFolders": [str(checkout.resolve())],
        }, "folder trust must be persisted before the process boundary"
        spawned.append(work_dir)
        return {"ok": True, "session": f"wt-{worktree_id}", "new_pane": "%5"}

    monkeypatch.setattr(sessions, "mux_new_session", spawn)
    args = argparse.Namespace(
        worktree_id=record.worktree_id, new=False, codename=None, anchor=False,
        seed=None, driver=None, seed_ready_timeout=180.0, verify_timeout=0.0,
        recovery=False, dry_run=mode == "dry-run", ensure_mux=False,
    )
    result = command.cmd_embody(args)
    output = capfd.readouterr()
    if mode == "invalid-config":
        assert result == 3, output.out + output.err
        assert "folder trust" in output.out + output.err
        assert (home / "config.json").read_text() == "malformed"
    else:
        assert result == 0, output.out + output.err
    assert spawned == ([str(checkout)] if mode == "cold" else [])
    if mode in {"dry-run", "live"}:
        assert not home.exists()
    assert not (home / "permissions-config.json").exists()


@pytest.mark.parametrize("source,broken_config,other_active_project", [
    ("hook:new", False, False), ("bind", False, False),
    ("hook:new", True, False), ("bind", True, False),
    ("hook:new", False, True), ("bind", False, True),
])
def test_registered_entry_repairs_trust_after_admission(
    native_checkout: NativeCheckout, sample_record: tracking.WorktreeRecord,
    tmp_tracking_dir: Path, monkeypatch_config: None, monkeypatch: pytest.MonkeyPatch,
    source: str, broken_config: bool, other_active_project: bool,
    capfd: pytest.CaptureFixture[str],
) -> None:
    import argparse
    from agent_worktrees import config as cfg

    checkout, home = native_checkout
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    record = replace(
        sample_record, worktree_path=str(checkout), kind="session", status="active", sessions=[],
        machine="test",
    )
    tracking.save_record(record, tmp_tracking_dir / f"{record.worktree_id}.yaml")
    if broken_config:
        home.mkdir()
        (home / "config.json").write_text("malformed")
    if other_active_project:
        ambient = tmp_tracking_dir.parent / "ambient" / "worktrees"
        ambient.mkdir(parents=True)
        monkeypatch.setattr(cfg, "tracking_dir", lambda: ambient)
        monkeypatch.setattr(tracking, "_owning_tracking_dir", lambda worktree_id: tmp_tracking_dir)
        monkeypatch.setattr(
            worktree_identity, "_resolve_worktree_id", lambda value: value,
        )
    monkeypatch.setattr(session_binding_cli, "_spawn_status_updater", lambda *args: None)
    monkeypatch.setattr(session_binding_cli, "_register_manager_owned_monitor_session", lambda *a, **k: True)
    monkeypatch.setattr(session_binding_cli, "_note_disposition_snapshot", lambda *a, **k: None)
    args = argparse.Namespace(
        session_id="current-session", worktree_id=record.worktree_id,
        worktree_dir=str(checkout), cwd=str(checkout), pane=None, pid=None,
        stdin=False, emit_context=False, handoff_token=None,
    )
    enter = session_binding_cli.cmd_bind_session if source == "bind" else session_binding_cli.cmd_register_session
    if broken_config:
        assert enter(args) != 0
        captured = capfd.readouterr()
        assert "folder trust" in captured.out + captured.err
        assert (home / "config.json").read_text() == "malformed"
        assert tracking.load_record(
            tmp_tracking_dir / f"{record.worktree_id}.yaml"
        ).session_entry("current-session") is not None
        return
    assert enter(args) == 0
    assert json.loads((home / "config.json").read_text())["trustedFolders"] == [str(checkout.resolve())]
    assert enter(args) == 0
    assert not (home / "permissions-config.json").exists()


def test_rejected_registration_does_not_trust(
    native_checkout: NativeCheckout, sample_record: tracking.WorktreeRecord,
    tmp_tracking_dir: Path, monkeypatch_config: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import argparse

    checkout, home = native_checkout
    record = replace(sample_record, worktree_path=str(checkout))
    tracking.save_record(record, tmp_tracking_dir / f"{record.worktree_id}.yaml")
    monkeypatch.setattr(
        tracking_write, "dispatch",
        lambda *args, **kwargs: {"error": "lifecycle", "message": "fixture admission refusal"},
    )
    assert session_binding_cli.cmd_register_session(argparse.Namespace(
        worktree_id=record.worktree_id, session_id="rejected-session",
        cwd=str(checkout), stdin=False, pane=None, pid=None,
    )) == 1
    assert not home.exists()


@pytest.mark.parametrize("mismatch", ["machine", "platform"])
def test_legacy_record_without_topology_cannot_trust_foreign_checkout(
    native_checkout: NativeCheckout, sample_record: tracking.WorktreeRecord,
    tmp_path: Path, mismatch: str,
) -> None:
    from agent_worktrees import config as cfg

    checkout, home = native_checkout
    actual = detect_platform()
    config = cfg.Config(
        srcroot=str(tmp_path), machine="local-host", platform=actual, repo_name="test-repo",
        repos={"test-repo": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))},
    )
    record = replace(
        sample_record, worktree_path=str(checkout),
        machine="foreign-host" if mismatch == "machine" else config.machine,
        platform=actual if mismatch == "machine" else ("windows" if actual != "windows" else "linux"),
    )
    assert not (tmp_path / "machines.yaml").exists()
    with pytest.raises(ExecutionSpaceError, match="local execution|native execution platform"):
        worktree_trust.ensure_record_trusted(record, config)
    assert not home.exists()


def test_declared_legacy_alias_remains_local(
    native_checkout: NativeCheckout, sample_record: tracking.WorktreeRecord, tmp_path: Path,
) -> None:
    from agent_worktrees import config as cfg

    checkout, home = native_checkout
    (tmp_path / "machines.yaml").write_text(
        "machines:\n  local-space:\n    alias: historical-alias\n", encoding="utf-8",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="local-space", platform=detect_platform(), repo_name="test-repo",
        repos={"test-repo": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))},
    )
    record = replace(
        sample_record, worktree_path=str(checkout), machine="historical-alias", platform=config.platform,
    )
    assert worktree_trust.ensure_record_trusted(record, config)
    assert json.loads((home / "config.json").read_text())["trustedFolders"] == [str(checkout.resolve())]


def test_missing_local_claim_identity_refuses_before_legacy_lookup(
    native_checkout: NativeCheckout, sample_record: tracking.WorktreeRecord,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import config as cfg, legacy_wsl_record_locality

    checkout, home = native_checkout
    config = cfg.Config(srcroot=str(tmp_path), machine="local", platform=sample_record.platform,
                        repos={"sample": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))})
    record = replace(sample_record, worktree_path=str(checkout), machine_identity=None)
    monkeypatch.setattr(worktree_trust, "require_record_mutation", lambda *a: None)
    monkeypatch.setattr(worktree_trust, "require_owner_identity", lambda *a: False)
    monkeypatch.setattr(cfg, "detect_platform", lambda: record.platform)
    monkeypatch.setattr(tracking, "parse_claim_ref", lambda *a: None)
    monkeypatch.setattr(legacy_wsl_record_locality, "resolve_legacy_wsl_owner_ref",
                        lambda *a: pytest.fail("missing claim identity cannot enter legacy lookup"))
    with pytest.raises(ExecutionSpaceError, match="valid local claim identity"):
        worktree_trust.ensure_record_trusted(record, config)
    assert not home.exists()
