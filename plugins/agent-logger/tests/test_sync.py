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


def test_local_target_overwrites_readonly_dest(tmp_path: Path) -> None:
    """A read-only destination file must be overwritten, not abort the push.

    Regression for a graceful-overlap blocker: the legacy session-sync writes
    provenance markers read-only (0444, surfaced as the DOS read-only attribute
    over CIFS). ``shutil.copy2`` truncate-opens the destination, which raises
    EPERM on such a file and aborts the whole push (and its post-push notify).
    The engine now unlinks the destination before copying, so the overwrite
    succeeds regardless of the existing file's mode.
    """
    import os
    import stat

    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    target = LocalTarget({"path": str(dest_root)})
    target.push(src, "m1")

    # Make a destination file read-only, then change the source so a re-copy is
    # required (larger content -> _needs_copy is True).
    dst_file = dest_root / "m1" / "session-state" / "abc-123" / "events.jsonl"
    os.chmod(dst_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    (src / "session-state" / "abc-123" / "events.jsonl").write_text(
        '{"ts": 1}\n{"ts": 2}\n', encoding="utf-8"
    )

    result = target.push(src, "m1")
    assert result.ok
    assert result.file_count == 1
    assert dst_file.read_text(encoding="utf-8") == '{"ts": 1}\n{"ts": 2}\n'


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


def test_rsync_children_suppress_console_window(monkeypatch, tmp_path: Path) -> None:
    """ssh/ingest pushes must pass the windowless kwargs to their rsync child.

    Regression guard: on Windows a child rsync/ssh process launched from a
    windowless host flashes a console unless CREATE_NO_WINDOW is set. The kwargs
    are a no-op on POSIX, so this asserts they are forwarded verbatim.
    """
    from agent_logger.sync.targets import base, ingest, ssh

    captured: dict = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return _Proc()

    monkeypatch.setattr("shutil.which", lambda _name: "rsync")

    monkeypatch.setattr(ssh.subprocess, "run", _fake_run)
    SshTarget({"host": "user@example", "remote_path": "/srv"}).push(tmp_path, "m1")
    for key, val in base.NO_WINDOW_KWARGS.items():
        assert captured.get(key) == val

    monkeypatch.setattr(ingest.subprocess, "run", _fake_run)
    IngestTarget({"url": "rsync://h/mod"}).push(tmp_path, "m1")
    for key, val in base.NO_WINDOW_KWARGS.items():
        assert captured.get(key) == val


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


def test_engine_run_push_explicit_machine(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", tmp_path / "unused", dest)
    rc = engine.run_push(cfg, source=str(src), machine=".codespaces/my-cs", verbose=True)
    assert rc == 0
    machine_dir = dest / ".codespaces" / "my-cs"
    assert (machine_dir / "session-state" / "abc-123" / "events.jsonl").is_file()
    assert not (machine_dir / "session-state" / "abc-123" / ".lock").exists()
    assert (machine_dir / "sync-meta.json").is_file()


def test_engine_run_push_missing_source(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path / "home", tmp_path / "unused", tmp_path / "dest")
    assert engine.run_push(cfg, source=str(tmp_path / "nope"), machine="m") == 1


def test_engine_push_parser_wires_args() -> None:
    args = engine.build_parser().parse_args(
        ["push", "--source", "/tmp/x", "--machine", ".codespaces/foo"]
    )
    assert args.command == "push"
    assert args.source == "/tmp/x"
    assert args.machine == ".codespaces/foo"


def _make_multi_repo_source(root: Path) -> Path:
    """Source with two sessions in different repos (by workspace cwd)."""
    src = root / "copilot"
    a = src / "session-state" / "sess-a"
    a.mkdir(parents=True)
    (a / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (a / "workspace.yaml").write_text("cwd: /home/u/Src/dotfiles\n", encoding="utf-8")
    b = src / "session-state" / "sess-b"
    b.mkdir(parents=True)
    (b / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (b / "workspace.yaml").write_text("cwd: /home/u/Src/other-repo\n", encoding="utf-8")
    (src / "session-store.db").write_text("global", encoding="utf-8")  # must be excluded
    return src


def test_repo_allowlist_filters_sessions(tmp_path: Path) -> None:
    src = _make_multi_repo_source(tmp_path)
    dest = tmp_path / "dest"
    data = dict(load_config(home=tmp_path / "home").as_dict())
    data["sync"]["source"] = str(src)
    data["sync"]["repo_allowlist"] = ["dotfiles"]
    data["sync"]["targets"]["local"]["path"] = str(dest)
    cfg = Config(data, tmp_path / "home")

    assert engine.run_sync(cfg, verbose=True) == 0
    machine_dir = next(dest.iterdir())
    ss = machine_dir / "session-state"
    assert (ss / "sess-a").is_dir()           # dotfiles -> included
    assert not (ss / "sess-b").exists()       # other-repo -> excluded
    # Global session-store.db must NOT leak when filtering.
    assert not (machine_dir / "session-store.db").exists()


def test_allowlist_fail_open_without_workspace(tmp_path: Path) -> None:
    src = tmp_path / "copilot"
    s = src / "session-state" / "no-ws"
    s.mkdir(parents=True)
    (s / "events.jsonl").write_text("{}\n", encoding="utf-8")  # no workspace.yaml
    included = engine._included_sessions(src, ["dotfiles"])
    assert included == {"no-ws"}  # fail-open: kept when repo unknown


def _make_polluted_source(root: Path) -> Path:
    """Source with one session plus non-session ~/.copilot junk and secrets."""
    src = root / "copilot"
    sess = src / "session-state" / "abc-123"
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (src / "session-store.db").write_text("index", encoding="utf-8")
    # Non-session state that must NEVER be archived.
    (src / "installed-plugins").mkdir()
    (src / "installed-plugins" / "binary.exe").write_text("MZ", encoding="utf-8")
    (src / "mcp-oauth-config").mkdir()
    (src / "mcp-oauth-config" / "token.json").write_text("secret", encoding="utf-8")
    (src / "m-encryption-key.enc").write_text("key", encoding="utf-8")
    (src / "settings.json").write_text("{}", encoding="utf-8")
    return src


def test_push_without_allowlist_excludes_non_session_state(tmp_path: Path) -> None:
    """No allowlist must still scope to session data, not the whole ~/.copilot."""
    src = _make_polluted_source(tmp_path)
    dest_root = tmp_path / "dest"
    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")
    assert result.ok

    machine_dir = dest_root / "m1"
    # Session data is archived.
    assert (machine_dir / "session-state" / "abc-123" / "events.jsonl").is_file()
    assert (machine_dir / "session-store.db").is_file()
    # Secrets and binaries are NOT.
    assert not (machine_dir / "installed-plugins").exists()
    assert not (machine_dir / "mcp-oauth-config").exists()
    assert not (machine_dir / "m-encryption-key.enc").exists()
    assert not (machine_dir / "settings.json").exists()
    assert result.file_count == 2  # events.jsonl + session-store.db only


def test_rsync_session_filters_scope_without_allowlist() -> None:
    """The unfiltered rsync filter must scope to session data, not be empty."""
    from agent_logger.sync.targets.base import rsync_session_filters

    unfiltered = rsync_session_filters(None)
    assert "--include=session-state/***" in unfiltered
    assert "--include=session-store.db" in unfiltered
    assert unfiltered[-1] == "--exclude=*"

    filtered = rsync_session_filters({"abc-123"})
    assert "--include=session-state/abc-123/***" in filtered
    # session-store.db is dropped when filtering by repo.
    assert "--include=session-store.db" not in filtered
    assert filtered[-1] == "--exclude=*"


def test_config_repo_allowlist_parsing(tmp_path: Path) -> None:
    base = load_config(home=tmp_path).as_dict()
    data = dict(base)
    data["sync"] = dict(data["sync"], repo_allowlist="dotfiles, example-ai-hub")
    assert Config(data, tmp_path).sync_repo_allowlist == ["dotfiles", "example-ai-hub"]


# ── Post-push notify (target-independent) ────────────────────────────


def _cfg_notify(home, source, dest, *, url, token_file=""):
    data = dict(load_config(home=home).as_dict())
    data["sync"]["source"] = str(source)
    data["sync"]["targets"]["local"]["path"] = str(dest)
    data["sync"]["notify"] = {"url": url, "bearer_token_file": token_file, "timeout": 3}
    return Config(data, home)


def test_notify_helper_posts_json_and_substitutes_machine(monkeypatch, tmp_path):
    from agent_logger.sync import notify as notify_mod

    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["timeout"] = timeout
        captured["auth"] = req.get_header("Authorization")
        return None

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", _fake_urlopen)
    tok = tmp_path / "tok"
    tok.write_text("s3cret", encoding="utf-8")
    ok = notify_mod.post_notify(
        "https://h/api/webhook/x?m={machine}", "lambda-core-wsl",
        bearer_token_file=str(tok), timeout=3,
    )
    assert ok is True
    assert captured["url"] == "https://h/api/webhook/x?m=lambda-core-wsl"
    assert b'"machine": "lambda-core-wsl"' in captured["data"]
    assert captured["auth"] == "Bearer s3cret"
    assert captured["timeout"] == 3


def test_notify_helper_swallows_errors(monkeypatch):
    from agent_logger.sync import notify as notify_mod

    def _boom(req, timeout=None):
        raise OSError("network down")

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", _boom)
    assert notify_mod.post_notify("https://h/x", "m") is False


def test_notify_helper_no_url_is_noop():
    from agent_logger.sync import notify as notify_mod

    assert notify_mod.post_notify("", "m") is False


def test_engine_fires_notify_after_push(monkeypatch, tmp_path):
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg_notify(tmp_path / "home", src, dest, url="https://h/api/webhook/x")
    calls = []
    monkeypatch.setattr(
        engine, "post_notify",
        lambda url, machine, **kw: calls.append((url, machine, kw)) or True,
    )
    assert engine.run_sync(cfg, verbose=True) == 0
    assert len(calls) == 1
    assert calls[0][0] == "https://h/api/webhook/x"
    assert calls[0][1]  # machine resolved (non-empty)


def test_engine_no_notify_without_url(monkeypatch, tmp_path):
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)  # default: no notify url
    calls = []
    monkeypatch.setattr(engine, "post_notify", lambda *a, **k: calls.append(a) or True)
    assert engine.run_sync(cfg) == 0
    assert calls == []
