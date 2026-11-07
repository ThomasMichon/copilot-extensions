"""File-backed scheduled reclamation; native deletion is never simulated."""

from __future__ import annotations

import copy
import io
import json
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

import pytest

from agent_logger.config import Config, load_config
from agent_logger import __main__ as logger_cli
from agent_logger.segmenter import prepare_log
from agent_logger.sync import (
    engine, log_archives, log_reclamation, log_reclamation_runtime, log_retirement_file,
)
from agent_logger.sync.log_archive_cli import run_compact_logs
from agent_logger.sync.targets import build_target
from agent_logger.sync.targets import filesystem
from agent_logger.sync.targets.base import PushResult
from agent_logger.sync.targets.filesystem import LocalTarget

pytestmark = [pytest.mark.guard, pytest.mark.contract("agent_logger.process_logs.reclamation")]
native = pytest.mark.skipif(os.name != "nt", reason="native scheduled exact-entry retirement")
NAME = "process-1000-123.log"
CONTENT = b"synthetic provider accounting\n"


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Config, Path]:
    home = tmp_path / "logger"
    home.mkdir()
    source = tmp_path / "source"
    (source / "logs").mkdir(parents=True)
    raw = source / "logs" / NAME
    raw.write_bytes(CONTENT)
    os.utime(raw, (1, 1))
    data = {
        "machine": {"name": "example"},
        "sync": {
            "source": str(source), "change_tracking": {"enabled": False},
            "targets": {
                "local": {"path": str(tmp_path / "first")},
                "onedrive": {"root": str(tmp_path / "second"), "subfolder": "sessions"},
            },
            "process_logs": {
                "enabled": True,
                "zip_snapshots": {"enabled": True, "local_source": True, "min_quiet_seconds": 1},
                "reclamation": {
                    "enabled": True, "min_age_seconds": 60,
                    "required_targets": ["local", "onedrive"],
                },
            },
        },
    }
    (home / "config.yaml").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.delenv("AGENT_LOGGER_SYNC_DISABLED", raising=False)
    monkeypatch.delenv("AGENT_LOGGER_SYNC_TARGET", raising=False)
    monkeypatch.setattr(log_archives, "producer_exited", lambda pid: True)
    monkeypatch.setattr(log_reclamation, "producer_exited", lambda pid: True)
    return load_config(home=home, include_repo=False), raw


def _write_policy(cfg: Config, mutate: Any) -> None:
    path = cfg.home / "config.yaml"
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def _pass(cfg: Config) -> log_reclamation_runtime.LogReclamationPass:
    run = log_reclamation_runtime.prepare_reclamation(
        cfg, "example", build_target(cfg.sync_target, cfg.target_options(cfg.sync_target)),
        dry_run=True,
    )
    assert run is not None
    return run


def test_source_roots_runtime_provenance_requires_explicit_machine_only_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    for ordinary in [load_config(home=cfg.home), Config(cfg.as_dict(), cfg.home)]:
        assert ordinary.process_log_reclamation.enabled
        assert ordinary._machine_origin is None
        with pytest.raises(ValueError, match="requires file-backed"):
            engine.run_sync(ordinary, dry_run=True)
    assert raw.read_bytes() == CONTENT
    with cfg._machine_origin.guard(cfg):
        pass
    assert "synthetic provider" not in repr(cfg._machine_origin)


@pytest.mark.parametrize("kind", ["disable", "age", "target", "source", "lock", "filter", "machine"])
def test_source_roots_runtime_current_file_policy_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    run = _pass(cfg)
    def mutate(data: dict[str, Any]) -> None:
        sync = data["sync"]
        if kind == "disable":
            sync["process_logs"]["reclamation"]["enabled"] = False
        elif kind == "age":
            sync["process_logs"]["reclamation"]["min_age_seconds"] = 600
        elif kind == "target":
            sync["targets"]["local"]["path"] = str(tmp_path / "changed")
        elif kind == "source":
            sync["source"] = str(tmp_path / "changed")
        elif kind == "lock":
            sync["lock_name"] = "changed.lock"
        elif kind == "filter":
            sync["repo_allowlist"] = ["example"]
        else:
            data["machine"]["name"] = "different"
    _write_policy(cfg, mutate)
    with pytest.raises(ValueError, match="policy file changed"):
        with run.guard():
            pytest.fail("a changed local policy reached disposition")
    assert raw.read_bytes() == CONTENT


@pytest.mark.parametrize("kind", ["memory", "target_env", "source_root", "disabled", "target_root"])
def test_source_roots_runtime_effective_identity_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    first = tmp_path / "first" / "example" / "logs"
    first.mkdir(parents=True)
    run = _pass(cfg)
    if kind == "memory":
        cfg._data["sync"]["lock_name"] = "changed.lock"
    elif kind == "target_env":
        monkeypatch.setenv("AGENT_LOGGER_SYNC_TARGET", "onedrive")
    elif kind == "source_root":
        raw.parent.rename(raw.parent.with_name("old"))
        raw.parent.mkdir()
        raw.write_bytes(CONTENT)
    elif kind == "target_root":
        first.rename(first.with_name("old"))
        first.mkdir()
    else:
        monkeypatch.setenv("AGENT_LOGGER_SYNC_DISABLED", "1")
    with pytest.raises(ValueError, match="changed|disabled"):
        with run.guard():
            pytest.fail("changed state reached disposition")


@pytest.mark.parametrize("field,value", [
    ("repo_allowlist", ["example"]), ("repo_denylist", ["example"]),
    ("require_repo_opt_in", True),
])
def test_source_roots_runtime_filtered_context_refused_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    _write_policy(cfg, lambda data: data["sync"].update({field: value}))
    cfg = load_config(home=cfg.home, include_repo=False)
    with pytest.raises(ValueError, match="repository-filtered"):
        engine.run_sync(cfg, dry_run=True)
    assert not (cfg.home / cfg.sync_lock_name).exists()
    assert raw.read_bytes() == CONTENT


def test_source_roots_runtime_dry_run_has_no_writes_or_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("dry run wrote, acquired a lock, or retired")
    monkeypatch.setattr(engine, "sync_lock", forbidden)
    monkeypatch.setattr(os, "mkdir", forbidden)
    monkeypatch.setattr(os, "fsync", forbidden)
    monkeypatch.setattr(log_reclamation, "retirement_file", forbidden)
    assert engine.run_sync(cfg, dry_run=True) == 1  # No existing verified ZIPs.
    assert "dry-run inspected=1 eligible=1 retained=1 retired=0 failed=1" in capsys.readouterr().out
    assert raw.read_bytes() == CONTENT
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes()
                      for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.skipif(os.name == "nt", reason="POSIX must fail before all sync writes")
def test_source_roots_runtime_posix_enabled_refused_before_sync_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    with pytest.raises(OSError, match="requires native Windows"):
        engine.run_sync(cfg)
    assert raw.read_bytes() == CONTENT
    assert not (cfg.home / cfg.sync_lock_name).exists()
    assert list(raw.parent.iterdir()) == [raw]


@native
def test_source_roots_runtime_native_existing_lock_and_two_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    original = engine.sync_lock
    held = False
    @contextmanager
    def tracked_lock(*args: Any, **kwargs: Any) -> Iterator[bool]:
        nonlocal held
        with original(*args, **kwargs) as acquired:
            held = acquired
            try:
                yield acquired
            finally:
                held = False
    original_retire = log_reclamation.RetirementFile.retire
    def observed_retire(self: Any, *args: Any, **kwargs: Any) -> None:
        assert held
        original_retire(self, *args, **kwargs)
    monkeypatch.setattr(engine, "sync_lock", tracked_lock)
    monkeypatch.setattr(log_reclamation.RetirementFile, "retire", observed_retire)
    assert engine.run_sync(cfg) == 0
    assert not raw.exists()
    assert "retired=1 failed=0 committed_unconfirmed=0 reclaimed_bytes=30" in capsys.readouterr().out
    assert len(list(raw.parent.glob("*.zip"))) == 1
    assert len(list((tmp_path / "first" / "example" / "logs").glob("*.zip"))) == 1
    second = tmp_path / "second" / "sessions" / "example"
    assert len(list((second / "logs").glob("*.zip"))) == 1
    assert not list(second.rglob("*.log"))
    assert not (second / "session-state").exists()
    assert engine.run_sync(cfg) == 0
    assert "inspected=0" in capsys.readouterr().out


@native
@pytest.mark.parametrize("kind", ["file", "memory", "disabled", "producer", "partial"])
def test_source_roots_runtime_change_during_publication_retains_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    original = LocalTarget.push_process_logs
    def changed(self: LocalTarget, *args: Any, **kwargs: Any) -> PushResult:
        result = original(self, *args, **kwargs)
        if kind == "file":
            _write_policy(cfg, lambda d: d["sync"]["process_logs"]["reclamation"].update(enabled=False))
        elif kind == "memory":
            cfg._data["sync"]["process_logs"]["reclamation"]["enabled"] = False
        elif kind == "disabled":
            monkeypatch.setenv("AGENT_LOGGER_SYNC_DISABLED", "1")
        elif kind == "producer":
            monkeypatch.setattr(log_reclamation, "producer_exited", lambda pid: False)
        else:
            result.process_logs_complete = False
        return result
    monkeypatch.setattr(LocalTarget, "push_process_logs", changed)
    if kind in {"file", "memory", "disabled"}:
        with pytest.raises(ValueError, match="changed|disabled"):
            engine.run_sync(cfg)
    else:
        assert engine.run_sync(cfg) == (1 if kind == "partial" else 0)
    assert raw.read_bytes() == CONTENT


def test_source_roots_runtime_manual_snapshot_remains_additive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    assert run_compact_logs(cfg, dry_run=False) == 0
    assert raw.read_bytes() == CONTENT
    assert len(list(raw.parent.glob("*.zip"))) == 1
    assert not (tmp_path / "first").exists() and not (tmp_path / "second").exists()


def test_source_roots_runtime_cli_machine_only_is_explicit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    cfg, _ = _setup(tmp_path, monkeypatch)
    calls = []
    def loader(**kwargs: Any) -> Config:
        calls.append(copy.deepcopy(kwargs))
        return cfg
    monkeypatch.setattr(engine, "load_config", loader)
    monkeypatch.setattr(engine, "run_sync", lambda *a, **kw: 0)
    assert engine.main(["run", "--machine-config-only", "--dry-run"]) == 0
    assert calls == [{"include_repo": False}]
    with pytest.raises(ValueError, match="does not support detached"):
        engine.main(["run", "--machine-config-only", "--detach"])


def test_source_roots_runtime_disabled_automation_has_no_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_LOGGER_SYNC_DISABLED", "1")
    assert engine.run_sync(cfg) == 0
    assert list(raw.parent.iterdir()) == [raw]
    assert not (cfg.home / cfg.sync_lock_name).exists()
    assert not (tmp_path / "first").exists()


def test_source_roots_runtime_locked_deferral_is_not_positive_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(filesystem, "_copy_process_logs", lambda *args: (0, 0, [raw]))
    target = build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))
    result = target.push_process_logs(raw.parent, "example")
    assert result.ok and not result.process_logs_complete
    assert "skipped 1 locked" in result.detail
    assert raw.read_bytes() == CONTENT


@pytest.mark.parametrize("name", ["ssh", "ssh-tunnel", "ingest", "unknown"])
def test_source_roots_runtime_unsupported_transport_refused_before_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    _write_policy(cfg, lambda d: d["sync"]["process_logs"]["reclamation"].update(
        required_targets=["local", name],
    ))
    cfg = load_config(home=cfg.home, include_repo=False)
    with pytest.raises(ValueError, match="required transport is unsupported"):
        engine.run_sync(cfg, dry_run=True)
    assert list(raw.parent.iterdir()) == [raw]
    assert not (cfg.home / cfg.sync_lock_name).exists()


def test_source_roots_runtime_namespace_push_never_fans_out_or_retires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    rescue = tmp_path / "rescue"
    (rescue / "session-state").mkdir(parents=True)
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("namespace push attempted local process-log reclamation")
    monkeypatch.setattr(log_reclamation_runtime, "reclaim_process_logs", forbidden)
    assert engine.run_push(cfg, source=str(rescue), machine=".codespaces/example") == 0
    assert raw.read_bytes() == CONTENT
    assert list(raw.parent.iterdir()) == [raw]
    assert not (tmp_path / "second").exists()


def test_source_roots_runtime_pass_source_identity_is_not_recaptured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    run = _pass(cfg)
    raw.parent.rename(raw.parent.with_name("old"))
    raw.parent.mkdir()
    raw.write_bytes(CONTENT)
    with pytest.raises(ValueError, match="source root changed after pass preparation"):
        log_reclamation.reclaim_process_logs(
            run.source, settings=run.settings, archives=run.archives,
            process_logs_enabled=True, active_target=run.active_target,
            targets=run.targets, policy_guard=run.guard, dry_run=True,
            source_identity=run.identity,
        )
    assert not (tmp_path / "first").exists()


@native
def test_source_roots_runtime_native_policy_file_and_ancestry_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    native_file_writer: Callable[[Path], AbstractContextManager[int]],
) -> None:
    cfg, _ = _setup(tmp_path, monkeypatch)
    run = _pass(cfg)
    path = cfg.home / "config.yaml"
    with native_file_writer(path):
        with pytest.raises(OSError) as denied:
            with run.guard():
                pytest.fail("policy guard admitted a pre-existing native writer")
        assert denied.value.winerror == 32
    with run.guard():
        with pytest.raises(OSError) as denied:
            with native_file_writer(path):
                pytest.fail("policy guard admitted a new native writer")
        assert denied.value.winerror == 32
        with pytest.raises(OSError):
            path.rename(path.with_name("old.yaml"))
        with pytest.raises(OSError):
            cfg.home.rename(cfg.home.with_name("old"))
    # Locks are scoped to the commit: the operator may edit after it releases.
    with native_file_writer(path):
        pass


@native
@pytest.mark.parametrize("kind", ["file", "memory", "disabled"])
def test_source_roots_runtime_native_final_hash_policy_change_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    original = log_retirement_file._hash_stream
    called = False
    def changed(stream: Any, size: int) -> str:
        nonlocal called
        digest = original(stream, size)
        assert not called
        called = True
        if kind == "file":
            _write_policy(cfg, lambda d: d["sync"]["process_logs"]["reclamation"].update(enabled=False))
        elif kind == "memory":
            cfg._data["sync"]["process_logs"]["reclamation"]["min_age_seconds"] = 999999
        else:
            monkeypatch.setenv("AGENT_LOGGER_SYNC_DISABLED", "1")
        return digest
    monkeypatch.setattr(log_retirement_file, "_hash_stream", changed)
    assert engine.run_sync(cfg) == 1
    assert called and raw.read_bytes() == CONTENT
    assert "eligible=1 retained=1 retired=0 failed=1" in capsys.readouterr().out
    for directory in [
        raw.parent, tmp_path / "first" / "example" / "logs",
        tmp_path / "second" / "sessions" / "example" / "logs",
    ]:
        assert len(list(directory.glob("*.zip"))) == 1


@pytest.mark.parametrize("command", ["status", "compact-logs", "push", "prepare", "scan", "tick"])
def test_source_roots_runtime_non_retiring_cli_preserves_repository_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(cfg.home))
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    repo_config = repo / ".agent-logger.yaml"
    repo_config.write_text(json.dumps({
        "log": {"root": "output", "note_marker": "EXAMPLE_NOTE"},
        "sync": {"local_path": str(tmp_path / "repo-target")},
    }), encoding="utf-8")
    monkeypatch.chdir(repo)
    ordinary = load_config()
    assert ordinary.repo_config_path == repo_config
    assert ordinary.sync_path == tmp_path / "repo-target"
    assert ordinary.note_marker == "EXAMPLE_NOTE"
    assert ordinary.process_log_reclamation.enabled and ordinary._machine_origin is None
    if command == "push":
        rescue = tmp_path / "rescue"
        (rescue / "session-state").mkdir(parents=True)
        assert engine.main(["push", "--source", str(rescue), "--machine", ".codespaces/example"]) == 0
    elif command == "prepare":
        user = tmp_path / "user"
        session = user / ".copilot" / "session-state" / "example-session"
        session.mkdir(parents=True)
        monkeypatch.setenv("HOME", str(user))
        monkeypatch.setenv("USERPROFILE", str(user))
        class Output(io.StringIO):
            def reconfigure(self, **kwargs: object) -> None:
                pass
        output = Output()
        with monkeypatch.context() as context:
            context.setattr(prepare_log.sys, "stdout", output)
            context.setattr(prepare_log.sys, "argv", [
                "prepare-session-log", "--session", "example-session", "--json",
            ])
            prepare_log.main()
        result = json.loads(output.getvalue())
        assert result["output_root"] == str(repo / "output")
        assert result["note_marker"] == "EXAMPLE_NOTE"
    elif command in {"scan", "tick"}:
        assert logger_cli.main(["chronicle", command]) == 0
    else:
        assert engine.main([command]) == 0
    assert raw.read_bytes() == CONTENT
    assert not (tmp_path / "second").exists()
    if command == "compact-logs":
        assert len(list(raw.parent.glob("*.zip"))) == 1
    else:
        assert not list(raw.parent.glob("*.zip"))
    if command == "status":
        assert str(tmp_path / "repo-target") in capsys.readouterr().out


def test_source_roots_runtime_initially_missing_logs_dry_run_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    raw.parent.rename(raw.parent.with_name("retained-elsewhere"))
    assert engine.run_sync(cfg, dry_run=True) == 0
    assert "reclamation skipped (no process-log source)" in capsys.readouterr().out
    assert not raw.parent.exists()
    assert not (cfg.home / cfg.sync_lock_name).exists()


@native
@pytest.mark.parametrize("appears_later", [False, True])
def test_source_roots_runtime_initially_absent_logs_do_not_block_sessions_or_get_adopted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, appears_later: bool,
) -> None:
    cfg, raw = _setup(tmp_path, monkeypatch)
    raw.parent.rename(raw.parent.with_name("retained-elsewhere"))
    session = cfg.sync_source / "session-state" / "example-session"
    session.mkdir(parents=True)
    (session / "events.jsonl").write_text("{}\n", encoding="utf-8")
    if appears_later:
        original = engine._mark_source
        def create_logs(*args: Any, **kwargs: Any) -> None:
            raw.parent.mkdir()
            raw.write_bytes(CONTENT)
            os.utime(raw, (1, 1))
            original(*args, **kwargs)
        monkeypatch.setattr(engine, "_mark_source", create_logs)
    assert engine.run_sync(cfg) == 0
    assert (tmp_path / "first" / "example" / "session-state" / "example-session" / "events.jsonl").is_file()
    assert not (tmp_path / "second").exists()
    if appears_later:
        assert raw.read_bytes() == CONTENT
    else:
        assert not raw.parent.exists()
