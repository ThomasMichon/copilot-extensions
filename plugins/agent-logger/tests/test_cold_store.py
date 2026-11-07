"""Phase 2c: agent-logger's session-fetch cold-store provider.

Validates :mod:`agent_logger.cold_store` resolves a session across all three
tiers (local live directory, on-device compact archive, and the locally
synced corpus -- both unpacked and packed), matching the
`agent_bridge.cold_store` client's exit-code/payload contract.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest
import yaml

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


def test_resolve_session_synced_from_a_codespace(tmp_path: Path, monkeypatch) -> None:
    """agent-codespaces lands a CodeSpace's sessions under ``.codespaces/<name>/``."""
    corpus_root = tmp_path / "sessions"
    _make_session(corpus_root / ".codespaces" / "cs-one" / "session-state", "s-venue")
    _make_session(corpus_root / "box" / "session-state", "s-host")

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)
    _cfg_stub(monkeypatch, corpus_root)

    ref = cold_store.resolve_session("s-venue")
    assert ref is not None and ref.kind == "live"
    assert ref.path == corpus_root / ".codespaces" / "cs-one" / "session-state" / "s-venue"
    assert cold_store.resolve_session("s-host") is not None  # flat machines still resolve


def test_a_live_mirrored_codespace_session_resolves_and_a_close_out_copy_wins(
    tmp_path: Path, monkeypatch
) -> None:
    """The live mirror lands under ``.codespaces-live/<name>/``; a close-out capture
    of the same session under ``.codespaces/<name>/`` is the complete copy."""
    corpus_root = tmp_path / "sessions"
    _make_session(corpus_root / ".codespaces-live" / "cs-one" / "session-state", "s-running")
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)
    _cfg_stub(monkeypatch, corpus_root)

    ref = cold_store.resolve_session("s-running")
    assert ref is not None
    assert ref.path == corpus_root / ".codespaces-live" / "cs-one" / "session-state" / "s-running"
    _make_session(corpus_root / ".codespaces" / "cs-one" / "session-state", "s-running")
    ref = cold_store.resolve_session("s-running")
    assert ref.path == corpus_root / ".codespaces" / "cs-one" / "session-state" / "s-running"


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


@pytest.mark.parametrize(
    "session_id",
    [
        "", ".", "..", "../session", r"..\session", "/session", r"C:\session",
        "container:example", "session:stream", "session.", "session ", "CON",
        "nul.txt", "session?name", "session\x00name",
    ],
)
def test_fetch_session_json_rejects_noncanonical_ids_before_discovery(
    session_id: str, monkeypatch,
) -> None:
    def unexpected_discovery():
        pytest.fail("noncanonical session ID reached filesystem discovery")

    monkeypatch.setattr(cold_store, "_local_state_root", unexpected_discovery)

    code, payload = cold_store.fetch_session_json(session_id)
    assert code == cold_store.NOT_FOUND_EXIT_CODE
    assert payload == ""


@pytest.mark.parametrize("session_id", ["container:example", "session.", "CON"])
def test_session_fetch_cli_noncanonical_id_is_not_found(session_id: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agent_logger", "session-fetch", session_id, "--json"],
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == cold_store.NOT_FOUND_EXIT_CODE, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


# --- streamed payload: byte identity and bounded memory ---------------------

_CODECS = [None, "targz", "zip"]


def _chunk_straddling_events() -> bytes:
    """Lines whose multi-byte and invalid UTF-8 runs cross 8 KiB read chunks."""
    pattern = "é✓😀".encode() + b"\xff\xe2\x82c"
    return b"".join(
        b'{"type":"pad","data":{"s":"' + b"p" * pad + pattern * (9000 // len(pattern)) + b'"}}\n'
        for pad in range(len(pattern))
    )


_TRICKY_EVENTS = (
    b'{"type":"session.start","data":{"n":1}}\n'
    b"\n"
    b"   \n"
    b"not json\n"
    b'["array-not-object"]\n'
    b"42\n"
    b'{"type":"text","data":{"s":"caf\xc3\xa9 \xe2\x9c\x93 \xf0\x9f\x98\x80 \\u00e9"}}\n'
    b'{"type":"bad-utf8","data":{"s":"a\xff\xfeb\xe2\x82c"}}\n'
    b'{"type":"line-separator","data":{"s":"a\xe2\x80\xa8b"}}\n'
    b'{"type":"crlf"}\r\n'
    b'{"type":"cr"}\r{"type":"after-cr"}\n'
    + _chunk_straddling_events()
    + b'{"type":"no-trailing-newline"}'
)


def _legacy_session_json(ref: sessions.SessionRef) -> str:
    """The pre-streaming whole-member implementation: the byte-identity oracle."""
    raw = sessions.read_member(ref, "events.jsonl")
    events = []
    if raw is not None:
        for line in io.StringIO(raw.decode("utf-8", errors="replace")):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    ws = sessions.read_workspace(ref)
    origin = sessions.read_origin(ref)
    session = {
        "session_id": ref.id,
        "cwd": ws.get("cwd") or None,
        "project": origin.get("source_repo") if isinstance(origin, dict) else None,
        "created_at": ws.get("created_at") or None,
        "updated_at": ws.get("updated_at") or None,
    }
    return json.dumps({"session": session, "events": events})


def _store_session(root: Path, sid: str, events: bytes, codec: str | None):
    """Create ``sid`` live or archived under ``root``; return (state_root, stores)."""
    state_root = root / "copilot" / "session-state"
    src = _make_session(state_root, sid)
    (src / "events.jsonl").write_bytes(events)
    if codec is None:
        return state_root, []
    archive_root = root / "archived-sessions"
    sessions.archive_session(src, archive_root, codec=codec)
    sessions.force_rmtree(src)
    return state_root, [archive_root]


def _use_stores(monkeypatch: pytest.MonkeyPatch, state_root: Path, stores: list[Path]) -> None:
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: state_root)
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: list(stores))


@pytest.mark.parametrize("codec", _CODECS, ids=["live", "targz", "zip"])
@pytest.mark.parametrize(
    "events",
    [_TRICKY_EVENTS, b"", b"\n \n", b'{"only":1}'],
    ids=["tricky", "empty", "blank", "single"],
)
def test_streamed_session_json_is_byte_identical_to_legacy(
    tmp_path: Path, monkeypatch, codec: str | None, events: bytes,
) -> None:
    state_root, stores = _store_session(tmp_path, "s-stream", events, codec)
    _use_stores(monkeypatch, state_root, stores)
    ref = cold_store.resolve_session("s-stream")
    assert ref is not None and ref.kind == ("live" if codec is None else "archive")
    expected = _legacy_session_json(ref)

    out = io.StringIO()
    assert cold_store.write_session_json("s-stream", out) == 0
    assert out.getvalue() == expected
    assert cold_store.fetch_session_json("s-stream") == (0, expected)
    assert json.dumps(cold_store.build_session_payload(ref)) == expected
    if events is _TRICKY_EVENTS:
        types = [event["type"] for event in json.loads(expected)["events"]]
        assert types[:5] == ["session.start", "text", "bad-utf8", "line-separator", "crlf"]
        assert types[-1] == "no-trailing-newline"
        assert "\ufffd" in json.loads(expected)["events"][2]["data"]["s"]


def test_write_session_json_not_found_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)
    _cfg_stub(monkeypatch, tmp_path / "sessions")

    out = io.StringIO()
    assert cold_store.write_session_json("does-not-exist", out) == cold_store.NOT_FOUND_EXIT_CODE
    assert out.getvalue() == ""


def test_session_fetch_cli_streams_the_legacy_payload(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state_root = home / ".copilot" / "session-state"
    (_make_session(state_root, "s-cli") / "events.jsonl").write_bytes(_TRICKY_EVENTS)
    ref = sessions.resolve_ref("s-cli", state_root)
    assert ref is not None
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "AGENT_LOGGER_HOME": str(tmp_path / "agent-logger-home"),
    }

    result = subprocess.run(
        [sys.executable, "-m", "agent_logger", "session-fetch", "s-cli", "--json"],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == _legacy_session_json(ref) + "\n"


@pytest.mark.parametrize("codec", _CODECS, ids=["live", "targz", "zip"])
def test_open_member_stream_yields_none_for_missing_member(tmp_path: Path, codec: str | None) -> None:
    state_root, stores = _store_session(tmp_path, "s-none", b"{}\n", codec)
    ref = sessions.resolve_ref("s-none", state_root, *stores)
    assert ref is not None and ref.kind == ("live" if codec is None else "archive")
    with sessions.open_member_stream(ref, "missing.jsonl") as raw:
        assert raw is None
    with sessions.open_member_stream(ref, "events.jsonl") as raw:
        assert raw is not None and raw.read() == b"{}\n"


#: Large enough that whole-file buffering (bytes + decoded text + parsed
#: events + encoded payload) would dwarf the asserted bound.
_LARGE_TRANSCRIPT_BYTES = 24 * 1024 * 1024


@pytest.fixture(scope="module")
def large_sessions(tmp_path_factory: pytest.TempPathFactory) -> dict:
    filler = "x" * 2000
    events = io.BytesIO()
    i = 0
    while events.tell() < _LARGE_TRANSCRIPT_BYTES:
        events.write((json.dumps({
            "type": "tool.execution_complete",
            "data": {"toolCallId": f"c{i}", "result": {"content": f"{i} {filler}"}},
        }) + "\n").encode())
        i += 1
    data = events.getvalue()
    del events
    stored = {}
    for codec in _CODECS:
        root = tmp_path_factory.mktemp(f"large-{codec or 'live'}")
        stored[codec] = (*_store_session(root, "s-large", data, codec), len(data))
    return stored


class _HashingSink:
    """A ``TextIO`` stand-in that keeps only a digest of what it receives."""

    def __init__(self) -> None:
        self.digest = hashlib.sha256()
        self.chars = 0

    def write(self, text: str) -> int:
        self.digest.update(text.encode())
        self.chars += len(text)
        return len(text)


def _traced_peak(fn) -> tuple[object, int]:
    tracemalloc.start()
    try:
        result = fn()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, peak


@pytest.mark.parametrize("codec", _CODECS, ids=["live", "targz", "zip"])
def test_session_fetch_memory_is_bounded(large_sessions, monkeypatch, codec) -> None:
    state_root, stores, size = large_sessions[codec]
    _use_stores(monkeypatch, state_root, stores)
    sink = _HashingSink()

    code, peak = _traced_peak(lambda: cold_store.write_session_json("s-large", sink))

    assert code == 0
    assert sink.chars > size  # every event was re-serialized
    print(f"session-fetch[{codec or 'live'}]: peak {peak} bytes for {size}-byte transcript")
    assert peak < size // 4


@pytest.mark.parametrize("codec", _CODECS, ids=["live", "targz", "zip"])
def test_open_events_stream_memory_is_bounded(large_sessions, codec) -> None:
    state_root, stores, size = large_sessions[codec]
    ref = sessions.resolve_ref("s-large", state_root, *stores)
    assert ref is not None

    def read_all() -> int:
        total = 0
        with sessions.open_events_stream(ref) as lines:
            for line in lines:
                total += len(line)
        return total

    total, peak = _traced_peak(read_all)

    assert total == size
    print(f"open_events_stream[{codec or 'live'}]: peak {peak} bytes for {size}-byte transcript")
    assert peak < size // 4


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


@pytest.mark.no_autotrust
def test_resolve_session_honors_repo_local_sync_local_path(monkeypatch, tmp_path):
    """resolve_session()'s synced-corpus tier must resolve schema v3's
    repo-local sync.local_path when no cfg is passed in (the real fallback
    path scheduled/CLI callers use), not just the ambient CLI config
    command. Exercises the real (unstubbed) load_config() -> repo_trust
    path, unlike this file's other tests which stub load_config entirely."""
    from .conftest import init_git_repo

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)

    repo = tmp_path / "repo"
    init_git_repo(repo, remote="https://example.test/example-owner/demo.git", branch="main")
    (repo / ".agent-logger.yaml").write_text(
        "schema_version: 3\nsync:\n  local_path: "
        + str(tmp_path / "declared-target")
        + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "repos.yaml"
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

    def _fake_resolve_from_synced_corpus(session_id, sync_path):
        captured["sync_path"] = sync_path
        return None

    monkeypatch.setattr(
        cold_store, "_resolve_from_synced_corpus", _fake_resolve_from_synced_corpus
    )

    assert cold_store.resolve_session("some-session-id") is None
    assert captured["sync_path"] == tmp_path / "declared-target"


@pytest.mark.no_autotrust
def test_query_reviewer_sessions_honors_repo_local_sync_local_path(
    monkeypatch, tmp_path: Path
) -> None:
    """query_reviewer_sessions()'s OWN load_config() call (the no-cfg
    fallback the session-fetch CLI uses) must also resolve schema v3's
    repo-local sync.local_path -- a regression to
    load_config(include_repo=False) at THAT call site (a distinct line
    from resolve_session's own, and not exercised by it, since a cfg
    resolve_session already has takes priority over calling load_config()
    again) would pass every other query_reviewer_sessions test in this
    file, which all stub load_config entirely. Mirrors
    test_resolve_session_honors_repo_local_sync_local_path's pattern, but
    driven through an actual catalog entry."""
    from agent_logger.catalog import ReviewCatalogIndex

    from .conftest import init_git_repo

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: None)

    repo = tmp_path / "repo"
    init_git_repo(repo, remote="https://example.test/example-owner/demo.git", branch="main")
    (repo / ".agent-logger.yaml").write_text(
        "schema_version: 3\nsync:\n  local_path: "
        + str(tmp_path / "declared-target")
        + "\n",
        encoding="utf-8",
    )
    registry = tmp_path / "repos.yaml"
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
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_WORKTREES_REPOS_YAML", str(registry))
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(home))
    monkeypatch.chdir(repo)

    index = ReviewCatalogIndex(home / "review-catalog.db")
    index.record(
        session_id="s1", repo="example/repo", pr_number=6100,
        role="reviewer", recorded_at="2026-09-22T19:00:00Z",
    )

    captured = {}

    def _fake_resolve_from_synced_corpus(session_id, sync_path):
        captured["sync_path"] = sync_path
        return None

    monkeypatch.setattr(
        cold_store, "_resolve_from_synced_corpus", _fake_resolve_from_synced_corpus
    )

    refs = cold_store.query_reviewer_sessions("example/repo", 6100)

    assert refs == []
    assert captured["sync_path"] == tmp_path / "declared-target"
