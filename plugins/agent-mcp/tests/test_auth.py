from __future__ import annotations

from agent_mcp.auth import (
    EntraInjector,
    EnvInjector,
    GitCredentialInjector,
    NoneInjector,
    build_injector,
    parse_response,
)
from agent_mcp.config import parse_config


def _cfg(auth, server=None):
    doc = {"server": server or {"type": "http", "url": "https://mcp.example/o"}, "auth": auth}
    return parse_config(doc)


def test_parse_response_keyvalue():
    fields = parse_response("protocol=https\nhost=h\ntoken=abc\n\n")
    assert fields["token"] == "abc"
    assert fields["host"] == "h"


def test_parse_response_empty():
    assert parse_response(None) == {}
    assert parse_response("") == {}


def test_build_none():
    inj = build_injector(_cfg({"kind": "none"}))
    assert isinstance(inj, NoneInjector)


async def test_none_injects_nothing():
    inj = NoneInjector()
    assert await inj.headers() == {}
    assert await inj.child_env() == {}


async def test_env_injector_header(monkeypatch):
    monkeypatch.setenv("MY_TOK", "s3cret")
    inj = build_injector(_cfg({"kind": "env", "source_env": "MY_TOK"}))
    assert isinstance(inj, EnvInjector)
    assert await inj.headers() == {"Authorization": "Bearer s3cret"}


async def test_env_injector_static_value_and_child_env():
    inj = build_injector(_cfg(
        {"kind": "static", "value": "lit", "target_env": "API_KEY", "format": "{token}"},
        server={"type": "stdio", "command": "npx"},
    ))
    assert await inj.headers() == {"Authorization": "lit"}
    assert await inj.child_env() == {"API_KEY": "lit"}


async def test_env_injector_missing_token_is_empty():
    inj = build_injector(_cfg({"kind": "env", "source_env": "DEFINITELY_UNSET_VAR_XYZ"}))
    assert await inj.headers() == {}


async def test_token_injector_caches_and_invalidates(monkeypatch):
    calls = {"n": 0}

    monkeypatch.setenv("ROT", "v1")
    inj = build_injector(_cfg({"kind": "env", "source_env": "ROT"}))

    orig = inj._acquire

    async def counting():
        calls["n"] += 1
        return await orig()

    inj._acquire = counting
    await inj.headers()
    await inj.headers()
    assert calls["n"] == 1  # cached
    await inj.invalidate()
    await inj.headers()
    assert calls["n"] == 2


async def test_entra_injector_wraps_source(monkeypatch):
    inj = build_injector(_cfg({"kind": "entra", "resource": "res"}))
    assert isinstance(inj, EntraInjector)

    class FakeSource:
        async def resolve(self, action, fields, *, timeout=30.0):
            assert action == "get-azure-token"
            assert fields["resource"] == "res"
            return "protocol=https\nhost=h\ntoken=AZTOKEN\n\n"

    inj._source = FakeSource()
    assert await inj.headers() == {"Authorization": "Bearer AZTOKEN"}


def test_build_git_credential_derives_host():
    inj = build_injector(_cfg(
        {"kind": "git-credential"},
        server={"type": "http", "url": "https://dev.azure.com/org"},
    ))
    assert isinstance(inj, GitCredentialInjector)
    assert inj._host == "dev.azure.com"
