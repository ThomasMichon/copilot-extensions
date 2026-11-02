"""Root attention is bounded last-report evidence, never transcript inference."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_worktrees import session_attention as provider


@pytest.fixture
def pulse(tmp_path, monkeypatch):
    root = tmp_path / "session-state"
    entry = root / "session-1"
    entry.mkdir(parents=True)
    monkeypatch.setattr(provider.sessions, "_session_state_dir", lambda: root)
    monkeypatch.setattr(provider.sessions, "read_session_transcript",
                        lambda *a, **k: pytest.fail("transcript read"))
    monkeypatch.setattr(provider.sessions, "_infer_rest_from_events",
                        lambda *a, **k: pytest.fail("events read"))
    return entry / "substatus.json"


def write(pulse, **changes):
    data = {
        "sessionId": "session-1", "rest": "awaiting-operator",
        "restAt": "2020-01-02T03:04:05.000Z",
        "updatedAt": "2026-01-02T03:04:05.000Z",
        "intent": "private-intent", "request": "private-request",
    }
    data.update(changes)
    pulse.write_text(json.dumps(data), encoding="utf-8")
    return data


@pytest.mark.parametrize("rest", ["busy", "idle", "awaiting-operator"])
def test_last_reported_states_are_not_ttl_expired_or_claimed_live(pulse, rest):
    write(pulse, rest=rest)
    before = pulse.read_bytes()
    result = provider.session_attention("session-1")
    assert result["contract"] == provider.CONTRACT
    assert result["version"] == 1 and result["supported_versions"] == [1]
    assert result["availability"] == "observed" and result["rest"] == rest
    assert result["evidence_scope"] == "last_reported_root_state"
    assert result["runtime_continuity"] == "unknown"
    assert result["reported_at"] == "2020-01-02T03:04:05+00:00"
    assert result["reported_at_source"] == "restAt"
    assert result["as_of"] and result["issues"] == {}
    assert "private-" not in json.dumps(result)
    assert pulse.read_bytes() == before


def test_initial_busy_does_not_use_intent_timestamp(pulse):
    write(pulse, rest="busy", restAt=None)
    result = provider.session_attention("session-1")
    assert result["availability"] == "observed" and result["rest"] == "busy"
    assert result["reported_at"] is None and result["reported_at_source"] is None


@pytest.mark.parametrize("changes,issue", [
    ({"sessionId": "other"}, "session_identity_mismatch"),
    ({"sessionId": None}, "session_identity_mismatch"),
    ({"rest": None}, "unknown_root_state"),
    ({"rest": []}, "unknown_root_state"),
    ({"rest": "private-invented"}, "unknown_root_state"),
    ({"restAt": None}, "invalid_reported_at"),
    ({"restAt": "private-invalid"}, "invalid_reported_at"),
    ({"restAt": "2026-01-02"}, "invalid_reported_at"),
    ({"restAt": 1}, "invalid_reported_at"),
    ({"restAt": "x" * 65}, "invalid_reported_at"),
])
def test_invalid_evidence_is_unknown_and_sanitized(pulse, changes, issue):
    write(pulse, **changes)
    result = provider.session_attention("session-1")
    assert result["availability"] == "unknown" and result["rest"] is None
    assert result["reported_at"] is None and result["issues"] == {issue: 1}
    assert "private-" not in json.dumps(result)


def test_legacy_idle_and_missing_transition_do_not_infer_attention(pulse):
    for data in ({"idle": True}, {"sessionId": "session-1", "rest": "idle"}):
        pulse.write_text(json.dumps(data), encoding="utf-8")
        assert provider.session_attention("session-1")["availability"] == "unknown"


@pytest.mark.parametrize("raw", [b"", b"{", b"[]", b"\xff", b'{"sessionId":'])
def test_partial_or_malformed_source(pulse, raw):
    pulse.write_bytes(raw)
    result = provider.session_attention("session-1")
    assert result["issues"] == {"malformed_source": 1}


@pytest.mark.parametrize("session_id", [
    "../session-1", r"..\session-1", "C:session-1", "/session-1",
    ".", "..", "a..b", "", "x" * 129, "private-\nrequest",
])
def test_invalid_ids_are_not_read_or_echoed(pulse, monkeypatch, session_id):
    monkeypatch.setattr(provider.sessions, "_session_state_dir",
                        lambda: pytest.fail("invalid ID reached filesystem"))
    result = provider.session_attention(session_id)
    assert result["issues"] == {"invalid_session_id": 1}
    assert result["session_id"] is None


def test_missing_unreadable_and_non_regular_source(pulse, monkeypatch):
    assert provider.session_attention("session-1")["issues"] == {"source_missing": 1}
    pulse.mkdir()
    assert provider.session_attention("session-1")["issues"] == {"not_regular_file": 1}
    monkeypatch.setattr(Path, "stat", lambda *a, **k: (_ for _ in ()).throw(
        PermissionError("private-path")))
    result = provider.session_attention("session-1")
    assert result["availability"] == "unknown"
    assert result["issues"] == {"source_unreadable": 1}


def test_size_limit_refuses_before_open(pulse, monkeypatch):
    pulse.write_bytes(b"x" * (provider.MAX_BYTES + 1))
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("oversized source read"))
    assert provider.session_attention("session-1")["issues"] == {"oversized_source": 1}


def test_exact_limit_is_readable(pulse):
    write(pulse)
    pulse.write_bytes(pulse.read_bytes().ljust(provider.MAX_BYTES, b" "))
    assert provider.session_attention("session-1")["availability"] == "observed"


@pytest.mark.parametrize("growth", [10, provider.MAX_BYTES + 10])
def test_growth_during_read_is_unknown_and_read_size_is_bounded(pulse, monkeypatch, growth):
    write(pulse)
    original_open = Path.open
    requested = []

    class GrowingReader:
        def __enter__(self):
            self.stream = original_open(pulse, "rb")
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            requested.append(size)
            with original_open(pulse, "ab") as writer:
                writer.write(b" " * growth)
            return self.stream.read(size)

    monkeypatch.setattr(Path, "open", lambda *a, **k: GrowingReader())
    result = provider.session_attention("session-1")
    assert requested == [provider.MAX_BYTES + 1]
    assert result["availability"] == "unknown" and result["rest"] is None
    assert result["issues"] == {
        "oversized_source" if growth > provider.MAX_BYTES else "source_changed": 1,
    }


def test_replaced_file_is_unknown(pulse, monkeypatch):
    write(pulse)
    original = provider._signature
    calls = 0

    def changed(info):
        nonlocal calls
        calls += 1
        return original(info) if calls < 4 else ("changed",)

    monkeypatch.setattr(provider, "_signature", changed)
    result = provider.session_attention("session-1")
    assert result["issues"] == {"source_changed": 1} and result["rest"] is None


def test_resolved_escape_is_not_read(pulse, tmp_path):
    target = tmp_path / "foreign"
    target.write_text("private-data", encoding="utf-8")
    try:
        pulse.symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege unavailable")
    assert provider.session_attention("session-1")["issues"] == {"unsafe_session_path": 1}
