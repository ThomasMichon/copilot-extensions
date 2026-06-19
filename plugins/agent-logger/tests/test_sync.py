"""Tests for the session-sync engine and targets."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_logger.config import Config, load_config
from agent_logger.sync import engine
from agent_logger.sync.targets import TARGET_NAMES, build_target
from agent_logger.sync.targets.filesystem import (
    LocalTarget,
    OneDriveTarget,
    resolve_onedrive_root,
)
from agent_logger.sync.targets.ingest import IngestTarget
from agent_logger.sync.targets.ssh import SshTarget, SshTunnelTarget


def _make_source(root: Path) -> Path:
    """Create a fake ~/.copilot-style source with one session."""
    src = root / "copilot"
    sess = src / "session-state" / "abc-123"
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text('{"ts": 1}\n', encoding="utf-8")
    (sess / "workspace.yaml").write_text("id: abc-123\n", encoding="utf-8")
    (sess / ".lock").write_text("pid", encoding="utf-8")  # should be excluded
    return src


def test_registry_names_and_classes() -> None:
    assert TARGET_NAMES == ("local", "onedrive", "ssh", "ssh-tunnel", "ingest")
    assert isinstance(build_target("local", {"path": "/tmp/x"}), LocalTarget)
    assert isinstance(build_target("onedrive"), OneDriveTarget)
    assert isinstance(build_target("ssh"), SshTarget)
    assert isinstance(build_target("ssh-tunnel"), SshTunnelTarget)
    assert isinstance(build_target("ingest"), IngestTarget)


def test_build_target_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown sync target"):
        build_target("nope")


def test_local_target_push_excludes_lock_and_writes_meta(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    target = LocalTarget({"path": str(dest_root)})

    result = target.push(src, "m1")
    assert result.ok
    assert result.file_count == 2  # events.jsonl + workspace.yaml, not .lock
    machine_dir = dest_root / "m1"
    assert (machine_dir / "session-state" / "abc-123" / "events.jsonl").is_file()
    assert not (machine_dir / "session-state" / "abc-123" / ".lock").exists()
    assert (machine_dir / "sync-meta.json").is_file()


def test_local_target_push_is_incremental(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    target = LocalTarget({"path": str(tmp_path / "dest")})
    target.push(src, "m1")
    # Nothing changed -> second push copies zero files.
    second = target.push(src, "m1")
    assert second.ok
    assert second.file_count == 0


def test_local_target_prune_removes_old(tmp_path: Path) -> None:
    import os
    import time

    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    target = LocalTarget({"path": str(dest_root)})
    target.push(src, "m1")

    old = time.time() - 40 * 86400
    sess = dest_root / "m1" / "session-state" / "abc-123"
    for f in sess.rglob("*"):
        os.utime(f, (old, old))

    assert target.prune("m1", 30) == 1
    assert not sess.exists()
    # Retention disabled -> no-op.
    assert target.prune("m1", None) == 0


def test_retention_days_coercion(tmp_path: Path) -> None:
    base = load_config(home=tmp_path).as_dict()
    for sentinel in ("infinite", "forever", "", "nonsense"):
        data = dict(base)
        data["sync"] = dict(data["sync"], retention_days=sentinel)
        assert Config(data, tmp_path).sync_retention_days is None
    data = dict(base)
    data["sync"] = dict(data["sync"], retention_days="30")
    assert Config(data, tmp_path).sync_retention_days == 30


def test_local_target_doctor_ok(tmp_path: Path) -> None:
    target = LocalTarget({"path": str(tmp_path / "dest")})
    assert target.doctor().ok


def test_onedrive_root_resolution(monkeypatch, tmp_path: Path) -> None:
    od = tmp_path / "od"
    od.mkdir()
    monkeypatch.setenv("OneDrive", str(od))
    assert resolve_onedrive_root() == od
    target = OneDriveTarget({"subfolder": "Apps/x"})
    assert target._root() == od / "Apps" / "x"


def test_onedrive_doctor_fails_without_root(monkeypatch) -> None:
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "agent_logger.sync.targets.filesystem.resolve_onedrive_root", lambda: None
    )
    assert not OneDriveTarget().doctor().ok


def test_ssh_target_describe_and_doctor() -> None:
    target = SshTarget({"host": "user@example", "remote_path": "/srv/sessions"})
    assert "example" in target.describe()
    # No host configured -> doctor flags it.
    assert not SshTarget({}).doctor().ok


def _cfg(home: Path, source: Path, dest: Path) -> Config:
    data = dict(load_config(home=home).as_dict())
    data["sync"]["source"] = str(source)
    data["sync"]["targets"]["local"]["path"] = str(dest)
    return Config(data, home)


def test_engine_run_sync_local(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)
    rc = engine.run_sync(cfg, verbose=True)
    assert rc == 0
    # Pushed under <dest>/<machine>/.
    machines = list(dest.iterdir())
    assert len(machines) == 1
    assert (machines[0] / "session-state" / "abc-123" / "events.jsonl").is_file()


def test_engine_dry_run_makes_no_dest(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)
    assert engine.run_sync(cfg, dry_run=True) == 0
    assert not dest.exists()


def test_engine_run_sync_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_LOGGER_SYNC_DISABLED", "1")
    cfg = _cfg(tmp_path / "home", _make_source(tmp_path), tmp_path / "dest")
    assert engine.run_sync(cfg) == 0
    assert not (tmp_path / "dest").exists()
