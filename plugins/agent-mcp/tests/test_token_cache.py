"""Tests for the optional shared/on-disk token cache (auth.token_cache)."""

from __future__ import annotations

import base64
import json
import time

from agent_mcp.auth import token_cache


def _jwt(exp: int) -> str:
    """A minimal JWT-shaped token whose payload carries ``exp``."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=")
    return "h." + payload.decode() + ".s"


def test_default_key_stable_and_distinct():
    a = token_cache.default_key(kind="command", command=["x", "--r", "R"], resource="R")
    b = token_cache.default_key(kind="command", command=["x", "--r", "R"], resource="R")
    c = token_cache.default_key(kind="command", command=["x", "--r", "OTHER"], resource="OTHER")
    assert a == b
    assert a != c
    assert len(a) == 32


def test_jwt_exp_parses_and_tolerates_garbage():
    assert token_cache.jwt_exp(_jwt(1234567890)) == 1234567890
    assert token_cache.jwt_exp("not-a-jwt") is None
    assert token_cache.jwt_exp("") is None


def test_write_read_roundtrip_fixed_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    assert token_cache.write("k1", "sekret", ttl="3600") is True
    assert token_cache.read("k1") == "sekret"


def test_read_none_when_expired(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    # JWT already expired -> written with a past exp -> read declines.
    token_cache.write("k2", _jwt(int(time.time()) - 10), ttl="auto")
    assert token_cache.read("k2") is None


def test_read_none_within_skew(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    token_cache.write("k3", "s", ttl="30")
    assert token_cache.read("k3", skew=60) is None  # expires within the skew window
    assert token_cache.read("k3", skew=0) == "s"


def test_auto_ttl_non_jwt_not_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    # No JWT exp and ttl=auto -> unknown expiry -> not written.
    assert token_cache.write("k4", "opaque-secret", ttl="auto") is False
    assert token_cache.read("k4") is None


def test_auto_ttl_jwt_persisted(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    tok = _jwt(int(time.time()) + 3600)
    assert token_cache.write("k5", tok, ttl="auto") is True
    assert token_cache.read("k5") == tok


def test_invalidate_removes(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    token_cache.write("k6", "s", ttl="3600")
    assert token_cache.read("k6") == "s"
    token_cache.invalidate("k6")
    assert token_cache.read("k6") is None
    token_cache.invalidate("k6")  # idempotent
