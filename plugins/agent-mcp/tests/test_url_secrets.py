"""Tests for ``server.url_secrets`` -- secret ``${name}`` placeholders in the URL.

Covers the config surface (parse + validate), the placeholder helper, the
injector ``acquire_secret`` addition, the async resolver, and the http
transport's lazy (spawn-time) resolution.
"""
from __future__ import annotations

import pytest

from agent_mcp.auth.base import NoneInjector, TokenInjector
from agent_mcp.auth.url_secrets import resolve_url
from agent_mcp.config import (
    ConfigError,
    parse_config,
    url_placeholder_names,
)


# -- placeholder helper ------------------------------------------------------

def test_url_placeholder_names_extracts_in_order_deduped():
    url = "http://h:9583/${a}/x/${b}/${a}"
    assert url_placeholder_names(url) == ["a", "b"]


def test_url_placeholder_names_empty_when_none_or_plain():
    assert url_placeholder_names(None) == []
    assert url_placeholder_names("http://h/plain") == []


# -- config parse + validation ----------------------------------------------

def _doc(url, url_secrets):
    return {
        "server": {"type": "http", "url": url, "url_secrets": url_secrets},
    }


def _vault_source(entry):
    return {"kind": "command", "parse": "raw",
            "command": ["vault", "get", entry, "password"]}


def test_parse_url_secrets_ok_whole_url_placeholder():
    cfg = parse_config(_doc("${ha_url}", {"ha_url": _vault_source("HA MCP")}), name="ha")
    assert "ha_url" in cfg.server.url_secrets
    assert cfg.server.url_secrets["ha_url"].kind == "command"
    assert cfg.server.url_secrets["ha_url"].parse == "raw"


def test_parse_url_secrets_ok_partial_path_placeholder():
    cfg = parse_config(
        _doc("http://h:9583/${tok}", {"tok": _vault_source("HA MCP path")}), name="ha")
    assert url_placeholder_names(cfg.server.url) == ["tok"]


def test_validate_missing_source_for_placeholder():
    with pytest.raises(ConfigError, match=r"references \$\{tok\} but server.url_secrets"):
        parse_config(_doc("http://h/${tok}", {}), name="ha")


def test_validate_unused_source_without_placeholder():
    with pytest.raises(ConfigError, match=r"defines 'extra' but server.url has no"):
        parse_config(_doc("http://h/plain", {"extra": _vault_source("X")}), name="ha")


def test_validate_url_secrets_rejected_on_non_http():
    doc = {
        "server": {"type": "stdio", "command": ["x"], "url_secrets": {"a": _vault_source("X")}},
    }
    with pytest.raises(ConfigError, match="only valid for transport 'http'"):
        parse_config(doc, name="s")


def test_validate_bad_source_kind():
    with pytest.raises(ConfigError, match=r"url_secrets\['a'\].kind 'bogus'"):
        parse_config(_doc("http://h/${a}", {"a": {"kind": "bogus"}}), name="ha")


def test_no_url_secrets_is_unaffected():
    cfg = parse_config({"server": {"type": "http", "url": "https://x/mcp"}}, name="x")
    assert cfg.server.url_secrets == {}
    assert url_placeholder_names(cfg.server.url) == []


# -- acquire_secret on injectors --------------------------------------------

async def test_none_injector_acquire_secret_is_none():
    assert await NoneInjector().acquire_secret() is None


class _StubToken(TokenInjector):
    name = "stub"

    def __init__(self, value):
        super().__init__(spec=None)  # spec unused for _acquire here
        self._value = value

    async def _acquire(self):
        return self._value


async def test_token_injector_acquire_secret_returns_raw_and_caches():
    inj = _StubToken("s3cr3t")
    assert await inj.acquire_secret() == "s3cr3t"
    # cached
    inj._value = "changed"
    assert await inj.acquire_secret() == "s3cr3t"


# -- the resolver ------------------------------------------------------------

async def test_resolve_url_substitutes_whole_url(monkeypatch):
    cfg = parse_config(_doc("${ha_url}", {"ha_url": _vault_source("HA MCP")}), name="ha")

    async def fake_acquire(self):
        return "http://homeassistant:9583/private_TOKEN"

    monkeypatch.setattr("agent_mcp.auth.injectors.CommandInjector.acquire_secret",
                        fake_acquire, raising=True)
    resolved = await resolve_url(cfg)
    assert resolved == "http://homeassistant:9583/private_TOKEN"


async def test_resolve_url_substitutes_partial_path(monkeypatch):
    cfg = parse_config(
        _doc("http://h:9583/${tok}", {"tok": _vault_source("HA path")}), name="ha")

    async def fake_acquire(self):
        return "private_ABC"

    monkeypatch.setattr("agent_mcp.auth.injectors.CommandInjector.acquire_secret",
                        fake_acquire, raising=True)
    assert await resolve_url(cfg) == "http://h:9583/private_ABC"


async def test_resolve_url_no_secrets_returns_unchanged():
    cfg = parse_config({"server": {"type": "http", "url": "https://x/mcp"}}, name="x")
    assert await resolve_url(cfg) == "https://x/mcp"


async def test_resolve_url_raises_when_secret_unresolvable(monkeypatch):
    cfg = parse_config(_doc("${ha_url}", {"ha_url": _vault_source("HA MCP")}), name="ha")

    async def fake_acquire(self):
        return None  # e.g. vault locked / command failed

    monkeypatch.setattr("agent_mcp.auth.injectors.CommandInjector.acquire_secret",
                        fake_acquire, raising=True)
    with pytest.raises(RuntimeError, match=r"could not resolve URL secret '\$\{ha_url\}'"):
        await resolve_url(cfg)


# -- http transport lazy resolution -----------------------------------------

async def test_http_transport_resolves_url_lazily(monkeypatch):
    from agent_mcp.transports.http import HttpTransport

    cfg = parse_config(_doc("http://h:9583/${tok}", {"tok": _vault_source("HA path")}), name="ha")
    injector = NoneInjector()
    t = HttpTransport(cfg, injector)

    # Not resolved at construction (no vault touched on load/status).
    assert t._url_resolved is False
    assert t._url == "http://h:9583/${tok}"

    async def fake_acquire(self):
        return "private_XYZ"

    monkeypatch.setattr("agent_mcp.auth.injectors.CommandInjector.acquire_secret",
                        fake_acquire, raising=True)
    await t._ensure_url_resolved()
    assert t._url == "http://h:9583/private_XYZ"
    assert t._url_resolved is True


async def test_http_transport_no_secrets_marked_resolved():
    from agent_mcp.transports.http import HttpTransport

    cfg = parse_config({"server": {"type": "http", "url": "https://x/mcp"}}, name="x")
    t = HttpTransport(cfg, NoneInjector())
    # Nothing to resolve -> flagged resolved immediately, never touches a source.
    assert t._url_resolved is True
    assert t._url == "https://x/mcp"
