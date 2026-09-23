"""Phase 2c: agent-logger's session-fetch cold-store provider.

Validates :mod:`agent_logger.cold_store` resolves a session across all three
tiers (local live directory, on-device compact archive, and the locally
synced corpus -- both unpacked and packed), matching the
`agent_bridge.cold_store` client's exit-code/payload contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_logger import cold_store, sessions


def _make_session(state_root: Path, sid: str, *, cwd: str = "C:/repo") -> Path:
    d = state_root / sid
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(
        '{"type":"session.start","data":{}}\n'
        '{"type":"session.end","data":{}}\n',
        encoding="utf-8",
    )
    (d / "workspace.yaml").write_text(
        f"id: {sid}\ncwd: {cwd}\ncreated_at: 2026-01-01T00:00:00Z\n"
        "updated_at: 2026-01-02T00:00:00Z\n",
        encoding="utf-8",
    )
    (d / "origin.json").write_text(
        json.dumps({"machine": "box", "source_repo": "example-repo"}),
        encoding="utf-8",
    )
    return d


def _cfg_stub(monkeypatch: pytest.MonkeyPatch, sync_path: Path) -> None:
    class _StubConfig:
        pass

    stub = _StubConfig()
    stub.sync_path = sync_path
    monkeypatch.setattr(cold_store, "load_config", lambda **_kw: stub)


def test_resolve_session_live_directory(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "copilot" / "session-state"
    _make_session(state_root, "s-live")
    monkeypatch.setattr(
        cold_store, "_local_state_root", lambda: state_root
    )
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [])

    ref = cold_store.resolve_session("s-live")
    assert ref is not None
    assert ref.kind == "live"


def test_resolve_session_on_device_archive(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "copilot" / "session-state"
    src = _make_session(state_root, "s-arch")
    archive_root = tmp_path / "archived-sessions"
    sessions.archive_session(src, archive_root)
    sessions.force_rmtree(src)

    monkeypatch.setattr(
        cold_store, "_local_state_root", lambda: state_root
    )
    monkeypatch.setattr(
        cold_store, "session_archive_stores", lambda: [archive_root]
    )

    ref = cold_store.resolve_session("s-arch")
    assert ref is not None
    assert ref.kind == "archive"


def test_resolve_session_synced_unpacked(tmp_path: Path, monkeypatch) -> None:
    corpus_root = tmp_path / "sessions"
    _make_session(corpus_root / "box" / "session-state", "s-synced")

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)
    _cfg_stub(monkeypatch, corpus_root)

    ref = cold_store.resolve_session("s-synced")
    assert ref is not None
    assert ref.kind == "live"
    assert ref.path == corpus_root / "box" / "session-state" / "s-synced"


def test_resolve_session_synced_packed(tmp_path: Path, monkeypatch) -> None:
    corpus_root = tmp_path / "sessions"
    src = _make_session(tmp_path / "raw", "s-packed")
    archived_store = corpus_root / "box" / "archived"
    sessions.archive_session(src, archived_store)

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)
    _cfg_stub(monkeypatch, corpus_root)

    ref = cold_store.resolve_session("s-packed")
    assert ref is not None
    assert ref.kind == "archive"


def test_resolve_session_not_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)
    _cfg_stub(monkeypatch, tmp_path / "sessions")

    assert cold_store.resolve_session("nope") is None


@pytest.mark.skipif(
    not hasattr(Path, "symlink_to"), reason="platform lacks symlink support"
)
def test_resolve_session_rejects_symlinked_live_session_dir(
    tmp_path: Path, monkeypatch
) -> None:
    """A symlinked session directory under the live root must not resolve.

    Mirrors ``SyncedSessionSource``'s own no-link boundary: a symlink named
    exactly the requested session id could otherwise redirect
    ``session-fetch`` to read an arbitrary directory's ``events.jsonl``.
    """
    outside = _make_session(tmp_path / "outside", "real-target")
    state_root = tmp_path / "copilot" / "session-state"
    state_root.mkdir(parents=True)
    link = state_root / "linked-sess"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: state_root)
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [])

    assert cold_store.resolve_session("linked-sess") is None


@pytest.mark.skipif(
    not hasattr(Path, "symlink_to"), reason="platform lacks symlink support"
)
def test_resolve_session_rejects_symlinked_corpus_machine_dir(
    tmp_path: Path, monkeypatch
) -> None:
    """A symlinked ``<machine>/`` subtree under the synced corpus is rejected."""
    outside = tmp_path / "outside"
    _make_session(outside / "session-state", "s-outside")
    corpus_root = tmp_path / "sessions"
    corpus_root.mkdir(parents=True)
    try:
        (corpus_root / "linked-machine").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)
    _cfg_stub(monkeypatch, corpus_root)

    assert cold_store.resolve_session("s-outside") is None


@pytest.mark.skipif(
    not hasattr(Path, "symlink_to"), reason="platform lacks symlink support"
)
def test_resolve_session_rejects_symlinked_live_events_member(
    tmp_path: Path, monkeypatch
) -> None:
    """A live session dir that is real, but whose ``events.jsonl`` member is a
    symlink to an outside file, must not resolve -- the directory-chain check
    alone is not enough; individual members must be validated too."""
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    state_root = tmp_path / "copilot" / "session-state"
    sess_dir = state_root / "s-linked-member"
    sess_dir.mkdir(parents=True)
    try:
        (sess_dir / "events.jsonl").symlink_to(secret)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")
    (sess_dir / "workspace.yaml").write_text("cwd: /tmp\n", encoding="utf-8")

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: state_root)
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [])

    assert cold_store.resolve_session("s-linked-member") is None


@pytest.mark.skipif(
    not hasattr(Path, "symlink_to"), reason="platform lacks symlink support"
)
def test_resolve_session_rejects_symlinked_archive_sidecar(
    tmp_path: Path, monkeypatch
) -> None:
    """A real archive whose uncompressed ``workspace.yaml`` sidecar is a
    symlink to an outside file must not resolve."""
    secret = tmp_path / "secret.yaml"
    secret.write_text("cwd: /outside\n", encoding="utf-8")
    src = _make_session(tmp_path / "raw", "s-linked-sidecar")
    archive_root = tmp_path / "archived-sessions"
    sessions.archive_session(src, archive_root)
    sidecar = archive_root / "s-linked-sidecar.workspace.yaml"
    sidecar.unlink()
    try:
        sidecar.symlink_to(secret)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: tmp_path / "copilot" / "session-state")
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [archive_root])

    assert cold_store.resolve_session("s-linked-sidecar") is None


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "../escaped",
        "..\\escaped",
        "a/../../escaped",
        "a\\..\\..\\escaped",
        "/etc/passwd",
        "\\\\server\\share",
        "C:\\Windows\\System32",
        "",
        ".",
        "..",
    ],
)
def test_resolve_session_rejects_unsafe_ids(
    tmp_path: Path, monkeypatch, unsafe_id: str
) -> None:
    """A path-traversal/absolute/anchored session id is never resolved.

    ``session_id`` reaches ``Path`` composition unvalidated from the daemon's
    request path; an unsafe id must be treated as "not found", not resolved
    against an escaped location.
    """
    state_root = tmp_path / "copilot" / "session-state"
    _make_session(state_root, "s-real")
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: state_root)
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [])

    assert cold_store.resolve_session(unsafe_id) is None


def test_resolve_session_accepts_normal_uuid_like_id(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = tmp_path / "copilot" / "session-state"
    _make_session(state_root, "3f9c1a2b-4d5e-6f70-8192-a3b4c5d6e7f8")
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: state_root)
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [])

    ref = cold_store.resolve_session("3f9c1a2b-4d5e-6f70-8192-a3b4c5d6e7f8")
    assert ref is not None


def test_build_session_payload_shape(tmp_path: Path) -> None:
    state_root = tmp_path / "copilot" / "session-state"
    src = _make_session(state_root, "s-payload")
    ref = sessions.resolve_ref("s-payload", state_root)
    assert ref is not None

    payload = cold_store.build_session_payload(ref)
    assert payload["session"]["session_id"] == "s-payload"
    assert payload["session"]["cwd"] == "C:/repo"
    assert payload["session"]["project"] == "example-repo"
    assert payload["session"]["created_at"] == "2026-01-01T00:00:00Z"
    assert len(payload["events"]) == 2
    assert payload["events"][0]["type"] == "session.start"
    assert src.is_dir()  # sanity: still a live dir, not consumed


def test_build_session_payload_skips_malformed_event_lines(tmp_path: Path) -> None:
    d = tmp_path / "session-state" / "s-bad"
    d.mkdir(parents=True)
    (d / "events.jsonl").write_text(
        '{"type":"ok"}\nnot json\n["array-not-object"]\n', encoding="utf-8"
    )
    ref = sessions.SessionRef(id="s-bad", kind="live", path=d)

    payload = cold_store.build_session_payload(ref)
    assert payload["events"] == [{"type": "ok"}]


def test_fetch_session_json_found(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "copilot" / "session-state"
    _make_session(state_root, "s-fetch")
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: state_root)
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [])

    code, payload = cold_store.fetch_session_json("s-fetch")
    assert code == 0
    data = json.loads(payload)
    assert data["session"]["session_id"] == "s-fetch"


def test_fetch_session_json_not_found(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)
    _cfg_stub(monkeypatch, tmp_path / "sessions")

    code, payload = cold_store.fetch_session_json("does-not-exist")
    assert code == cold_store.NOT_FOUND_EXIT_CODE
    assert payload == ""


def test_fetch_session_json_rejects_traversal(tmp_path: Path, monkeypatch) -> None:
    state_root = tmp_path / "copilot" / "session-state"
    _make_session(state_root, "s-real")
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: state_root)
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [])

    code, payload = cold_store.fetch_session_json("../s-real")
    assert code == cold_store.NOT_FOUND_EXIT_CODE
    assert payload == ""


# --- query_reviewer_sessions -----------------------------------------------

def _cfg_stub_with_catalog(
    monkeypatch: pytest.MonkeyPatch, sync_path: Path, catalog_db_path: Path,
) -> None:
    class _StubConfig:
        pass

    stub = _StubConfig()
    stub.sync_path = sync_path
    stub.catalog_db_path = catalog_db_path
    monkeypatch.setattr(cold_store, "load_config", lambda **_kw: stub)


def test_query_reviewer_sessions_resolves_catalog_hits(
    tmp_path: Path, monkeypatch,
) -> None:
    from agent_logger.catalog import ReviewCatalogIndex

    state_root = tmp_path / "copilot" / "session-state"
    _make_session(state_root, "s1")
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: state_root)
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [])
    _cfg_stub_with_catalog(monkeypatch, tmp_path / "sessions", tmp_path / "catalog.db")

    index = ReviewCatalogIndex(tmp_path / "catalog.db")
    index.record(
        session_id="s1", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )

    refs = cold_store.query_reviewer_sessions("example/repo", 6100)

    assert len(refs) == 1
    assert refs[0].id == "s1"


def test_query_reviewer_sessions_skips_unresolvable_sessions(
    tmp_path: Path, monkeypatch,
) -> None:
    from agent_logger.catalog import ReviewCatalogIndex

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)
    _cfg_stub_with_catalog(monkeypatch, tmp_path / "sessions", tmp_path / "catalog.db")

    index = ReviewCatalogIndex(tmp_path / "catalog.db")
    index.record(
        session_id="ghost", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )

    refs = cold_store.query_reviewer_sessions("example/repo", 6100)

    assert refs == []


def test_query_reviewer_sessions_empty_catalog_returns_empty(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)
    _cfg_stub_with_catalog(monkeypatch, tmp_path / "sessions", tmp_path / "catalog.db")

    refs = cold_store.query_reviewer_sessions("example/repo", 6100)

    assert refs == []


def test_query_reviewer_sessions_dedupes_by_session_id(
    tmp_path: Path, monkeypatch,
) -> None:
    from agent_logger.catalog import ReviewCatalogIndex

    state_root = tmp_path / "copilot" / "session-state"
    _make_session(state_root, "s1")
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: state_root)
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [])
    _cfg_stub_with_catalog(monkeypatch, tmp_path / "sessions", tmp_path / "catalog.db")

    index = ReviewCatalogIndex(tmp_path / "catalog.db")
    index.record(
        session_id="s1", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )
    index.record(
        session_id="s1", repo="example/repo", pr_number=6100,
        role="author", recorded_at="2026-09-22T19:05:00Z",
    )

    refs = cold_store.query_reviewer_sessions("example/repo", 6100)

    assert len(refs) == 1
