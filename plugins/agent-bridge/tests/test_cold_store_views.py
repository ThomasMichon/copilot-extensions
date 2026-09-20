"""Tests for cold-store-provider -> SessionInfo rendering (cold_store_views.py)."""

from __future__ import annotations

from agent_bridge.cold_store import ColdStoreSession
from agent_bridge.cold_store_views import (
    cold_store_session_info,
    parse_cold_store_timestamp,
)
from agent_bridge.models import SessionStatus


def test_parse_cold_store_timestamp_valid_iso():
    dt = parse_cold_store_timestamp("2026-01-01T00:00:00+00:00")
    assert dt.year == 2026


def test_parse_cold_store_timestamp_missing_falls_back_to_now():
    assert parse_cold_store_timestamp(None) is not None


def test_parse_cold_store_timestamp_unparseable_falls_back_to_now():
    assert parse_cold_store_timestamp("not-a-date") is not None


def test_parse_cold_store_timestamp_non_string_never_raises():
    """A malformed/non-string provider timestamp (e.g. a stray int) must
    fall back to now rather than raise -- the docstring's contract."""
    assert parse_cold_store_timestamp(12345) is not None  # type: ignore[arg-type]


def test_cold_store_session_info_shape():
    cold = ColdStoreSession(
        session_id="abc",
        status="ended",
        cwd="/repo",
        worktree_id="wt-1",
        project="proj",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
    )
    info = cold_store_session_info(cold)
    assert info.session_id == "abc"
    assert info.durable_session_id == "abc"
    assert info.worktree_id == "wt-1"
    assert info.read_only is True
    assert info.at_rest is True
    assert info.status == SessionStatus.ENDED


def test_cold_store_session_info_unknown_status_defaults_to_ended():
    cold = ColdStoreSession(session_id="abc", status="not-a-real-status")
    info = cold_store_session_info(cold)
    assert info.status == SessionStatus.ENDED


def test_cold_store_session_info_no_status_defaults_to_ended():
    cold = ColdStoreSession(session_id="abc")
    info = cold_store_session_info(cold)
    assert info.status == SessionStatus.ENDED
