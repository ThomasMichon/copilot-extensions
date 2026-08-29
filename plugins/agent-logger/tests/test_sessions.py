"""Tests for cold-session archival and archive-aware access (sessions.py)."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from agent_logger.sessions import (
    CODECS,
    SessionRef,
    archive_session,
    force_rmtree,
    get_codec,
    is_archived,
    iter_session_refs,
    materialize,
    member_exists,
    read_member,
    read_origin,
    read_workspace,
    remove_archive,
    resolve_ref,
    restore_session,
    verify_archive,
)


def _make_session(state_root: Path, sid: str, *, cwd: str = "C:/repo") -> Path:
    """Create a realistic live session directory."""
    d = state_root / sid
    (d / "checkpoints").mkdir(parents=True)
    (d / "events.jsonl").write_text(
        '{"type":"session.start","data":{}}\n{"type":"turn"}\n', encoding="utf-8"
    )
    (d / "workspace.yaml").write_text(
        f"id: {sid}\ncwd: {cwd}\nupdated_at: 2026-01-01T00:00:00Z\n",
        encoding="utf-8",
    )
    (d / "origin.json").write_text(
        json.dumps({"machine": "box", "source_repo": "dotfiles"}), encoding="utf-8"
    )
    (d / "checkpoints" / "index.md").write_text("# cp\n", encoding="utf-8")
    return d


# --- codec ----------------------------------------------------------------

def test_targz_registered_and_default() -> None:
    assert "targz" in CODECS
    assert get_codec("targz").suffix == ".tar.gz"


def test_get_codec_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown compression codec"):
        get_codec("zstd")


def test_archive_roundtrip_bytes(tmp_path: Path) -> None:
    src = _make_session(tmp_path / "session-state", "s1")
    store = tmp_path / "archived"
    ref = archive_session(src, store)
    assert ref.kind == "archive"
    assert ref.path.name == "s1.tar.gz"
    # events.jsonl comes back byte-identical through the tarball
    assert read_member(ref, "events.jsonl") == (src / "events.jsonl").read_bytes()
    # nested member survives byte-identically
    assert read_member(ref, "checkpoints/index.md") == (
        src / "checkpoints" / "index.md"
    ).read_bytes()


# --- sidecars -------------------------------------------------------------

def test_sidecars_written_uncompressed(tmp_path: Path) -> None:
    src = _make_session(tmp_path / "session-state", "s1")
    store = tmp_path / "archived"
    archive_session(src, store)
    assert (store / "s1.workspace.yaml").is_file()
    assert (store / "s1.origin.json").is_file()


def test_metadata_reads_use_sidecar_not_tarball(tmp_path: Path, monkeypatch) -> None:
    src = _make_session(tmp_path / "session-state", "s1")
    store = tmp_path / "archived"
    ref = archive_session(src, store)

    # If a sidecar read touched the tarball, opening it would blow up here.
    def _boom(*a, **k):
        raise AssertionError("sidecar read must not open the archive")

    monkeypatch.setattr(tarfile, "open", _boom)
    ws = read_workspace(ref)
    assert ws["id"] == "s1"
    assert ws["cwd"] == "C:/repo"
    assert read_origin(ref)["source_repo"] == "dotfiles"


# --- reads: live vs archive parity ---------------------------------------

def test_read_parity_live_and_archive(tmp_path: Path) -> None:
    src = _make_session(tmp_path / "session-state", "s1")
    live = SessionRef(id="s1", kind="live", path=src)
    arch = archive_session(src, tmp_path / "archived")

    assert read_workspace(live) == read_workspace(arch)
    assert read_origin(live) == read_origin(arch)
    assert member_exists(live, "events.jsonl")
    assert member_exists(arch, "events.jsonl")
    assert member_exists(arch, "checkpoints/index.md")
    assert not member_exists(arch, "nope.txt")
    assert read_member(arch, "nope.txt") is None


# --- materialize ----------------------------------------------------------

def test_materialize_live_yields_dir_itself(tmp_path: Path) -> None:
    src = _make_session(tmp_path / "session-state", "s1")
    live = SessionRef(id="s1", kind="live", path=src)
    with materialize(live) as d:
        assert d == src


def test_materialize_archive_extracts_and_cleans_up(tmp_path: Path) -> None:
    src = _make_session(tmp_path / "session-state", "s1")
    arch = archive_session(src, tmp_path / "archived")
    with materialize(arch) as d:
        assert d != src
        assert (d / "events.jsonl").is_file()
        assert (d / "checkpoints" / "index.md").read_text() == "# cp\n"
        extracted = d
    assert not extracted.exists()  # temp dir removed on exit


# --- discovery ------------------------------------------------------------

def test_iter_session_refs_unions_live_and_archive(tmp_path: Path) -> None:
    state = tmp_path / "session-state"
    _make_session(state, "live1")
    src2 = _make_session(state, "arch1")
    archive_session(src2, tmp_path / "archived")
    # remove the live copy of arch1 so it only exists archived
    for p in sorted((state / "arch1").rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    (state / "arch1").rmdir()

    refs = {r.id: r.kind for r in iter_session_refs(state, tmp_path / "archived")}
    assert refs == {"live1": "live", "arch1": "archive"}


def test_live_shadows_archive_same_id(tmp_path: Path) -> None:
    state = tmp_path / "session-state"
    src = _make_session(state, "dup")
    archive_session(src, tmp_path / "archived")  # both live and archived exist
    refs = list(iter_session_refs(state, tmp_path / "archived"))
    dup = [r for r in refs if r.id == "dup"]
    assert len(dup) == 1
    assert dup[0].kind == "live"


def test_resolve_ref_prefers_live(tmp_path: Path) -> None:
    state = tmp_path / "session-state"
    src = _make_session(state, "s1")
    archive_session(src, tmp_path / "archived")
    assert resolve_ref("s1", state, tmp_path / "archived").kind == "live"
    # archived-only resolves to archive
    for p in sorted((state / "s1").rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    (state / "s1").rmdir()
    assert resolve_ref("s1", state, tmp_path / "archived").kind == "archive"
    assert resolve_ref("missing", state, tmp_path / "archived") is None


# --- write-path helpers ---------------------------------------------------

def test_is_archived_and_remove(tmp_path: Path) -> None:
    src = _make_session(tmp_path / "session-state", "s1")
    store = tmp_path / "archived"
    ref = archive_session(src, store)
    assert is_archived("s1", store)
    assert verify_archive(ref)
    remove_archive(ref)
    assert not is_archived("s1", store)
    assert not (store / "s1.workspace.yaml").exists()


def test_restore_session(tmp_path: Path) -> None:
    src = _make_session(tmp_path / "session-state", "s1")
    ref = archive_session(src, tmp_path / "archived")
    dest = restore_session(ref, tmp_path / "restored")
    assert (dest / "events.jsonl").read_bytes() == (src / "events.jsonl").read_bytes()
    assert (dest / "checkpoints" / "index.md").read_text() == "# cp\n"


# --- safety ---------------------------------------------------------------

def test_extract_rejects_path_traversal(tmp_path: Path) -> None:
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo(name="../escape.txt")
        payload = b"pwned"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    ref = SessionRef(id="evil", kind="archive", path=evil, store=tmp_path)
    with pytest.raises(ValueError, match="unsafe archive member path"):
        with materialize(ref):
            pass
    assert not (tmp_path / "escape.txt").exists()


def test_no_such_codec_for_archive(tmp_path: Path) -> None:
    bogus = tmp_path / "s1.bogus"
    bogus.write_bytes(b"x")
    ref = SessionRef(id="s1", kind="archive", path=bogus, store=tmp_path)
    with pytest.raises(ValueError, match="no codec for archive"):
        read_member(ref, "events.jsonl")


# --- force_rmtree ---------------------------------------------------------

def test_archive_rejects_windows_absolute_member(tmp_path: Path) -> None:
    evil = tmp_path / "evil-windows.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        for name, payload in (
            ("events.jsonl", b"{}\n"),
            (r"C:\escape\owned.txt", b"owned"),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    ref = SessionRef(id="evil-windows", kind="archive", path=evil, store=tmp_path)

    assert not verify_archive(ref)
    with pytest.raises(ValueError, match="unsafe archive member path"):
        with materialize(ref):
            pass


def test_archive_rejects_windows_normalized_traversal(tmp_path: Path) -> None:
    evil = tmp_path / "evil-windows-normalized.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        for name, payload in (
            ("events.jsonl", b"{}\n"),
            (".. /escaped.txt", b"escaped"),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    ref = SessionRef(
        id="evil-windows-normalized",
        kind="archive",
        path=evil,
        store=tmp_path,
    )

    assert not verify_archive(ref)
    with pytest.raises(ValueError, match="unsafe archive member path"):
        with materialize(ref):
            pass


def test_force_rmtree_removes_readonly_tree(tmp_path: Path) -> None:
    import os
    import stat

    d = tmp_path / "ro"
    (d / "sub").mkdir(parents=True)
    f = d / "sub" / "file.txt"
    f.write_text("x", encoding="utf-8")
    os.chmod(f, stat.S_IREAD)  # read-only file defeats a plain rmtree on Windows
    assert force_rmtree(d) is True
    assert not d.exists()


def test_force_rmtree_missing_returns_true(tmp_path: Path) -> None:
    assert force_rmtree(tmp_path / "nope") is True
