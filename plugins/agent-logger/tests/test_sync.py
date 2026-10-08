"""Tests for the session-sync engine and targets."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_logger.config import Config, load_config
from agent_logger.sync import engine
from agent_logger.sync.lock import sync_lock
from agent_logger.sync.origin import effective_harness as origin_effective
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
    (sess / "LOCK").write_text("volatile", encoding="utf-8")
    return src


def _add_chromium_profile(session: Path, name: str = "browser-run") -> Path:
    profile = session / "files" / "tool-output" / name
    default = profile / "Default"
    network = default / "Network"
    network.mkdir(parents=True)
    (profile / "Local State").write_text("{}", encoding="utf-8")
    (default / "Preferences").write_text("{}", encoding="utf-8")
    (network / "Cookies").write_bytes(b"cookies")
    (profile / "component_crx_cache").mkdir()
    (profile / "component_crx_cache" / "large").write_bytes(b"x" * 1024)
    return profile


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
    projection = src / "session-state" / "abc-123" / "agent-worktrees.json"
    projection.write_bytes(b"{opaque-future-or-malformed-projection")
    (src / "session-state" / "abc-123" / "review-annotations.json.lock").write_text("1")
    (src / "session-state" / "abc-123" / "review-annotations.json.abc123.tmp").write_text("[]")
    (src / "session-state" / "abc-123" / "inuse.4242.hold").write_bytes(b"")
    (src / "session-state" / "abc-123" / "inuse.4242.lock").write_text("4242")
    dest_root = tmp_path / "dest"
    target = LocalTarget({"path": str(dest_root)})

    result = target.push(src, "m1")
    assert result.ok
    assert result.file_count == 3
    machine_dir = dest_root / "m1"
    assert (machine_dir / "session-state" / "abc-123" / "events.jsonl").is_file()
    assert (
        machine_dir / "session-state" / "abc-123" / "agent-worktrees.json"
    ).read_bytes() == projection.read_bytes()
    assert not (machine_dir / "session-state" / "abc-123" / ".lock").exists()
    assert not (machine_dir / "session-state" / "abc-123" / "LOCK").exists()
    assert not (
        machine_dir / "session-state" / "abc-123" / "review-annotations.json.lock"
    ).exists()
    assert not (
        machine_dir / "session-state" / "abc-123" / "review-annotations.json.abc123.tmp"
    ).exists()
    assert not (
        machine_dir / "session-state" / "abc-123" / "inuse.4242.hold"
    ).exists()
    assert not (
        machine_dir / "session-state" / "abc-123" / "inuse.4242.lock"
    ).exists()
    assert (machine_dir / "sync-meta.json").is_file()


def test_local_target_push_excludes_hold_marker_without_opening_it(
    monkeypatch, tmp_path: Path
) -> None:
    """Regression test for a live Copilot session's ``inuse.<pid>.hold`` marker.

    Unlike a locked browser database (a Windows sharing violation, deferred as
    a retryable partial), a ``.hold`` marker can raise a plain
    ``PermissionError`` with no ``winerror`` at all -- previously unrecognized
    by ``_is_windows_sharing_violation`` and left to abort the entire push
    rather than being skipped. It must never even be opened.
    """
    from agent_logger.sync.targets import filesystem

    src = _make_source(tmp_path)
    (src / "session-state" / "abc-123" / "inuse.4242.hold").write_bytes(b"")
    original = filesystem.open_regular_no_follow

    def fail_if_opened(source: Path):
        if source.name == "inuse.4242.hold":
            raise PermissionError(13, "Permission denied", str(source))
        return original(source)

    monkeypatch.setattr(filesystem, "open_regular_no_follow", fail_if_opened)
    target = LocalTarget({"path": str(tmp_path / "dest")})

    result = target.push(src, "m1")

    assert result.ok, result.detail
    assert not (
        tmp_path / "dest" / "m1" / "session-state" / "abc-123" / "inuse.4242.hold"
    ).exists()



def test_local_target_excludes_and_removes_chromium_profile(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path)
    session = src / "session-state" / "abc-123"
    profile = _add_chromium_profile(session)
    lookalike = session / "files" / "lookalike"
    (lookalike / "Default").mkdir(parents=True)
    (lookalike / "Local State").write_text("keep", encoding="utf-8")
    dest_root = tmp_path / "dest"
    stale_profile = (
        dest_root
        / "m1"
        / profile.relative_to(src)
    )
    stale_profile.mkdir(parents=True)
    (stale_profile / "stale.bin").write_bytes(b"stale")

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert result.ok
    assert result.excluded_file_count == 4
    assert result.excluded_byte_count == 1035
    assert result.excluded_roots == (
        str(profile.relative_to(src)),
    )
    machine = dest_root / "m1"
    assert not (machine / profile.relative_to(src)).exists()
    assert (
        machine / lookalike.relative_to(src) / "Local State"
    ).read_text(encoding="utf-8") == "keep"
    metadata = json.loads((machine / "sync-meta.json").read_text())
    assert metadata["excluded_detritus_root_count"] == 1
    assert metadata["excluded_detritus_file_count"] == 4
    assert metadata["excluded_detritus_byte_count"] == 1035
    assert metadata["excluded_detritus_measurement_complete"] is True
    assert metadata["excluded_detritus_roots"] == [
        str(profile.relative_to(src))
    ]


def test_local_target_removes_destination_only_chromium_profile(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    destination_session = dest_root / "m1" / "session-state" / "old"
    profile = _add_chromium_profile(destination_session)

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert result.ok
    assert not profile.exists()
    assert (src / "session-state" / "abc-123" / "events.jsonl").is_file()


def test_chromium_signature_requires_expected_names_and_types(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path)
    files = src / "session-state" / "abc-123" / "files"
    bad_name = files / "bad-name"
    (bad_name / "Profile backup" / "Network").mkdir(parents=True)
    (bad_name / "Local State").write_text("keep", encoding="utf-8")
    (bad_name / "Profile backup" / "Preferences").write_text(
        "keep",
        encoding="utf-8",
    )
    bad_types = files / "bad-types"
    (bad_types / "Local State").mkdir(parents=True)
    (bad_types / "Default" / "Preferences").mkdir(parents=True)
    (bad_types / "Default" / "Network").write_text("keep", encoding="utf-8")
    dest_root = tmp_path / "dest"

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert result.ok
    published = dest_root / "m1" / "session-state" / "abc-123" / "files"
    assert (published / "bad-name" / "Local State").is_file()
    assert (published / "bad-types" / "Default" / "Network").is_file()
    assert result.excluded_roots == ()


def test_local_target_excludes_venv_git_clone_and_node_modules(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path)
    session = src / "session-state" / "abc-123"
    files = session / "files"

    venv = files / "install-probe"
    certifi = venv / "Lib" / "site-packages" / "pip" / "_vendor" / "certifi"
    certifi.mkdir(parents=True)
    (certifi / "cacert.pem").write_bytes(b"-----BEGIN CERTIFICATE-----")
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")

    clone = files / "harness-clone"
    (clone / ".git").mkdir(parents=True)
    (clone / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (clone / "README.md").write_text("hello\n", encoding="utf-8")

    modules = files / "tool" / "node_modules"
    pkg = modules / "left-pad"
    pkg.mkdir(parents=True)
    (pkg / "index.js").write_text("module.exports = {}\n", encoding="utf-8")

    dest_root = tmp_path / "dest"

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert result.ok
    published = dest_root / "m1" / "session-state" / "abc-123" / "files"
    assert not (published / "install-probe").exists()
    assert not (published / "harness-clone").exists()
    assert not (published / "tool" / "node_modules").exists()
    assert set(result.excluded_roots) == {
        str(Path("session-state/abc-123/files/install-probe")),
        str(Path("session-state/abc-123/files/harness-clone")),
        str(Path("session-state/abc-123/files/tool/node_modules")),
    }


def test_detritus_measurement_race_does_not_block_exclusion(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import detritus

    src = _make_source(tmp_path)
    session = src / "session-state" / "abc-123"
    profile = _add_chromium_profile(session)
    real_scan = detritus._scan_entries

    def fail_cache(path: Path):
        if path.name == "component_crx_cache":
            raise FileNotFoundError(path)
        return real_scan(path)

    monkeypatch.setattr(detritus, "_scan_entries", fail_cache)
    dest_root = tmp_path / "dest"

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert result.ok
    assert result.excluded_roots == (str(profile.relative_to(src)),)
    assert result.excluded_measurement_complete is False
    assert not (dest_root / "m1" / profile.relative_to(src)).exists()


def test_new_detritus_during_copy_is_removed_and_retried(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import detritus
    from agent_logger.sync.targets import filesystem

    src = _make_source(tmp_path)
    session = src / "session-state" / "abc-123"
    profile = _add_chromium_profile(session)
    actual = detritus.discover_session_detritus(src, None)
    calls = 0

    def discover(_source: Path, _included):
        nonlocal calls
        calls += 1
        return detritus.DetritusSummary() if calls == 1 else actual

    monkeypatch.setattr(filesystem, "discover_session_detritus", discover)
    dest_root = tmp_path / "dest"

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert not result.ok
    assert "changed during publication" in result.detail
    assert not (dest_root / "m1" / profile.relative_to(src)).exists()


def test_detritus_cleanup_rejects_symlinked_destination_ancestor(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path)
    session = src / "session-state" / "abc-123"
    profile = _add_chromium_profile(session)
    dest_root = tmp_path / "dest"
    outside = tmp_path / "outside"
    outside_profile = outside / profile.name
    outside_profile.mkdir(parents=True)
    marker = outside_profile / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    linked_parent = (
        dest_root
        / "m1"
        / "session-state"
        / "abc-123"
        / "files"
        / "tool-output"
    )
    linked_parent.parent.mkdir(parents=True)
    try:
        linked_parent.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is unavailable")

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert not result.ok
    assert "detritus cleanup failed" in result.detail
    assert marker.read_text(encoding="utf-8") == "keep"


def test_selected_session_replacement_removes_chromium_profile(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path)
    session = src / "session-state" / "abc-123"
    profile = _add_chromium_profile(session)
    dest_root = tmp_path / "dest"
    destination_profile = dest_root / "m1" / profile.relative_to(src)
    destination_profile.mkdir(parents=True)
    (destination_profile / "stale.bin").write_bytes(b"stale")

    result = LocalTarget({"path": str(dest_root)}).push(
        src,
        "m1",
        {"abc-123"},
    )

    assert result.ok
    assert not destination_profile.exists()
    assert (
        dest_root / "m1" / "session-state" / "abc-123" / "events.jsonl"
    ).is_file()


def test_selected_session_retries_when_detritus_appears(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import detritus
    from agent_logger.sync.targets import filesystem

    src = _make_source(tmp_path)
    session = src / "session-state" / "abc-123"
    profile = _add_chromium_profile(session)
    actual = detritus.discover_session_detritus(src, {"abc-123"})
    calls = 0

    def discover(_source: Path, _included):
        nonlocal calls
        calls += 1
        return detritus.DetritusSummary() if calls == 1 else actual

    monkeypatch.setattr(filesystem, "discover_session_detritus", discover)
    dest_root = tmp_path / "dest"

    result = LocalTarget({"path": str(dest_root)}).push(
        src,
        "m1",
        {"abc-123"},
    )

    assert result.ok, result.detail
    assert not (dest_root / "m1" / profile.relative_to(src)).exists()
    assert calls >= 3


def test_selected_session_migrates_legacy_rescue_snapshot_detritus(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import detritus, provenance
    from agent_logger.sync.targets import filesystem

    src = _make_source(tmp_path)
    session = src / "session-state" / "abc-123"
    profile = _add_chromium_profile(session)
    provenance_dir = src / "provenance"
    provenance_dir.mkdir()
    (provenance_dir / "abc-123.json").write_text(
        json.dumps(
            {
                "provider": "agent-containers",
                "session_id": "abc-123",
                "capture_id": "capture-1",
                "captured_at": "2026-09-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    dest_root = tmp_path / "dest"
    real_discover = filesystem.discover_session_detritus
    monkeypatch.setattr(
        filesystem,
        "discover_session_detritus",
        lambda _source, _included: detritus.DetritusSummary(),
    )
    first = LocalTarget({"path": str(dest_root)}).push(
        src,
        "m1",
        {"abc-123"},
    )
    assert first.ok
    snapshot = provenance.rescue_snapshot_path(
        dest_root / "m1",
        "abc-123",
        "capture-1",
    )
    snapshot_profile = snapshot / profile.relative_to(session)
    assert os.path.isdir(provenance._windows_extended_path(snapshot_profile))

    monkeypatch.setattr(filesystem, "discover_session_detritus", real_discover)
    second = LocalTarget({"path": str(dest_root)}).push(
        src,
        "m1",
        {"abc-123"},
    )

    assert second.ok, second.detail
    assert not os.path.exists(provenance._windows_extended_path(snapshot_profile))
    assert os.path.isfile(
        provenance._windows_extended_path(snapshot / "events.jsonl")
    )


def test_local_target_push_is_incremental(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    target = LocalTarget({"path": str(tmp_path / "dest")})
    target.push(src, "m1")
    # Nothing changed -> second push copies zero files.
    second = target.push(src, "m1")
    assert second.ok
    assert second.file_count == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing violation behavior")
def test_local_target_push_defers_locked_files(monkeypatch, tmp_path: Path) -> None:
    from agent_logger.sync.targets import filesystem

    src = _make_source(tmp_path)
    target = LocalTarget({"path": str(tmp_path / "dest")})
    original = filesystem.open_regular_no_follow

    def open_unless_locked(source: Path):
        if source.name == "events.jsonl":
            raise PermissionError(
                13,
                "The process cannot access the file",
                str(source),
                32,
            )
        return original(source)

    monkeypatch.setattr(filesystem, "open_regular_no_follow", open_unless_locked)

    result = target.push(src, "m1")

    machine_dir = tmp_path / "dest" / "m1"
    assert result.ok
    assert "skipped 1 locked file(s), will retry" in result.detail
    assert not (
        machine_dir / "session-state" / "abc-123" / "events.jsonl"
    ).exists()
    assert (
        machine_dir / "session-state" / "abc-123" / "workspace.yaml"
    ).is_file()
    metadata = json.loads((machine_dir / "sync-meta.json").read_text())
    assert metadata["status"] == "partial"
    assert metadata["deferred_file_count"] == 1
    assert metadata["deferred_files"] == [
        str(Path("session-state") / "abc-123" / "events.jsonl")
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing violation behavior")
def test_windows_sharing_violation_accepts_plain_oserror() -> None:
    from agent_logger.sync.targets import filesystem

    error = OSError("sharing violation")
    error.winerror = 32

    assert filesystem._is_windows_sharing_violation(error)


@pytest.mark.skipif(os.name != "nt", reason="Windows path namespace behavior")
def test_windows_extended_path_formats_drive_and_unc_paths() -> None:
    from agent_logger.sync import provenance

    drive = Path(r"C:\example\session.json")
    unc = Path(r"\\server\share\session.json")
    extended = Path(r"\\?\C:\example\session.json")

    assert provenance.windows_extended_path(drive) == (
        r"\\?\C:\example\session.json"
    )
    assert provenance.windows_extended_path(unc) == (
        r"\\?\UNC\server\share\session.json"
    )
    assert provenance.windows_extended_path(extended) == str(extended)


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
def test_local_target_push_handles_long_temporary_path(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest_root = tmp_path / ("d" * 48)
    source_parent = src / "session-state" / "abc-123" / "files"
    destination_parent = (
        dest_root / "m1" / "session-state" / "abc-123" / "files"
    )
    destination = destination_parent / "description"
    legacy_temporary = destination.with_name(
        f".{destination.name}.{'f' * 32}.tmp"
    )
    if len(str(destination)) >= 260:
        pytest.skip("temporary test root already exceeds MAX_PATH")
    while len(str(legacy_temporary)) < 260:
        source_parent /= "x"
        destination_parent /= "x"
        destination = destination_parent / "description"
        legacy_temporary = destination.with_name(
            f".{destination.name}.{'f' * 32}.tmp"
        )

    assert len(str(destination)) < 260
    assert len(str(legacy_temporary)) >= 260
    source_parent.mkdir(parents=True)
    (source_parent / "description").write_text("long path", encoding="utf-8")

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert result.ok, result.detail
    assert destination.read_text(encoding="utf-8") == "long path"


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
def test_local_target_push_handles_long_source_and_destination_paths(
    tmp_path: Path,
) -> None:
    from agent_logger.sync import provenance

    src = _make_source(tmp_path)
    dest_root = tmp_path / ("d" * 48)
    source_parent = src / "session-state" / "abc-123" / "files"
    destination_parent = (
        dest_root / "m1" / "session-state" / "abc-123" / "files"
    )
    while len(str(source_parent)) < 260:
        source_parent /= "x"
        destination_parent /= "x"

    source = source_parent / "events.jsonl"
    destination = destination_parent / "events.jsonl"
    os.makedirs(provenance._windows_extended_path(source_parent), exist_ok=True)
    with open(
        provenance._windows_extended_path(source),
        "w",
        encoding="utf-8",
    ) as f:
        f.write("long paths")

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert result.ok, result.detail
    with open(provenance._windows_extended_path(destination), encoding="utf-8") as f:
        assert f.read() == "long paths"


@pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH regression")
def test_filtered_push_handles_long_source_and_staging_paths(
    tmp_path: Path,
) -> None:
    from agent_logger.sync import provenance

    src = _make_source(tmp_path)
    dest_root = tmp_path / ("d" * 48)
    source_parent = src / "session-state" / "abc-123" / "files"
    destination_parent = (
        dest_root / "m1" / "session-state" / "abc-123" / "files"
    )
    while len(str(source_parent)) < 260:
        source_parent /= "x"
        destination_parent /= "x"

    source = source_parent / "events.jsonl"
    destination = destination_parent / "events.jsonl"
    os.makedirs(provenance._windows_extended_path(source_parent), exist_ok=True)
    with open(
        provenance._windows_extended_path(source),
        "w",
        encoding="utf-8",
    ) as f:
        f.write("selected long paths")
    provenance_dir = src / "provenance"
    provenance_dir.mkdir()
    source_provenance = provenance_dir / "abc-123.json"
    source_provenance.write_text(
        json.dumps(
            {
                "provider": "agent-containers",
                "session_id": "abc-123",
                "capture_id": "capture-1",
                "captured_at": "2026-09-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = LocalTarget({"path": str(dest_root)}).push(
        src,
        "m1",
        {"abc-123"},
    )

    assert result.ok, result.detail
    with open(provenance._windows_extended_path(destination), encoding="utf-8") as f:
        assert f.read() == "selected long paths"
    snapshot = provenance.rescue_snapshot_path(
        dest_root / "m1",
        "abc-123",
        "capture-1",
    )
    snapshot_file = snapshot / destination.relative_to(
        dest_root / "m1" / "session-state" / "abc-123"
    )
    with open(
        provenance._windows_extended_path(snapshot_file),
        encoding="utf-8",
    ) as f:
        assert f.read() == "selected long paths"
    assert result.file_count == 8
    assert not (
        dest_root / "m1" / ".session-sync-replacement"
    ).exists()


def test_filtered_local_target_push_is_incremental(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    target = LocalTarget({"path": str(tmp_path / "dest")})

    first = target.push(src, "m1", {"abc-123"})
    second = target.push(src, "m1", {"abc-123"})

    assert first.ok
    assert first.file_count == 2
    assert second.ok
    assert second.file_count == 0


def test_filtered_push_omits_session_symlinks(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    session = src / "session-state" / "abc-123"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        (session / "linked-dir").symlink_to(outside, target_is_directory=True)
        (session / "linked-file").symlink_to(outside / "secret.txt")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    dest_root = tmp_path / "dest"
    result = LocalTarget({"path": str(dest_root)}).push(
        src,
        "m1",
        {"abc-123"},
    )

    assert result.ok
    published = dest_root / "m1" / "session-state" / "abc-123"
    assert (published / "events.jsonl").is_file()
    assert not (published / "linked-dir").exists()
    assert not (published / "linked-file").exists()
    assert not (published / "linked-dir").is_symlink()
    assert not (published / "linked-file").is_symlink()


@pytest.mark.parametrize("linked_parent", ["session-state", "provenance"])
def test_filtered_push_rejects_symlinked_destination_parent(
    tmp_path: Path,
    linked_parent: str,
) -> None:
    src = _make_source(tmp_path)
    provenance = src / "provenance"
    provenance.mkdir()
    (provenance / "abc-123.json").write_text("{}\n", encoding="utf-8")
    dest_root = tmp_path / "dest"
    machine = dest_root / "m1"
    machine.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (machine / linked_parent).symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = LocalTarget({"path": str(dest_root)}).push(
        src,
        "m1",
        {"abc-123"},
    )

    assert not result.ok
    assert list(outside.iterdir()) == []


def test_push_rejects_symlinked_configured_root_ancestor(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    target = LocalTarget({"path": str(alias / "nested")})

    result = target.push(src, "m1")

    assert not result.ok
    assert "destination directory is unsafe" in result.detail
    assert list(outside.iterdir()) == []


def test_push_rejects_windows_root_relative_machine_label(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    target = LocalTarget({"path": str(dest_root)})

    result = target.push(src, r"\outside")

    assert not result.ok
    assert "unsafe destination path" in result.detail
    assert not dest_root.exists()


def test_filtered_push_rejects_linked_replacement_root(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    machine = dest_root / "m1"
    machine.mkdir(parents=True)
    outside = tmp_path / "outside-replacement"
    outside.mkdir()
    (outside / "stale.cleanup").mkdir()
    replacement = machine / ".session-sync-replacement"
    try:
        replacement.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    target = LocalTarget({"path": str(dest_root)})

    result = target.push(src, "m1", {"abc-123"})

    assert not result.ok
    assert (outside / "stale.cleanup").is_dir()


def test_recursive_cleanup_refuses_link(tmp_path: Path) -> None:
    from agent_logger.sync.targets import filesystem

    outside = tmp_path / "outside-cleanup"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    linked = tmp_path / "linked-cleanup"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(OSError, match="reparse point"):
        filesystem._remove_path_checked(linked)
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only directory behavior")
def test_recursive_cleanup_removes_read_only_directories(tmp_path: Path) -> None:
    import ctypes

    from agent_logger.sync import provenance
    from agent_logger.sync.targets import filesystem

    root = tmp_path / "read-only-tree"
    child = root / "child"
    child.mkdir(parents=True)
    (child / "data.txt").write_text("data", encoding="utf-8")
    set_attributes = ctypes.WinDLL("kernel32", use_last_error=True).SetFileAttributesW
    set_attributes.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    set_attributes.restype = ctypes.c_int
    assert set_attributes(provenance._windows_extended_path(child), 0x1)
    try:
        filesystem._remove_tree_checked(root)
    finally:
        if child.exists():
            set_attributes(provenance._windows_extended_path(child), 0x80)

    assert not root.exists()


def test_recovery_manifest_cannot_target_machine_root(tmp_path: Path) -> None:
    from agent_logger.sync.targets import filesystem

    dest = tmp_path / "machine"
    replacement = dest / ".session-sync-replacement"
    transaction = replacement / "transaction.active"
    transaction.mkdir(parents=True)
    sentinel = dest / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    (transaction / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "staged": "new/item",
                        "destination": ".",
                        "backup": "old/item",
                        "had_destination": False,
                        "destination_fingerprint": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="strict descendants"):
        filesystem._recover_active_transactions(replacement, dest)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_completed_rollback_advances_generation_epoch(tmp_path: Path) -> None:
    from agent_logger.sync.targets import filesystem

    dest = tmp_path / "machine"
    replacement = dest / ".session-sync-replacement"
    transaction = replacement / "transaction.active"
    backup = transaction / "old" / "session-state" / "one"
    backup.mkdir(parents=True)
    (backup / "events.jsonl").write_text("old\n", encoding="utf-8")
    current = dest / "session-state" / "one"
    current.mkdir(parents=True)
    (current / "events.jsonl").write_text("new\n", encoding="utf-8")
    old_fingerprint = filesystem._path_fingerprint(backup)
    (dest / ".session-sync-generation").write_text(
        "previous.active",
        encoding="ascii",
    )
    (transaction / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "staged": "new/session-state/one",
                        "destination": "session-state/one",
                        "backup": "old/session-state/one",
                        "had_destination": True,
                        "destination_fingerprint": old_fingerprint,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    filesystem._recover_active_transactions(replacement, dest)

    assert (current / "events.jsonl").read_text(encoding="utf-8") == "old\n"
    assert (dest / ".session-sync-generation").read_text(
        encoding="ascii"
    ) == "transaction.active.rolled-back"
    assert not transaction.exists()


def test_recovery_rejects_linked_backup_ancestor(tmp_path: Path) -> None:
    from agent_logger.sync.targets import filesystem

    dest = tmp_path / "machine"
    transaction = dest / ".session-sync-replacement" / "transaction.active"
    transaction.mkdir(parents=True)
    outside = tmp_path / "outside-backup"
    external = outside / "session-state" / "one"
    external.mkdir(parents=True)
    (external / "events.jsonl").write_text("external\n", encoding="utf-8")
    try:
        (transaction / "old").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    (transaction / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {
                        "staged": "new/session-state/one",
                        "destination": "session-state/one",
                        "backup": "old/session-state/one",
                        "had_destination": True,
                        "destination_fingerprint": "0" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OSError, match="unsafe"):
        filesystem._recover_active_transactions(transaction.parent, dest)
    assert (external / "events.jsonl").read_text(encoding="utf-8") == "external\n"


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
    provenance = dest_root / "m1" / "provenance" / "abc-123.json"
    provenance.parent.mkdir()
    provenance.write_text("{}\n", encoding="utf-8")

    old = time.time() - 40 * 86400
    sess = dest_root / "m1" / "session-state" / "abc-123"
    for f in sess.rglob("*"):
        os.utime(f, (old, old))

    assert target.prune("m1", 30) == 1
    assert not sess.exists()
    assert not provenance.exists()
    # Retention disabled -> no-op.
    assert target.prune("m1", None) == 0


def test_local_target_prune_rejects_unsafe_machine_path(tmp_path: Path) -> None:
    dest_root = tmp_path / "dest"
    outside = tmp_path / "outside"
    (outside / "session-state").mkdir(parents=True)
    try:
        (dest_root / "linked").parent.mkdir(parents=True)
        (dest_root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    target = LocalTarget({"path": str(dest_root)})

    with pytest.raises(OSError, match="unsafe"):
        target.prune("linked", 30)
    with pytest.raises(OSError, match="unsafe destination path"):
        target.prune("../outside", 30)


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


def test_ssh_executable_prefers_sibling_of_rsync_on_windows(monkeypatch, tmp_path: Path):
    """An MSYS2/Cygwin-runtime rsync.exe (the only rsync distribution
    generally available on Windows) that spawns a *different-runtime* ssh
    for its own `-e ssh` child -- e.g. the native Win32 OpenSSH client --
    corrupts the rsync protocol handshake across that runtime boundary
    (reproduced live: the ssh session itself completes and exchanges bytes,
    but rsync reports "connection unexpectedly closed (0 bytes received so
    far)"). A sibling ssh binary in the same directory as the resolved
    rsync shares its runtime, so it must be preferred."""
    from agent_logger.sync.targets import ssh

    rsync_dir = tmp_path / "msys64" / "usr" / "bin"
    rsync_dir.mkdir(parents=True)
    rsync_exe = rsync_dir / "rsync.exe"
    rsync_exe.write_text("", encoding="utf-8")
    ssh_exe = rsync_dir / "ssh.exe"
    ssh_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(ssh, "_IS_WINDOWS", True)
    monkeypatch.setattr(ssh.shutil, "which", lambda _name: str(rsync_exe))
    assert ssh._ssh_executable() == str(ssh_exe)


def test_ssh_executable_falls_back_without_sibling(monkeypatch, tmp_path: Path):
    from agent_logger.sync.targets import ssh

    rsync_dir = tmp_path / "some-other-rsync-dist"
    rsync_dir.mkdir(parents=True)
    rsync_exe = rsync_dir / "rsync.exe"
    rsync_exe.write_text("", encoding="utf-8")
    # No sibling ssh.exe written here.

    monkeypatch.setattr(ssh, "_IS_WINDOWS", True)
    monkeypatch.setattr(ssh.shutil, "which", lambda _name: str(rsync_exe))
    assert ssh._ssh_executable() == "ssh"


def test_ssh_executable_is_plain_ssh_on_posix(monkeypatch, tmp_path: Path):
    from agent_logger.sync.targets import ssh

    rsync_dir = tmp_path / "usr" / "bin"
    rsync_dir.mkdir(parents=True)
    (rsync_dir / "rsync").write_text("", encoding="utf-8")
    (rsync_dir / "ssh").write_text("", encoding="utf-8")

    monkeypatch.setattr(ssh, "_IS_WINDOWS", False)
    monkeypatch.setattr(ssh.shutil, "which", lambda _name: str(rsync_dir / "rsync"))
    assert ssh._ssh_executable() == "ssh"


def test_ssh_target_push_uses_sibling_ssh_in_command(monkeypatch, tmp_path: Path):
    """End-to-end: push()'s constructed rsync -e command must carry the
    resolved sibling ssh path, not a bare "ssh"."""
    from agent_logger.sync.targets import base as sync_base
    from agent_logger.sync.targets import ssh as ssh_mod

    bin_dir = tmp_path / "msys64" / "usr" / "bin"
    bin_dir.mkdir(parents=True)
    rsync_exe = bin_dir / "rsync.exe"
    rsync_exe.write_text("", encoding="utf-8")
    ssh_exe = bin_dir / "ssh.exe"
    ssh_exe.write_text("", encoding="utf-8")
    source = _make_source(tmp_path / "home")

    monkeypatch.setattr(ssh_mod, "_IS_WINDOWS", True)
    monkeypatch.setattr(ssh_mod.shutil, "which", lambda _name: str(rsync_exe))
    # This exercises the native-Windows (no WSL) sibling-ssh fallback
    # specifically -- disable the WSL-preferred path so the test is
    # deterministic regardless of whether the test host actually has WSL.
    monkeypatch.setattr(sync_base, "wsl_rsync_available", lambda **_kwargs: False)

    captured_commands: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        return _Proc()

    monkeypatch.setattr(ssh_mod.subprocess, "run", _fake_run)
    SshTarget({"host": "user@example", "remote_path": "/srv"}).push(source, "m1")

    ssh_arg_index = captured_commands[-1].index("-e") + 1
    # Compare against the quoted form, not the bare path: push() quotes
    # whenever the (test-generated, potentially space-containing) tmp_path
    # happens to contain a space or apostrophe -- independent of the
    # temporary directory's own name.
    from agent_logger.sync.targets.ssh import _quote_executable

    assert captured_commands[-1][ssh_arg_index].startswith(
        _quote_executable(str(ssh_exe))
    )


def test_ssh_executable_quoting_handles_spaces_in_path():
    from agent_logger.sync.targets.ssh import _quote_executable

    assert _quote_executable("ssh") == "ssh"
    assert _quote_executable(r"C:\no-spaces\ssh.exe") == r"C:\no-spaces\ssh.exe"
    quoted = _quote_executable(r"C:\Program Files\msys64\usr\bin\ssh.exe")
    assert quoted == r'"C:\Program Files\msys64\usr\bin\ssh.exe"'


def test_ssh_executable_quoting_handles_apostrophe_in_path():
    """An apostrophe needs quoting even with no space: rsync's `-e` parser
    treats it as an opening quote and rejects the command for having no
    closing one."""
    from agent_logger.sync.targets.ssh import _quote_executable

    path = r"C:\Users\O'Brien\msys64\usr\bin\ssh.exe"
    assert _quote_executable(path) == f'"{path}"'



def test_ssh_target_push_quotes_sibling_path_with_spaces(monkeypatch, tmp_path: Path):
    """The constructed `-e` command string must quote a sibling ssh path
    that contains a space (rsync re-splits that string on whitespace to
    build the command it execs)."""
    from agent_logger.sync.targets import base as sync_base
    from agent_logger.sync.targets import ssh as ssh_mod

    bin_dir = tmp_path / "Program Files" / "msys64" / "usr" / "bin"
    bin_dir.mkdir(parents=True)
    rsync_exe = bin_dir / "rsync.exe"
    rsync_exe.write_text("", encoding="utf-8")
    ssh_exe = bin_dir / "ssh.exe"
    ssh_exe.write_text("", encoding="utf-8")
    source = _make_source(tmp_path / "home")

    monkeypatch.setattr(ssh_mod, "_IS_WINDOWS", True)
    monkeypatch.setattr(ssh_mod.shutil, "which", lambda _name: str(rsync_exe))
    monkeypatch.setattr(sync_base, "wsl_rsync_available", lambda **_kwargs: False)

    captured_commands: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        return _Proc()

    monkeypatch.setattr(ssh_mod.subprocess, "run", _fake_run)
    SshTarget({"host": "user@example", "remote_path": "/srv"}).push(source, "m1")

    ssh_arg_index = captured_commands[-1].index("-e") + 1
    assert captured_commands[-1][ssh_arg_index].startswith(f'"{ssh_exe}"')


def test_rsync_children_suppress_console_window(monkeypatch, tmp_path: Path) -> None:
    """ssh/ingest pushes must pass the windowless kwargs to their rsync child.

    Regression guard: on Windows a child rsync/ssh process launched from a
    windowless host flashes a console unless CREATE_NO_WINDOW is set. The kwargs
    are a no-op on POSIX, so this asserts they are forwarded verbatim.
    """
    from agent_logger.sync.targets import base, ingest, ssh

    captured: dict = {}
    captured_commands: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured.clear()
        captured.update(kwargs)
        captured_commands.append(cmd)
        return _Proc()

    monkeypatch.setattr("shutil.which", lambda _name: "rsync")
    # Keep this test's NO_WINDOW_KWARGS/filter-arg assertions independent of
    # whether the test host actually has WSL -- it exercises the native
    # rsync invocation, not the WSL-wrapped one (covered separately).
    monkeypatch.setattr(base, "wsl_rsync_available", lambda **_kwargs: False)

    monkeypatch.setattr(ssh.subprocess, "run", _fake_run)
    SshTarget({"host": "user@example", "remote_path": "/srv"}).push(
        tmp_path, "m1", {"abc-123"}
    )
    for key, val in base.NO_WINDOW_KWARGS.items():
        assert captured.get(key) == val
    assert "--include=provenance/abc-123.json" in captured_commands[-1]
    assert "--exclude=*.hold" in captured_commands[-1]
    assert "--delete-excluded" not in captured_commands[-1]

    monkeypatch.setattr(ingest.subprocess, "run", _fake_run)
    IngestTarget({"url": "rsync://h/mod"}).push(tmp_path, "m1", {"abc-123"})
    for key, val in base.NO_WINDOW_KWARGS.items():
        assert captured.get(key) == val
    assert "--include=provenance/abc-123.json" in captured_commands[-1]
    assert "--exclude=*.hold" in captured_commands[-1]
    assert "--delete-excluded" not in captured_commands[-1]


def test_wsl_rsync_available_false_on_posix(monkeypatch) -> None:
    from agent_logger.sync.targets import base

    monkeypatch.setattr(base, "_IS_WINDOWS", False)
    assert base.wsl_rsync_available() is False


def test_wsl_rsync_available_false_without_wsl_exe(monkeypatch) -> None:
    from agent_logger.sync.targets import base

    monkeypatch.setattr(base, "_IS_WINDOWS", True)
    monkeypatch.setattr(base.shutil, "which", lambda _name: None)
    assert base.wsl_rsync_available() is False


def test_wsl_rsync_available_true_when_probe_succeeds(monkeypatch) -> None:
    from agent_logger.sync.targets import base

    monkeypatch.setattr(base, "_IS_WINDOWS", True)
    monkeypatch.setattr(base.shutil, "which", lambda _name: r"C:\Windows\System32\wsl.exe")

    class _Proc:
        returncode = 0

    monkeypatch.setattr(base.subprocess, "run", lambda *a, **k: _Proc())
    assert base.wsl_rsync_available() is True


def test_wsl_rsync_available_false_when_probe_fails(monkeypatch) -> None:
    """wsl.exe exists but has no distro with both rsync and ssh on PATH."""
    from agent_logger.sync.targets import base

    monkeypatch.setattr(base, "_IS_WINDOWS", True)
    monkeypatch.setattr(base.shutil, "which", lambda _name: r"C:\Windows\System32\wsl.exe")

    class _Proc:
        returncode = 1

    monkeypatch.setattr(base.subprocess, "run", lambda *a, **k: _Proc())
    assert base.wsl_rsync_available() is False


def test_wsl_rsync_available_require_ssh_false_only_checks_rsync(monkeypatch) -> None:
    """ingest speaks rsync's daemon protocol directly and never shells out to
    ssh -- a WSL distro with rsync but no ssh client must still count."""
    from agent_logger.sync.targets import base

    monkeypatch.setattr(base, "_IS_WINDOWS", True)
    monkeypatch.setattr(base.shutil, "which", lambda _name: r"C:\Windows\System32\wsl.exe")

    captured_checks: list[str] = []

    class _Proc:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured_checks.append(cmd[-1])
        return _Proc()

    monkeypatch.setattr(base.subprocess, "run", _fake_run)
    assert base.wsl_rsync_available(require_ssh=False) is True
    assert "ssh" not in captured_checks[-1]
    assert "rsync" in captured_checks[-1]


def test_resolve_rsync_runtime_prefers_wsl_when_available(monkeypatch) -> None:
    from agent_logger.sync.targets import base

    monkeypatch.setattr(base, "wsl_rsync_available", lambda **_kwargs: True)
    runtime = base.resolve_rsync_runtime()
    assert runtime.use_wsl is True
    assert runtime.command_prefix == ["wsl.exe", "-e"]


def test_resolve_rsync_runtime_native_without_wsl(monkeypatch) -> None:
    from agent_logger.sync.targets import base

    monkeypatch.setattr(base, "wsl_rsync_available", lambda **_kwargs: False)
    runtime = base.resolve_rsync_runtime()
    assert runtime.use_wsl is False
    assert runtime.command_prefix == []


def test_rsync_runtime_source_arg_converts_via_wslpath(monkeypatch, tmp_path: Path) -> None:
    from agent_logger.sync.targets import base

    class _Proc:
        returncode = 0
        stdout = "/mnt/c/Users/someuser/.copilot\n"

    monkeypatch.setattr(base.subprocess, "run", lambda *a, **k: _Proc())
    runtime = base.RsyncRuntime(command_prefix=["wsl.exe", "-e"], use_wsl=True)
    assert runtime.source_arg(tmp_path) == "/mnt/c/Users/someuser/.copilot/"


def test_rsync_runtime_source_arg_is_none_when_wslpath_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """A failed wslpath conversion must be a hard error, never a silent
    fallback to the raw Windows path -- WSL rsync would misparse or simply
    fail to find it."""
    from agent_logger.sync.targets import base

    class _Proc:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(base.subprocess, "run", lambda *a, **k: _Proc())
    runtime = base.RsyncRuntime(command_prefix=["wsl.exe", "-e"], use_wsl=True)
    assert runtime.source_arg(tmp_path) is None


def test_stage_wsl_secret_file_passes_content_as_stdin_and_sets_perms(
    monkeypatch, tmp_path: Path
) -> None:
    """Content must flow to the WSL-native staged file via stdin (never a
    converted DrvFS path), with 0600 permissions set in a separate call so a
    write failure can still clean up the file mktemp already created."""
    from agent_logger.sync.targets import base

    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"hunter2")

    captured_calls: list[tuple[list[str], dict]] = []

    def _fake_run(cmd, **kwargs):
        captured_calls.append((cmd, kwargs))
        if cmd[2:3] == ["mktemp"]:
            return type("P", (), {"returncode": 0, "stdout": b"/tmp/tmp.abc123\n"})()
        return type("P", (), {"returncode": 0, "stdout": b""})()

    monkeypatch.setattr(base.subprocess, "run", _fake_run)
    assert base.stage_wsl_secret_file(str(secret)) == "/tmp/tmp.abc123"
    write_kwargs = next(
        k for c, k in captured_calls if c[2:3] != ["mktemp"]
    )
    assert write_kwargs.get("input") == b"hunter2"


def test_stage_wsl_secret_file_expands_tilde_prefixed_paths(
    monkeypatch, tmp_path: Path
) -> None:
    """The shipped config example uses password_file: ~/.agent-logger/...;
    a bare Path never expands that, so every configured ~-path must resolve
    through the real home directory before reading."""
    from agent_logger.sync.targets import base

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"hunter2")

    captured_calls: list[tuple[list[str], dict]] = []

    def _fake_run(cmd, **kwargs):
        captured_calls.append((cmd, kwargs))
        if cmd[2:3] == ["mktemp"]:
            return type("P", (), {"returncode": 0, "stdout": b"/tmp/tmp.abc123\n"})()
        return type("P", (), {"returncode": 0, "stdout": b""})()

    monkeypatch.setattr(base.subprocess, "run", _fake_run)
    assert base.stage_wsl_secret_file("~/secret.txt") == "/tmp/tmp.abc123"
    write_kwargs = next(k for c, k in captured_calls if c[2:3] != ["mktemp"])
    assert write_kwargs.get("input") == b"hunter2"


def test_stage_wsl_secret_file_none_when_mktemp_fails(monkeypatch, tmp_path: Path) -> None:
    from agent_logger.sync.targets import base

    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"hunter2")

    class _Proc:
        returncode = 1
        stdout = b""

    monkeypatch.setattr(base.subprocess, "run", lambda *a, **k: _Proc())
    assert base.stage_wsl_secret_file(str(secret)) is None


def test_stage_wsl_secret_file_cleans_up_when_write_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """If mktemp succeeds but the write/chmod step fails, the already-created
    temp file must not be leaked in WSL's filesystem indefinitely."""
    from agent_logger.sync.targets import base

    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"hunter2")

    def _fake_run(cmd, **kwargs):
        if cmd[2:3] == ["mktemp"]:
            return type("P", (), {"returncode": 0, "stdout": b"/tmp/tmp.abc123\n"})()
        return type("P", (), {"returncode": 1, "stdout": b""})()

    monkeypatch.setattr(base.subprocess, "run", _fake_run)
    cleanup_calls: list[str] = []
    monkeypatch.setattr(base, "cleanup_wsl_staged_file", cleanup_calls.append)

    assert base.stage_wsl_secret_file(str(secret)) is None
    assert cleanup_calls == ["/tmp/tmp.abc123"]


def test_cleanup_wsl_staged_file_invokes_rm(monkeypatch) -> None:
    from agent_logger.sync.targets import base

    captured_commands: list[list[str]] = []

    class _Proc:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        return _Proc()

    monkeypatch.setattr(base.subprocess, "run", _fake_run)
    base.cleanup_wsl_staged_file("/tmp/tmp.abc123")
    assert captured_commands[-1][-2:] == ["-f", "/tmp/tmp.abc123"]


def test_cleanup_wsl_staged_file_swallows_errors(monkeypatch) -> None:
    """Cleanup is best-effort -- a failure here must never raise and mask the
    push's own result."""
    from agent_logger.sync.targets import base

    def _raise(*_a, **_k):
        raise OSError("wsl.exe not found")

    monkeypatch.setattr(base.subprocess, "run", _raise)
    base.cleanup_wsl_staged_file("/tmp/tmp.abc123")  # must not raise


def test_rsync_runtime_source_arg_noop_without_wsl(tmp_path: Path) -> None:
    from agent_logger.sync.targets import base

    runtime = base.RsyncRuntime(command_prefix=[], use_wsl=False)
    assert runtime.source_arg(tmp_path) == f"{tmp_path}/"


def test_ssh_target_push_uses_wsl_wrapped_rsync(monkeypatch, tmp_path: Path) -> None:
    """When WSL is available, push() must run rsync wrapped in `wsl.exe -e`,
    with a bare "ssh" (WSL's own, no cross-runtime sibling needed) and the
    source path converted via wslpath instead of a raw Windows path."""
    from agent_logger.sync.targets import base as sync_base
    from agent_logger.sync.targets import ssh as ssh_mod

    source = _make_source(tmp_path / "home")

    monkeypatch.setattr(sync_base, "wsl_rsync_available", lambda **_kwargs: True)
    monkeypatch.setattr(
        sync_base, "wsl_posix_path", lambda _p: "/mnt/c/Users/someuser/.copilot"
    )

    captured_commands: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        return _Proc()

    monkeypatch.setattr(ssh_mod.subprocess, "run", _fake_run)
    result = SshTarget({"host": "user@example", "remote_path": "/srv"}).push(source, "m1")

    assert result.ok
    cmd = captured_commands[-1]
    assert cmd[:3] == ["wsl.exe", "-e", "rsync"]
    # Search for rsync's own -e flag past the wsl.exe -e prefix (index 1).
    ssh_arg_index = cmd.index("-e", 2) + 1
    assert cmd[ssh_arg_index].startswith("ssh ")
    assert "/mnt/c/Users/someuser/.copilot/" in cmd


def test_ssh_target_push_fails_explicitly_when_wsl_conversion_fails(
    monkeypatch, tmp_path: Path
) -> None:
    """A failed wslpath conversion must surface as a push failure, never
    silently fall back to a raw Windows source path that WSL rsync would
    misparse or fail to find."""
    from agent_logger.sync.targets import base as sync_base
    from agent_logger.sync.targets import ssh as ssh_mod

    source = _make_source(tmp_path / "home")

    monkeypatch.setattr(sync_base, "wsl_rsync_available", lambda **_kwargs: True)
    monkeypatch.setattr(sync_base, "wsl_posix_path", lambda _p: None)

    captured_commands: list[list[str]] = []
    monkeypatch.setattr(
        ssh_mod.subprocess,
        "run",
        lambda cmd, **kwargs: captured_commands.append(cmd),
    )
    result = SshTarget({"host": "user@example", "remote_path": "/srv"}).push(source, "m1")

    assert not result.ok
    assert "convert" in result.detail
    assert not captured_commands  # rsync must never be invoked


def test_ingest_target_push_uses_wsl_wrapped_rsync(monkeypatch, tmp_path: Path) -> None:
    from agent_logger.sync.targets import base as sync_base
    from agent_logger.sync.targets import ingest as ingest_mod

    source = _make_source(tmp_path / "home")

    monkeypatch.setattr(sync_base, "wsl_rsync_available", lambda **_kwargs: True)
    monkeypatch.setattr(
        sync_base, "wsl_posix_path", lambda _p: "/mnt/c/Users/someuser/.copilot"
    )

    captured_commands: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        return _Proc()

    monkeypatch.setattr(ingest_mod.subprocess, "run", _fake_run)
    result = IngestTarget({"url": "rsync://h/mod"}).push(source, "m1")

    assert result.ok
    cmd = captured_commands[-1]
    assert cmd[:3] == ["wsl.exe", "-e", "rsync"]
    assert "/mnt/c/Users/someuser/.copilot/" in cmd


def test_ingest_target_push_stages_password_file_under_wsl(
    monkeypatch, tmp_path: Path
) -> None:
    """A WSL-wrapped rsync must use a restrictive-permission copy of a
    configured native Windows password-file staged inside WSL's own
    filesystem -- DrvFS normally exposes the raw Windows file as
    group/world-readable, which rsync refuses for --password-file -- and
    that staged file must be cleaned up afterward."""
    from agent_logger.sync.targets import base as sync_base
    from agent_logger.sync.targets import ingest as ingest_mod

    source = _make_source(tmp_path / "home")
    pw_file = tmp_path / "secret.txt"
    pw_file.write_text("hunter2", encoding="utf-8")

    monkeypatch.setattr(sync_base, "wsl_rsync_available", lambda **_kwargs: True)
    monkeypatch.setattr(
        sync_base, "wsl_posix_path", lambda _p: "/mnt/c/Users/someuser/.copilot"
    )
    monkeypatch.setattr(
        sync_base, "stage_wsl_secret_file", lambda _p: "/tmp/staged-secret"
    )
    cleanup_calls: list[str] = []
    monkeypatch.setattr(
        sync_base, "cleanup_wsl_staged_file", cleanup_calls.append
    )

    captured_commands: list[list[str]] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        return _Proc()

    monkeypatch.setattr(ingest_mod.subprocess, "run", _fake_run)
    result = IngestTarget(
        {"url": "rsync://h/mod", "password_file": str(pw_file)}
    ).push(source, "m1")

    assert result.ok
    assert "--password-file=/tmp/staged-secret" in captured_commands[-1]
    assert cleanup_calls == ["/tmp/staged-secret"]


def test_ingest_target_push_fails_explicitly_when_password_file_staging_fails(
    monkeypatch, tmp_path: Path
) -> None:
    from agent_logger.sync.targets import base as sync_base
    from agent_logger.sync.targets import ingest as ingest_mod

    source = _make_source(tmp_path / "home")
    pw_file = tmp_path / "secret.txt"
    pw_file.write_text("hunter2", encoding="utf-8")

    monkeypatch.setattr(sync_base, "wsl_rsync_available", lambda **_kwargs: True)
    monkeypatch.setattr(
        sync_base, "wsl_posix_path", lambda _p: "/mnt/c/Users/someuser/.copilot"
    )
    monkeypatch.setattr(sync_base, "stage_wsl_secret_file", lambda _p: None)

    captured_commands: list[list[str]] = []
    monkeypatch.setattr(
        ingest_mod.subprocess,
        "run",
        lambda cmd, **kwargs: captured_commands.append(cmd),
    )
    result = IngestTarget(
        {"url": "rsync://h/mod", "password_file": str(pw_file)}
    ).push(source, "m1")

    assert not result.ok
    assert "stage" in result.detail
    assert not captured_commands


def test_ssh_target_doctor_reports_wsl_rsync(monkeypatch) -> None:
    from agent_logger.sync.targets import base as sync_base
    from agent_logger.sync.targets import ssh as ssh_mod

    monkeypatch.setattr(sync_base, "wsl_rsync_available", lambda **_kwargs: True)

    captured_commands: list[list[str]] = []

    class _Proc:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured_commands.append(cmd)
        return _Proc()

    monkeypatch.setattr(ssh_mod.subprocess, "run", _fake_run)
    result = SshTarget({"host": "user@example", "remote_path": "/srv"}).doctor()
    detail_by_name = {name: detail for name, _ok, detail in result.checks}
    assert detail_by_name["rsync present"] == "via WSL"
    assert detail_by_name["ssh present"] == "via WSL"
    # Regression guard: the reachability probe must use -e (direct exec),
    # never -- (which silently drops positional arguments after the
    # command name when wsl.exe re-shells the joined command line).
    assert captured_commands[-1][:2] == ["wsl.exe", "-e"]


def test_ingest_target_doctor_reports_wsl_rsync(monkeypatch) -> None:
    from agent_logger.sync.targets import base as sync_base

    monkeypatch.setattr(sync_base, "wsl_rsync_available", lambda **_kwargs: True)
    result = IngestTarget({"url": "rsync://h/mod"}).doctor()
    detail_by_name = {name: detail for name, _ok, detail in result.checks}
    assert detail_by_name["rsync present"] == "via WSL"


def _cfg(home: Path, source: Path, dest: Path) -> Config:
    data = dict(load_config(home=home).as_dict())
    data["sync"]["source"] = str(source)
    data["sync"]["targets"]["local"]["path"] = str(dest)
    return Config(data, home)


def test_sync_meta_bounds_deferred_file_samples(tmp_path: Path) -> None:
    from agent_logger.sync import meta

    deferred = [f"path-{index}-{'x' * 600}" for index in range(20)]
    meta.write_sync_meta(
        tmp_path,
        "m" * 1000,
        "t" * 1000,
        "s" * 1000,
        12,
        deferred_files=deferred,
    )

    payload = meta.read_sync_meta(tmp_path)

    assert payload is not None
    assert len(payload["machine_id"]) == meta.MAX_META_FIELD_CHARS
    assert len(payload["transport"]) == meta.MAX_META_FIELD_CHARS
    assert len(payload["status"]) == meta.MAX_META_FIELD_CHARS
    assert payload["deferred_file_count"] == 20
    assert len(payload["deferred_files"]) == meta.MAX_DEFERRED_FILE_SAMPLES
    assert all(
        len(path) <= meta.MAX_DEFERRED_PATH_CHARS
        for path in payload["deferred_files"]
    )


def test_heartbeat_sync_meta_preserves_fields_only_touching_timestamp(
    tmp_path: Path,
) -> None:
    from agent_logger.sync import meta

    meta.write_sync_meta(
        tmp_path,
        "machine",
        "local",
        "partial",
        session_count=5,
        deferred_files=[f"path-{i}" for i in range(20)],
    )
    before = meta.read_sync_meta(tmp_path)
    assert before["consecutive_partial_count"] == 1
    assert before["deferred_file_count"] == 20

    meta.heartbeat_sync_meta(tmp_path, "machine", "local", fallback_session_count=5)
    after = meta.read_sync_meta(tmp_path)

    # Only the timestamp may change -- status/counts/samples untouched.
    assert after["status"] == "partial"
    assert after["consecutive_partial_count"] == 1
    assert after["deferred_file_count"] == 20
    assert after["session_count"] == 5
    assert after["last_sync_utc"] != "" and after["last_sync_utc"] is not None


def test_heartbeat_sync_meta_writes_fresh_when_nothing_exists(tmp_path: Path) -> None:
    from agent_logger.sync import meta

    meta.heartbeat_sync_meta(tmp_path, "machine", "local", fallback_session_count=3)
    payload = meta.read_sync_meta(tmp_path)
    assert payload is not None
    assert payload["status"] == "ok"
    assert payload["session_count"] == 3


def test_heartbeat_sync_meta_preserves_unreadable_metadata(tmp_path: Path) -> None:
    """A malformed/unreadable sync-meta.json is a visible error signal --
    a no-op heartbeat must never paper over it with a fresh 'ok' status."""
    from agent_logger.sync import meta

    meta_file = tmp_path / "sync-meta.json"
    meta_file.write_bytes(b"\xff" * (meta.MAX_SYNC_META_BYTES + 100))

    meta.heartbeat_sync_meta(tmp_path, "machine", "local", fallback_session_count=1)

    assert meta_file.read_bytes() == b"\xff" * (meta.MAX_SYNC_META_BYTES + 100)


def test_filesystem_target_heartbeat_noop_for_missing_destination(
    tmp_path: Path,
) -> None:
    """A deleted destination (never re-created by a heartbeat) must not get
    a fresh 'ok' sync-meta.json -- that would mask the fact its sessions
    are actually gone until the next full reconciliation."""
    root = tmp_path / "hub"
    target = LocalTarget({"path": str(root)})

    target.heartbeat("machine")  # root doesn't exist at all yet

    assert not (root / "machine").exists()


def test_filesystem_target_heartbeat_refreshes_existing_destination(
    tmp_path: Path,
) -> None:
    root = tmp_path / "hub"
    target = LocalTarget({"path": str(root)})
    src = _make_source(tmp_path)
    assert target.push(src, "machine").ok

    target.heartbeat("machine")
    from agent_logger.sync import meta

    payload = meta.read_sync_meta(root / "machine")
    assert payload is not None


def test_sync_meta_tracks_and_resets_consecutive_partial_count(
    tmp_path: Path,
) -> None:
    from agent_logger.sync import meta

    meta.write_sync_meta(tmp_path, "machine", "local", "partial")
    assert meta.read_sync_meta(tmp_path)["consecutive_partial_count"] == 1

    meta.write_sync_meta(tmp_path, "machine", "local", "partial")
    assert meta.read_sync_meta(tmp_path)["consecutive_partial_count"] == 2

    meta.write_sync_meta(tmp_path, "machine", "local", "ok")
    assert meta.read_sync_meta(tmp_path)["consecutive_partial_count"] == 0


def test_sync_meta_rejects_parser_recursion_payload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import meta

    (tmp_path / "sync-meta.json").write_text("{}", encoding="utf-8")

    def raise_recursion_error(_raw):
        raise RecursionError

    monkeypatch.setattr(meta.json, "loads", raise_recursion_error)

    with pytest.raises(OSError, match="invalid sync metadata"):
        meta.read_sync_meta(tmp_path)


def test_status_prints_latest_partial_sync(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import meta

    source = _make_source(tmp_path)
    dest = tmp_path / "dest"
    machine_root = dest / "machine"
    meta.write_sync_meta(
        machine_root,
        "machine",
        "local",
        "partial",
        12,
        deferred_files=["session-state/one/locked.db"],
    )
    cfg = _cfg(tmp_path / "home", source, dest)
    monkeypatch.setattr(engine, "_machine", lambda _cfg: "machine")

    assert engine.do_status(cfg) == 0

    output = capsys.readouterr().out
    assert "latest_status:  partial" in output
    assert "partial_streak: 1" in output
    assert "sessions:       12" in output
    assert "deferred_files: 1" in output
    assert "session-state/one/locked.db" in output


def test_status_distinguishes_missing_sync_metadata(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path / "home", _make_source(tmp_path), tmp_path / "dest")
    monkeypatch.setattr(engine, "_machine", lambda _cfg: "machine")

    assert engine.do_status(cfg) == 0

    assert "latest_sync:    (none)" in capsys.readouterr().out


def test_status_handles_malformed_and_control_metadata(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    dest = tmp_path / "dest"
    machine_root = dest / "machine"
    machine_root.mkdir(parents=True)
    (machine_root / "sync-meta.json").write_text(
        json.dumps(
            {
                "last_sync_utc": {"invalid": True},
                "status": "partial\u001b[31m",
                "session_count": [],
                "deferred_file_count": 2,
                "deferred_files": 5,
            }
        ),
        encoding="utf-8",
    )
    cfg = _cfg(tmp_path / "home", _make_source(tmp_path), dest)
    monkeypatch.setattr(engine, "_machine", lambda _cfg: "machine")

    assert engine.do_status(cfg) == 0

    output = capsys.readouterr().out
    assert "latest_sync:    (unknown)" in output
    assert "latest_status:  partial[31m" in output
    assert "\u001b" not in output
    assert "sessions:       (unknown)" in output
    assert "(invalid deferred_files metadata)" in output


def test_status_sanitizes_unreadable_metadata_error(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path / "home", _make_source(tmp_path), tmp_path / "dest")
    monkeypatch.setattr(engine, "_machine", lambda _cfg: "machine")

    class InvalidStatusTarget:
        def describe(self) -> str:
            return "invalid"

        def sync_status(self, _machine):
            from agent_logger.sync.targets.base import SyncStatus

            return SyncStatus(supported=True, error="bad\u001b[31m\nmetadata")

    monkeypatch.setattr(engine, "build_target", lambda *_args: InvalidStatusTarget())

    assert engine.do_status(cfg) == 0

    output = capsys.readouterr().out
    assert "unreadable (bad[31mmetadata)" in output
    assert "\u001b" not in output


def test_health_allows_one_fresh_partial_pass(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import meta

    source = _make_source(tmp_path)
    dest = tmp_path / "dest"
    meta.write_sync_meta(
        dest / "machine",
        "machine",
        "local",
        "partial",
        deferred_files=["session-state/one/locked.db"],
    )
    cfg = _cfg(tmp_path / "home", source, dest)
    monkeypatch.setattr(engine, "_machine", lambda _cfg: "machine")

    assert engine.do_health(
        cfg,
        fleet=False,
        machines=[],
        max_age_hours=12,
        partial_threshold=3,
        json_output=False,
    ) == 0

    output = capsys.readouterr().out
    assert "machine: degraded (transient_partial)" in output
    assert "partial_streak=1" in output


def test_health_fleet_json_fails_on_repeated_partial(
    capsys,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import meta

    source = _make_source(tmp_path)
    dest = tmp_path / "dest"
    meta.write_sync_meta(dest / "healthy", "healthy", "local", "ok", 10)
    for _ in range(3):
        meta.write_sync_meta(
            dest / ".codespaces" / "repeated",
            ".codespaces/repeated",
            "local",
            "partial",
            4,
            deferred_files=["session-state/one/locked.db"],
        )
    cfg = _cfg(tmp_path / "home", source, dest)

    assert engine.do_health(
        cfg,
        fleet=True,
        machines=[],
        max_age_hours=12,
        partial_threshold=3,
        json_output=True,
    ) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["health"] == "unhealthy"
    assert payload["summary"] == {
        "healthy": 1,
        "degraded": 0,
        "unhealthy": 1,
    }
    repeated = next(
        machine
        for machine in payload["machines"]
        if machine["machine"] == ".codespaces/repeated"
    )
    assert repeated["reason"] == "repeated_partial"
    assert repeated["consecutive_partial_count"] == 3


def test_health_fleet_can_select_active_machine_subset(
    capsys,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import meta

    source = _make_source(tmp_path)
    dest = tmp_path / "dest"
    meta.write_sync_meta(dest / "active", "active", "local", "ok", 10)
    meta.write_sync_meta(dest / "retired", "retired", "local", "partial", 4)
    cfg = _cfg(tmp_path / "home", source, dest)

    assert engine.do_health(
        cfg,
        fleet=True,
        machines=["active"],
        max_age_hours=12,
        partial_threshold=1,
        json_output=True,
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["health"] == "healthy"
    assert [machine["machine"] for machine in payload["machines"]] == ["active"]


def test_health_fleet_ignores_root_metadata_and_scans_machine_dirs(
    capsys,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import meta

    source = _make_source(tmp_path)
    dest = tmp_path / "dest"
    meta.write_sync_meta(dest, "not-a-machine", "local", "ok", 1)
    meta.write_sync_meta(dest / "machine", "machine", "local", "ok", 2)
    cfg = _cfg(tmp_path / "home", source, dest)

    assert engine.do_health(
        cfg,
        fleet=True,
        machines=[],
        max_age_hours=12,
        partial_threshold=3,
        json_output=True,
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [machine["machine"] for machine in payload["machines"]] == ["machine"]


def test_health_fleet_reports_machine_missing_metadata(
    capsys,
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path)
    dest = tmp_path / "dest"
    (dest / "missing" / "session-state").mkdir(parents=True)
    cfg = _cfg(tmp_path / "home", source, dest)

    assert engine.do_health(
        cfg,
        fleet=True,
        machines=[],
        max_age_hours=12,
        partial_threshold=3,
        json_output=True,
    ) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["machines"][0]["machine"] == "missing"
    assert payload["machines"][0]["reason"] == "missing_metadata"


def test_health_fleet_scan_is_globally_bounded(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    from agent_logger.sync.targets import filesystem

    source = _make_source(tmp_path)
    dest = tmp_path / "dest"
    (dest / "one").mkdir(parents=True)
    (dest / "two").mkdir()
    cfg = _cfg(tmp_path / "home", source, dest)
    monkeypatch.setattr(filesystem, "_MAX_FLEET_ENTRIES", 1)

    assert engine.do_health(
        cfg,
        fleet=True,
        machines=[],
        max_age_hours=12,
        partial_threshold=3,
        json_output=False,
    ) == 1

    assert "fleet scan exceeds 1 entries" in capsys.readouterr().err


def test_health_fleet_keeps_other_machines_when_one_directory_is_unreadable(
    monkeypatch,
    capsys,
    tmp_path: Path,
) -> None:
    from agent_logger.sync import meta
    from agent_logger.sync.targets import filesystem

    source = _make_source(tmp_path)
    dest = tmp_path / "dest"
    meta.write_sync_meta(dest / "healthy", "healthy", "local", "ok", 10)
    (dest / "unreadable").mkdir(parents=True)
    real_scandir = filesystem.os.scandir

    def fail_one_directory(path):
        if str(path).endswith("unreadable"):
            raise PermissionError("blocked")
        return real_scandir(path)

    monkeypatch.setattr(filesystem.os, "scandir", fail_one_directory)
    cfg = _cfg(tmp_path / "home", source, dest)

    assert engine.do_health(
        cfg,
        fleet=True,
        machines=[],
        max_age_hours=12,
        partial_threshold=3,
        json_output=True,
    ) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {
        "healthy": 1,
        "degraded": 0,
        "unhealthy": 1,
    }
    assert {
        machine["machine"]: machine["reason"]
        for machine in payload["machines"]
    } == {
        "healthy": "fresh_complete",
        "unreadable": "unreadable_metadata",
    }


@pytest.mark.parametrize("threshold", ["nan", "inf", "-inf", "0"])
def test_health_cli_rejects_invalid_freshness_threshold(
    monkeypatch,
    capsys,
    tmp_path: Path,
    threshold: str,
) -> None:
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / "home"))

    assert engine.main(["health", f"--max-age-hours={threshold}"]) == 2

    assert "thresholds must be finite and positive" in capsys.readouterr().err


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


def test_engine_run_sync_second_run_skips_push_when_unchanged(
    monkeypatch, capsys, tmp_path: Path,
) -> None:
    """Change tracking is enabled by default: after a first (full) sync, a
    second run with no local changes must not invoke the target's push at
    all."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)

    assert engine.run_sync(cfg) == 0
    capsys.readouterr()

    calls: list[object] = []
    real_push = LocalTarget.push

    def _tracking_push(self, *a, **k):
        calls.append((a, k))
        return real_push(self, *a, **k)

    monkeypatch.setattr(LocalTarget, "push", _tracking_push)

    assert engine.run_sync(cfg, verbose=True) == 0
    assert calls == []
    assert "no session changes detected" in capsys.readouterr().out


def test_engine_run_sync_repushes_modified_session(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)

    assert engine.run_sync(cfg) == 0
    (src / "session-state" / "abc-123" / "events.jsonl").write_text(
        '{"ts": 2}\n', encoding="utf-8"
    )

    assert engine.run_sync(cfg) == 0
    machine_dir = next(dest.iterdir())
    assert (
        machine_dir / "session-state" / "abc-123" / "events.jsonl"
    ).read_text(encoding="utf-8") == '{"ts": 2}\n'


def test_engine_run_sync_full_flag_forces_reconciliation_detail(
    capsys, tmp_path: Path,
) -> None:
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)

    assert engine.run_sync(cfg) == 0
    capsys.readouterr()

    assert engine.run_sync(cfg, full=True) == 0
    assert "full reconciliation" in capsys.readouterr().out


def test_engine_run_sync_change_tracking_disabled_pushes_every_run(
    monkeypatch, tmp_path: Path,
) -> None:
    """``sync.change_tracking.enabled: false`` restores the pre-feature
    behavior: every run pushes via the plain ``include`` set, with no
    incremental skip and no segmented batching."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)
    cfg._data["sync"]["change_tracking"] = {"enabled": False}

    calls: list[object] = []
    real_push = LocalTarget.push

    def _tracking_push(self, *a, **k):
        calls.append((a, k))
        return real_push(self, *a, **k)

    monkeypatch.setattr(LocalTarget, "push", _tracking_push)

    assert engine.run_sync(cfg) == 0
    assert len(calls) == 1
    assert calls[0][0][-1] is None  # include=None, the legacy single-call shape

    assert engine.run_sync(cfg) == 0
    assert len(calls) == 2


def test_engine_run_sync_batches_full_reconciliation(tmp_path: Path) -> None:
    """A from-scratch full sync with more sessions than ``batch_size`` must
    still land every session, split across multiple bounded pushes."""
    src = tmp_path / "copilot"
    for i in range(5):
        sess = src / "session-state" / f"sess-{i}"
        sess.mkdir(parents=True)
        (sess / "events.jsonl").write_text(f'{{"ts": {i}}}\n', encoding="utf-8")
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)
    cfg._data["sync"]["change_tracking"] = {"batch_size": 2}

    assert engine.run_sync(cfg, verbose=True) == 0
    machine_dir = next(dest.iterdir())
    for i in range(5):
        assert (
            machine_dir / "session-state" / f"sess-{i}" / "events.jsonl"
        ).is_file()


def test_engine_run_sync_forgets_vanished_session_on_full_sync(
    tmp_path: Path,
) -> None:
    import shutil

    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)

    assert engine.run_sync(cfg) == 0

    from agent_logger.sync.change_tracker import ChangeTracker, resolve_db_path

    tracker = ChangeTracker(resolve_db_path(cfg.sync_change_tracking["db_path"], cfg.home))
    assert tracker.known_session_ids() == {"abc-123"}

    shutil.rmtree(src / "session-state" / "abc-123")
    assert engine.run_sync(cfg, full=True) == 0
    assert tracker.known_session_ids() == set()


def test_engine_run_sync_forces_full_when_destination_target_changes(
    tmp_path: Path,
) -> None:
    """Changing the sync target/path after the tracker has already recorded
    signatures against the old one must trigger a fresh full reconciliation,
    not silently skip pushing to the new destination."""
    src = _make_source(tmp_path)
    dest_a = tmp_path / "dest-a"
    cfg = _cfg(tmp_path / "home", src, dest_a)

    assert engine.run_sync(cfg) == 0
    assert (dest_a / next(dest_a.iterdir()).name / "session-state" / "abc-123").is_dir()

    dest_b = tmp_path / "dest-b"
    cfg._data["sync"]["targets"]["local"]["path"] = str(dest_b)

    assert engine.run_sync(cfg) == 0
    machine_dir = next(dest_b.iterdir())
    assert (
        machine_dir / "session-state" / "abc-123" / "events.jsonl"
    ).is_file()


def test_engine_run_sync_heartbeats_destination_on_no_change_skip(
    tmp_path: Path,
) -> None:
    """A skipped (no-change) push must still refresh the destination's own
    health metadata, or a routine health check reports a perfectly healthy,
    unchanged destination as stale between full-reconciliation passes."""
    from agent_logger.sync import meta

    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)

    assert engine.run_sync(cfg) == 0
    machine_dir = next(dest.iterdir())
    first_meta = meta.read_sync_meta(machine_dir)
    assert first_meta is not None

    import time as time_module

    time_module.sleep(1.1)
    assert engine.run_sync(cfg) == 0
    second_meta = meta.read_sync_meta(machine_dir)
    assert second_meta is not None
    assert second_meta["last_sync_utc"] != first_meta["last_sync_utc"]


def test_engine_run_sync_preserves_index_for_unfiltered_incremental_push(
    tmp_path: Path,
) -> None:
    """An unfiltered incremental/segmented push must still eventually
    refresh the global session-store.db index, even though any one call
    only carries a transport-size batch of sessions."""
    src = _make_source(tmp_path)
    (src / "session-store.db").write_text("index-v1", encoding="utf-8")
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)

    assert engine.run_sync(cfg) == 0
    machine_dir = next(dest.iterdir())
    assert (machine_dir / "session-store.db").read_text(encoding="utf-8") == "index-v1"

    (src / "session-store.db").write_text("index-v2", encoding="utf-8")
    (src / "session-state" / "abc-123" / "events.jsonl").write_text(
        '{"ts": 2}\n', encoding="utf-8"
    )
    assert engine.run_sync(cfg) == 0
    assert (machine_dir / "session-store.db").read_text(encoding="utf-8") == "index-v2"


def test_engine_run_sync_pushes_index_only_change_with_no_session_changes(
    tmp_path: Path,
) -> None:
    """An index-only change (no individual session touched) must still be
    detected and pushed on the next incremental run, not silently skipped."""
    src = _make_source(tmp_path)
    (src / "session-store.db").write_text("index-v1", encoding="utf-8")
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)

    assert engine.run_sync(cfg) == 0
    machine_dir = next(dest.iterdir())
    assert (machine_dir / "session-store.db").read_text(encoding="utf-8") == "index-v1"

    (src / "session-store.db").write_text("index-v2-only", encoding="utf-8")
    assert engine.run_sync(cfg) == 0
    assert (
        machine_dir / "session-store.db"
    ).read_text(encoding="utf-8") == "index-v2-only"


def test_engine_run_sync_full_pushes_index_only_source_with_no_sessions(
    tmp_path: Path,
) -> None:
    """A full reconciliation must still carry an index-only source (zero
    session directories) -- an empty batch list must not skip the index."""
    src = tmp_path / "copilot"
    src.mkdir()
    (src / "session-store.db").write_text("index-only", encoding="utf-8")
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)

    assert engine.run_sync(cfg, full=True) == 0
    machine_dir = next(dest.iterdir())
    assert (
        machine_dir / "session-store.db"
    ).read_text(encoding="utf-8") == "index-only"


def test_engine_run_sync_resets_stale_signatures_on_identity_change(
    tmp_path: Path,
) -> None:
    """A session unchanged since it was last synced to destination A must
    still be pushed to a NEW destination B -- its stale, content-only
    signature from A must not fool B's incremental check into skipping it."""
    src = tmp_path / "copilot"
    for name in ("x", "y"):
        sess = src / "session-state" / name
        sess.mkdir(parents=True)
        (sess / "events.jsonl").write_text(f'{{"id": "{name}"}}\n', encoding="utf-8")
    dest_a = tmp_path / "dest-a"
    cfg = _cfg(tmp_path / "home", src, dest_a)
    cfg._data["sync"]["repo_allowlist"] = []

    assert engine.run_sync(cfg) == 0
    assert (dest_a / next(dest_a.iterdir()).name / "session-state" / "y").is_dir()

    dest_b = tmp_path / "dest-b"
    cfg._data["sync"]["targets"]["local"]["path"] = str(dest_b)

    # y's content never changes -- only the destination does.
    assert engine.run_sync(cfg) == 0
    machine_dir = next(dest_b.iterdir())
    assert (machine_dir / "session-state" / "x").is_dir()
    assert (machine_dir / "session-state" / "y").is_dir()


def test_engine_run_sync_does_not_record_deferred_session_signature(
    monkeypatch, tmp_path: Path,
) -> None:
    """A session with a deferred (locked) file during the push must not be
    recorded as synced -- it must still show up as changed next run."""
    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)

    from agent_logger.sync.targets.base import PushResult

    real_push = LocalTarget.push
    calls: list[set] = []

    def _deferring_push(self, source, machine, include_sessions=None, *, batch_mode=False):
        result = real_push(
            self, source, machine, include_sessions, batch_mode=batch_mode
        )
        calls.append(include_sessions)
        if len(calls) == 1:
            return PushResult(
                ok=True,
                detail=result.detail,
                file_count=result.file_count,
                deferred_sessions=("abc-123",),
            )
        return result

    monkeypatch.setattr(LocalTarget, "push", _deferring_push)

    assert engine.run_sync(cfg) == 0  # first (full) pass "defers" abc-123

    from agent_logger.sync.change_tracker import ChangeTracker, resolve_db_path

    tracker = ChangeTracker(resolve_db_path(cfg.sync_change_tracking["db_path"], cfg.home))
    assert tracker.changed_sessions(src) == {"abc-123"}

    assert engine.run_sync(cfg) == 0  # second pass retries it, this time clean
    assert tracker.changed_sessions(src) == set()


def test_hub_compaction_fails_closed_when_tracked_lookup_unresolved(
    monkeypatch, capsys, tmp_path: Path,
) -> None:
    """An unresolved (not confirmed-empty) tracked-worktree lookup must skip
    hub compaction rather than proceed as if nothing needed protecting."""
    from agent_logger.sync import compact as compact_mod

    src = _make_source(tmp_path)
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)
    cfg._data["sync"]["compact"] = {"enabled": True}
    calls: list[object] = []
    monkeypatch.setattr(
        compact_mod, "resolve_hub_tracked_paths", lambda require: (None, True),
    )
    monkeypatch.setattr(
        LocalTarget, "compact_backlog",
        lambda *a, **k: calls.append((a, k)) or 0,
    )

    assert engine.run_sync(cfg) == 0
    assert calls == []
    assert "unresolved this pass" in capsys.readouterr().err

    calls.clear()
    assert engine.do_compact_hub(cfg, dry_run=False, verbose=False) == 0
    assert calls == []
    assert "unresolved this pass" in capsys.readouterr().err


def test_engine_run_sync_disabled(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_LOGGER_SYNC_DISABLED", "1")
    cfg = _cfg(tmp_path / "home", _make_source(tmp_path), tmp_path / "dest")
    assert engine.run_sync(cfg) == 0
    assert not (tmp_path / "dest").exists()


def test_sync_lock_propagates_body_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError, match="body failed"):
        with sync_lock(tmp_path / "sync.lock") as acquired:
            assert acquired
            raise OSError("body failed")


def test_sync_lock_rejects_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("unchanged", encoding="utf-8")
    lock = tmp_path / "sync.lock"
    try:
        lock.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(OSError):
        with sync_lock(lock):
            pytest.fail("symlinked lock must not be acquired")
    assert outside.read_text(encoding="utf-8") == "unchanged"


def test_sync_lock_rejects_symlinked_ancestor(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(OSError):
        with sync_lock(alias / "state" / "sync.lock"):
            pytest.fail("lock beneath a symlinked ancestor must not be acquired")
    assert list(outside.iterdir()) == []


def test_filtered_push_fails_while_destination_lock_is_held(
    tmp_path: Path,
    monkeypatch,
) -> None:
    src = _make_source(tmp_path)
    target = LocalTarget({"path": str(tmp_path / "dest")})

    class BusyLock:
        def __enter__(self):
            return False

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        "agent_logger.sync.targets.filesystem.sync_lock",
        lambda *_args, **_kwargs: BusyLock(),
    )

    result = target.push(src, "m1", {"abc-123"})

    assert not result.ok
    assert "destination rescue lock is busy" in result.detail
    assert not (tmp_path / "dest" / "m1" / "session-state").exists()


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


def test_allowlist_fail_closed_excludes_unclassified(tmp_path: Path) -> None:
    src = tmp_path / "copilot"
    # unclassifiable: no workspace.yaml
    nows = src / "session-state" / "no-ws"
    nows.mkdir(parents=True)
    (nows / "events.jsonl").write_text("{}\n", encoding="utf-8")
    # positively classified: cwd matches the allowlist
    ok = src / "session-state" / "dotfiles-sess"
    ok.mkdir(parents=True)
    (ok / "workspace.yaml").write_text("cwd: /home/u/dotfiles\n", encoding="utf-8")

    # fail-open (default): both kept
    assert engine._included_sessions(src, ["dotfiles"]) == {"no-ws", "dotfiles-sess"}
    # fail-closed: only the positively-classified session is kept
    assert engine._included_sessions(
        src, ["dotfiles"], fail_closed=True) == {"dotfiles-sess"}


def test_config_repo_allowlist_fail_closed_flag(tmp_path: Path) -> None:
    base = load_config(home=tmp_path).as_dict()
    # default is fail-open (false)
    assert Config(dict(base), tmp_path).sync_repo_allowlist_fail_closed is False
    data = dict(base)
    data["sync"] = dict(data["sync"], repo_allowlist_fail_closed=True)
    assert Config(data, tmp_path).sync_repo_allowlist_fail_closed is True


def test_denylist_catchall_is_complement_of_facility(tmp_path: Path) -> None:
    """book2's work sink: no allowlist + deny multi-machine system repos = 'everything else'.
    A multi-machine system session is excluded (goes to the NAS via the other pipeline); a
    work / unknown / metadata-less session is caught for the work store."""
    src = tmp_path / "copilot"
    for name, ws in (
        ("fac", "git_root: /home/u/src/test-chamber\n"),
        ("ce", "git_root: /home/u/src/copilot-extensions\n"),
        ("work", "git_root: /home/u/work/dotfiles\n"),
        ("mystery", "git_root: /home/u/work/some-employer-thing\n"),
    ):
        d = src / "session-state" / name
        d.mkdir(parents=True)
        (d / "workspace.yaml").write_text(ws, encoding="utf-8")
    bare = src / "session-state" / "bare"
    bare.mkdir(parents=True)
    (bare / "events.jsonl").write_text("{}\n", encoding="utf-8")

    deny = ["test-chamber", "copilot-extensions"]
    eff = origin_effective([], ["dotfiles"], deny)
    included = engine._included_sessions(
        src, [], effective=eff, machine="book2", denylist=deny)
    assert included == {"work", "mystery", "bare"}   # everything NOT multi-machine system


def test_no_filter_syncs_everything(tmp_path: Path) -> None:
    """Empty allowlist AND empty denylist -> no filter (sync all)."""
    src = tmp_path / "copilot"
    (src / "session-state" / "a").mkdir(parents=True)
    assert engine._included_sessions(src, [], denylist=[]) is None


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


def test_unfiltered_push_omits_symlinks_and_special_files(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    session = src / "session-state" / "abc-123"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        (session / "linked-file").symlink_to(outside)
        (session / "linked-dir").symlink_to(
            outside_dir,
            target_is_directory=True,
        )
    except OSError:
        pytest.skip("symlink creation is unavailable")
    fifo = session / "special-fifo"
    if hasattr(os, "mkfifo"):
        os.mkfifo(fifo)

    dest_root = tmp_path / "dest"
    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert result.ok
    published = dest_root / "m1" / "session-state" / "abc-123"
    assert not (published / "linked-file").exists()
    assert not (published / "linked-file").is_symlink()
    assert not (published / "linked-dir").exists()
    assert not (published / "linked-dir").is_symlink()
    assert not (published / "special-fifo").exists()


def test_unfiltered_push_replaces_destination_symlink_before_skip(
    tmp_path: Path,
) -> None:
    src = _make_source(tmp_path)
    source_file = src / "session-state" / "abc-123" / "events.jsonl"
    dest_root = tmp_path / "dest"
    destination = dest_root / "m1" / "session-state" / "abc-123" / "events.jsonl"
    destination.parent.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b"x" * source_file.stat().st_size)
    future = source_file.stat().st_mtime + 3600
    os.utime(outside, (future, future))
    try:
        destination.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1")

    assert result.ok
    assert not destination.is_symlink()
    assert destination.read_bytes() == source_file.read_bytes()
    assert outside.read_bytes() == b"x" * source_file.stat().st_size


def test_rsync_session_filters_scope_without_allowlist() -> None:
    """The unfiltered rsync filter must scope to session data, not be empty."""
    from agent_logger.sync.targets.base import rsync_session_filters

    unfiltered = rsync_session_filters(None)
    assert "--include=session-state/***" in unfiltered
    assert "--include=provenance/*.json" in unfiltered
    assert "--include=session-store.db" in unfiltered
    assert unfiltered[-1] == "--exclude=*"

    filtered = rsync_session_filters({"abc-123"})
    assert "--include=session-state/abc-123/***" in filtered
    assert "--include=provenance/abc-123.json" in filtered
    # session-store.db is dropped when filtering by repo.
    assert "--include=session-store.db" not in filtered
    assert filtered[-1] == "--exclude=*"


def test_rsync_session_filters_exclude_lock_sidecars_before_includes() -> None:
    """Lock/temp/hold sidecars must never reach an rsync-based target either.

    Mirrors the filesystem targets' ``_EXCLUDE_NAMES``/``_EXCLUDE_SUFFIXES``:
    a live ``inuse.<pid>.hold`` marker (or a ``.lock``/``.tmp`` sidecar) must
    be excluded before the recursive include, since rsync's filter list uses
    first-match-wins semantics.
    """
    from agent_logger.sync.targets.base import rsync_session_filters

    filters = rsync_session_filters(None)

    for pattern in ("--exclude=.lock", "--exclude=lock", "--exclude=*.lock",
                    "--exclude=*.tmp", "--exclude=*.hold"):
        assert pattern in filters
        assert filters.index(pattern) < filters.index("--include=session-state/***")


def test_rsync_session_filters_exclude_detected_detritus_first() -> None:
    from agent_logger.sync.targets.base import rsync_session_filters

    root = Path("session-state") / "abc-123" / "files" / "tool" / "browser"
    filters = rsync_session_filters(None, (root,))

    assert f"--exclude=/{root.as_posix()}/***" in filters
    assert (
        filters.index(f"--exclude=/{root.as_posix()}/***")
        < filters.index("--include=session-state/***")
    )
    # Lock/temp/hold sidecar excludes precede detected-detritus excludes.
    assert filters.index("--exclude=*.hold") < filters.index(
        f"--exclude=/{root.as_posix()}/***"
    )


def test_rsync_session_filters_escape_pattern_characters() -> None:
    from agent_logger.sync.targets.base import rsync_session_filters

    root = Path("session-state") / "abc" / "files" / "run[1]*?"
    filters = rsync_session_filters(None, (root,))

    assert (
        "--exclude=/session-state/abc/files/run\\[1\\]\\*\\?/***"
    ) in filters


def test_local_target_push_includes_only_selected_provenance(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    provenance = src / "provenance"
    provenance.mkdir()
    (provenance / "abc-123.json").write_text('{"schema_version":1}\n', encoding="utf-8")
    (provenance / "other.json").write_text('{"schema_version":1}\n', encoding="utf-8")
    dest_root = tmp_path / "dest"

    result = LocalTarget({"path": str(dest_root)}).push(src, "m1", {"abc-123"})

    assert result.ok
    assert (dest_root / "m1" / "provenance" / "abc-123.json").is_file()
    assert not (dest_root / "m1" / "provenance" / "other.json").exists()


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
        "https://h/api/webhook/x?m={machine}", "anomalous-potato-wsl",
        bearer_token_file=str(tok), timeout=3,
    )
    assert ok is True
    assert captured["url"] == "https://h/api/webhook/x?m=anomalous-potato-wsl"
    assert b'"machine": "anomalous-potato-wsl"' in captured["data"]
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


@pytest.mark.no_autotrust
def test_main_honors_repo_local_sync_local_path(monkeypatch, tmp_path):
    """engine.main() -- the actual session-sync CLI entry point every
    scheduled sync invokes -- must resolve schema v3's repo-local
    sync.local_path, not just the ambient CLI config command. This was
    missed when schema v3 was added: main() hardcoded
    load_config(include_repo=False), a pre-v3 boundary that made the whole
    feature inert for the one thing it exists to fix."""
    from .conftest import init_git_repo

    repo = tmp_path / "repo"
    init_git_repo(repo, remote="https://example.test/example-owner/demo.git", branch="main")
    (repo / ".agent-logger.yaml").write_text(
        "schema_version: 3\nsync:\n  local_path: "
        + str(tmp_path / "declared-target")
        + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "repos.yaml"
    import yaml

    registry.write_text(
        yaml.safe_dump(
            {
                "repos": {
                    "demo": {
                        "remote": "https://example.test/example-owner/demo.git",
                        "default_branch": "main",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(registry))
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(repo)

    captured = {}

    def _fake_run_sync(cfg, **kwargs):
        captured["sync_path"] = cfg.sync_path
        return 0

    monkeypatch.setattr(engine, "run_sync", _fake_run_sync)

    assert engine.main(["run"]) == 0
    assert captured["sync_path"] == tmp_path / "declared-target"


# -- process-log sync publication ------------------------------------------


def _make_process_logs(root: Path) -> Path:
    """Create a fake ``<sync_source>/logs`` directory with mixed evidence."""
    logs = root / "copilot" / "logs"
    logs.mkdir(parents=True)
    (logs / "process-111-1.log").write_text("line one\n", encoding="utf-8")
    (logs / "process-222-2.log.gz").write_bytes(b"\x1f\x8b\x08\x00fake-gzip")
    (logs / "process-333-3.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    (logs / "notes.log").write_text("not a process log\n", encoding="utf-8")
    (logs / "process-444-4.log.tmp").write_text("in-flight\n", encoding="utf-8")
    return logs


def test_is_process_log_candidate() -> None:
    from agent_logger.process_logs import is_process_log_candidate

    assert is_process_log_candidate("process-111-1.log")
    assert is_process_log_candidate("process-111-1.log.gz")
    assert is_process_log_candidate("anything.zip")
    assert not is_process_log_candidate("notes.log")
    assert not is_process_log_candidate("process-111-1.log.tmp")
    assert not is_process_log_candidate("process-.log")


def test_is_process_log_candidate_rejects_path_separators() -> None:
    """A bare filename is the documented contract; a path-like value must
    never satisfy the .zip branch's plain `str.endswith` check, regardless
    of which separator convention the host or the value itself uses."""
    from agent_logger.process_logs import is_process_log_candidate

    assert not is_process_log_candidate("../outside.zip")
    assert not is_process_log_candidate("sub/outside.zip")
    assert not is_process_log_candidate("sub\\outside.zip")
    assert not is_process_log_candidate("..\\outside.zip")
    assert not is_process_log_candidate("sub/process-111-1.log")
    assert not is_process_log_candidate("sub\\process-111-1.log")


def test_local_target_push_process_logs_selects_candidates_only(
    tmp_path: Path,
) -> None:
    logs = _make_process_logs(tmp_path)
    dest_root = tmp_path / "dest"
    target = LocalTarget({"path": str(dest_root)})

    result = target.push_process_logs(logs, "m1")

    assert result.ok
    assert result.file_count == 3
    machine_logs = dest_root / "m1" / "logs"
    assert (machine_logs / "process-111-1.log").read_text(encoding="utf-8") == "line one\n"
    assert (machine_logs / "process-222-2.log.gz").is_file()
    assert (machine_logs / "process-333-3.zip").is_file()
    assert not (machine_logs / "notes.log").exists()
    assert not (machine_logs / "process-444-4.log.tmp").exists()


def test_local_target_push_process_logs_is_incremental(tmp_path: Path) -> None:
    logs = _make_process_logs(tmp_path)
    target = LocalTarget({"path": str(tmp_path / "dest")})
    target.push_process_logs(logs, "m1")

    second = target.push_process_logs(logs, "m1")

    assert second.ok
    assert second.file_count == 0


def test_local_target_push_process_logs_missing_source_is_ok(tmp_path: Path) -> None:
    target = LocalTarget({"path": str(tmp_path / "dest")})

    result = target.push_process_logs(tmp_path / "copilot" / "logs", "m1")

    assert result.ok
    assert result.file_count == 0
    assert not (tmp_path / "dest").exists()


def test_base_target_push_process_logs_reports_unsupported(tmp_path: Path) -> None:
    target = IngestTarget({})

    result = target.push_process_logs(tmp_path, "m1")

    assert result.ok
    assert "does not support process-log sync" in result.detail


@pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW directory pinning is POSIX-only")
def test_copy_process_logs_rejects_symlinked_root(tmp_path: Path) -> None:
    """A deterministic stand-in for the TOCTOU race: once the configured
    root names a symlink (whether swapped in after the caller's own
    validation, or from the start), the copy must refuse it rather than
    traversing through it -- enumeration and every per-entry open are
    pinned to one directory handle opened with O_NOFOLLOW, not re-resolved
    by path for each entry (mirroring agent_logger.process_logs's own
    root-pinning guarantee)."""
    from agent_logger.sync.targets.filesystem import _copy_process_logs

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "process-111-1.log").write_text("outside evidence\n", encoding="utf-8")
    configured_root = tmp_path / "configured"
    configured_root.symlink_to(outside, target_is_directory=True)
    dest = tmp_path / "dest"
    dest.mkdir()

    with pytest.raises(ValueError, match="not a directory"):
        _copy_process_logs(configured_root, dest)

    assert list(dest.iterdir()) == []


def test_push_process_logs_reports_failure_for_symlinked_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    configured_root = tmp_path / "configured"
    try:
        configured_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    target = LocalTarget({"path": str(tmp_path / "dest")})

    result = target.push_process_logs(configured_root, "m1")

    assert not result.ok
    assert "process-log" in result.detail


def test_copy_process_logs_skips_file_rotated_away_mid_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A live log rotated/deleted between this pass's directory scan and its
    per-file open must be skipped as an ordinary candidate, never crash the
    whole process-log push -- disappearance is expected for a live source."""
    from agent_logger.sync.targets import filesystem
    from agent_logger.sync.targets.filesystem import _copy_process_logs

    logs = _make_process_logs(tmp_path)
    survivor = logs / "process-555-5.log"
    survivor.write_text("still here\n", encoding="utf-8")
    victim = logs / "process-111-1.log"
    dest = tmp_path / "dest"
    dest.mkdir()

    if os.name != "nt":
        real_open_regular_at = filesystem._process_logs.open_regular_at

        def flaky_open_regular_at(dir_fd, name):
            if name == victim.name:
                victim.unlink()
                raise FileNotFoundError(2, "No such file or directory", name)
            return real_open_regular_at(dir_fd, name)

        monkeypatch.setattr(filesystem._process_logs, "open_regular_at", flaky_open_regular_at)
    else:
        real_needs_copy = filesystem._needs_copy

        def flaky_needs_copy(src, dst):
            if src.name == victim.name:
                victim.unlink()
            return real_needs_copy(src, dst)

        monkeypatch.setattr(filesystem, "_needs_copy", flaky_needs_copy)

    copied, _nbytes, locked = _copy_process_logs(logs, dest)

    assert locked == []
    assert not (dest / victim.name).exists()
    assert (dest / survivor.name).read_text(encoding="utf-8") == "still here\n"
    assert copied >= 1


def test_copy_stream_replace_rejects_source_changed_during_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If the source file's identity/size changes between opening it and
    finishing the byte copy (e.g. rotated/truncated-and-rewritten under the
    same name), the destination must not land -- landing it anyway would
    carry the OLD bytes under the replacement's NEW size/mtime, which would
    make a future incremental size/mtime check believe the replacement is
    already synced, permanently masking it. Metadata/identity must come
    from the already-open descriptor (``os.fstat``), never a later
    path-based restat that could observe the replacement instead."""
    from agent_logger.sync.targets.filesystem import (
        _copy_stream_replace,
        _SourceChangedDuringCopy,
    )

    src = tmp_path / "process-111-1.log"
    src.write_text("original\n", encoding="utf-8")
    dst = tmp_path / "dest.log"

    real_fsync = os.fsync
    state = {"rotated": False}

    def flaky_fsync(fd):
        if not state["rotated"]:
            # Fires right after the byte copy's own fsync, before this
            # function's post-copy fstat -- simulates the source changing
            # under the still-open descriptor mid-transfer.
            state["rotated"] = True
            src.write_text("replaced with different length\n", encoding="utf-8")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", flaky_fsync)

    with open(src, "rb") as stream:
        with pytest.raises(_SourceChangedDuringCopy):
            _copy_stream_replace(stream, dst)

    assert not dst.exists()


@pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW directory pinning is POSIX-only")
def test_copy_process_logs_detects_rename_based_rotation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A rename-based rotation (the writer renames the old file aside and
    creates a fresh one under the same name) never changes the already-open
    descriptor's own identity -- the fd-only `_SourceChangedDuringCopy`
    check in `_copy_stream_replace` cannot observe it. `_copy_process_logs`
    must catch it anyway by revalidating the directory ENTRY's identity
    (by name, via the pinned root_fd) BEFORE committing the replace, so a
    same-name replacement can neither be mistaken for "already synced" NOR
    destroy a previously landed, still-good destination copy."""
    import contextlib

    from agent_logger.sync.targets import filesystem
    from agent_logger.sync.targets.filesystem import _copy_process_logs

    logs = _make_process_logs(tmp_path)
    target = logs / "process-111-1.log"
    target.write_text("original\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()
    # A previously synced, still-good copy of this same destination name --
    # the fix this test guards must never delete it.
    (dest / "process-111-1.log").write_text("previously synced\n", encoding="utf-8")

    real_open_regular_at = filesystem._process_logs.open_regular_at

    @contextlib.contextmanager
    def rotating_open_regular_at(dir_fd, name):
        with real_open_regular_at(dir_fd, name) as stream:
            if name == target.name:
                # The copy now holds an fd to the ORIGINAL inode (opened
                # before this rename, same as a real rename-rotation race)
                # -- reassign the name to a fresh inode while that fd is
                # still in use, before any bytes are read from it.
                target.rename(logs / "process-111-1.log.bak")
                target.write_text("original\n", encoding="utf-8")
            yield stream

    monkeypatch.setattr(
        filesystem._process_logs, "open_regular_at", rotating_open_regular_at,
    )

    _copied, _nbytes, locked = _copy_process_logs(logs, dest)

    assert (
        dest / "process-111-1.log"
    ).read_text(encoding="utf-8") == "previously synced\n"
    assert any(path.name == "process-111-1.log" for path in locked)


def test_engine_run_sync_reports_failure_when_process_log_push_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A failed opt-in process-log push must surface as a nonzero exit code
    (after any remaining cleanup/notify work still runs) -- the base
    unsupported-capability response already returns ok=True, so ok=False
    here specifically represents a real failure schedulers/monitoring must
    not silently record as a successful sync."""
    from agent_logger.sync.targets import filesystem
    from agent_logger.sync.targets.base import PushResult

    src = _make_source(tmp_path)
    (src / "logs").mkdir()
    (src / "logs" / "process-999-9.log").write_text("evidence\n", encoding="utf-8")
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)
    data = dict(cfg.as_dict())
    data["sync"]["process_logs"] = {"enabled": True}
    cfg = Config(data, cfg.home)

    monkeypatch.setattr(
        filesystem.LocalTarget,
        "push_process_logs",
        lambda self, log_root, machine: PushResult(ok=False, detail="simulated failure"),
    )

    assert engine.run_sync(cfg) == 1
    # The session-state push itself still landed -- only the process-log
    # leg failed.
    machines = list(dest.iterdir())
    assert len(machines) == 1
    assert (machines[0] / "session-state" / "abc-123" / "events.jsonl").is_file()


def test_engine_run_sync_publishes_process_logs_when_enabled(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    (src / "logs").mkdir()
    (src / "logs" / "process-999-9.log").write_text("evidence\n", encoding="utf-8")
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)
    data = dict(cfg.as_dict())
    data["sync"]["process_logs"] = {"enabled": True}
    cfg = Config(data, cfg.home)

    assert engine.run_sync(cfg) == 0

    machines = list(dest.iterdir())
    assert len(machines) == 1
    assert (
        machines[0] / "logs" / "process-999-9.log"
    ).read_text(encoding="utf-8") == "evidence\n"


def test_engine_run_sync_skips_process_logs_when_repo_scoped(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    (src / "logs").mkdir()
    (src / "logs" / "process-999-9.log").write_text("evidence\n", encoding="utf-8")
    dest = tmp_path / "dest"
    data = dict(load_config(home=tmp_path / "home").as_dict())
    data["sync"]["source"] = str(src)
    data["sync"]["targets"]["local"]["path"] = str(dest)
    data["sync"]["process_logs"] = {"enabled": True}
    data["sync"]["repo_allowlist"] = ["some-other-repo"]
    cfg = Config(data, tmp_path / "home")

    assert engine.run_sync(cfg, verbose=True) == 0

    machines = list(dest.iterdir())
    assert len(machines) == 1
    assert not (machines[0] / "logs").exists()


def test_engine_run_sync_process_logs_disabled_by_default(tmp_path: Path) -> None:
    src = _make_source(tmp_path)
    (src / "logs").mkdir()
    (src / "logs" / "process-999-9.log").write_text("evidence\n", encoding="utf-8")
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)

    assert engine.run_sync(cfg) == 0

    machines = list(dest.iterdir())
    assert len(machines) == 1
    assert not (machines[0] / "logs").exists()


def test_engine_run_sync_reports_unsupported_target_without_verbose(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    """An operator who explicitly enables sync.process_logs on a target that
    doesn't support it (SSH/ingest) must see that it's a no-op on every
    ordinary scheduled run -- not only when --verbose is passed."""
    from agent_logger.sync.targets import filesystem
    from agent_logger.sync.targets.base import PushResult

    src = _make_source(tmp_path)
    (src / "logs").mkdir()
    dest = tmp_path / "dest"
    cfg = _cfg(tmp_path / "home", src, dest)
    data = dict(cfg.as_dict())
    data["sync"]["process_logs"] = {"enabled": True}
    cfg = Config(data, cfg.home)

    monkeypatch.setattr(
        filesystem.LocalTarget,
        "push_process_logs",
        lambda self, log_root, machine: PushResult(
            ok=True, detail=f"{self.describe()} does not support process-log sync",
        ),
    )

    assert engine.run_sync(cfg) == 0
    assert "does not support process-log sync" in capsys.readouterr().out


def test_push_process_logs_failure_downgrades_sync_meta_to_partial(tmp_path: Path) -> None:
    """A process-log leg failing after the session-state leg already wrote
    `sync-meta.json` with status `ok` must not leave that status standing --
    otherwise `session-sync status`/fleet health reports this machine as
    fresh and healthy despite the requested evidence not landing."""
    from agent_logger.sync import meta

    src = _make_source(tmp_path)
    dest_root = tmp_path / "dest"
    target = LocalTarget({"path": str(dest_root)})

    result = target.push(src, "m1")
    assert result.ok
    machine_root = dest_root / "m1"
    original = meta.read_sync_meta(machine_root)
    assert original["status"] == "ok"

    # A root that exists but is unsafe (a symlink) fails push_process_logs
    # via its own "unsafe process-log source" branch.
    outside = tmp_path / "outside"
    outside.mkdir()
    bad_root = tmp_path / "bad-logs-root"
    try:
        bad_root.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    plog_result = target.push_process_logs(bad_root, "m1")

    assert not plog_result.ok
    downgraded = meta.read_sync_meta(machine_root)
    assert downgraded["status"] == "partial"
    assert downgraded["session_count"] == original["session_count"]


def test_process_logs_enabled_and_source_config() -> None:
    from agent_logger.config import load_config

    cfg = load_config(home=Path("/tmp/does-not-exist-agent-logger-home"))
    assert cfg.process_logs_enabled is False
    assert cfg.process_logs_source == cfg.sync_source / "logs"
