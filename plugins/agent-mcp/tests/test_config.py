from __future__ import annotations

import json

import pytest

from agent_mcp.config import (
    BRIDGES_DIR,
    ConfigError,
    load_config,
    parse_config,
    resolve_config_path,
)


def _http_doc(**over):
    doc = {
        "server": {"type": "http", "url": "https://mcp.example/org"},
        "auth": {"kind": "entra", "resource": "res-id"},
        "timeout": 15,
        "retries": 2,
    }
    doc.update(over)
    return doc


def test_parse_http_entra_ok():
    cfg = parse_config(_http_doc(), name="ado")
    assert cfg.server.type == "http"
    assert cfg.server.url == "https://mcp.example/org"
    assert cfg.auth.normalized_kind == "entra"
    assert cfg.auth.resolve_inject("http") == "header"
    assert cfg.timeout == 15
    assert cfg.retries == 2
    assert cfg.name == "ado"


def test_stdio_command_string_and_args_merge():
    doc = {
        "server": {"type": "stdio", "command": "npx", "args": ["-y", "@scope/mcp"]},
        "auth": {"kind": "env", "source_env": "TOK", "target_env": "API_KEY"},
    }
    cfg = parse_config(doc)
    assert cfg.server.command == ["npx", "-y", "@scope/mcp"]
    assert cfg.auth.resolve_inject("stdio") == "env"


def test_missing_server_rejected():
    with pytest.raises(ConfigError):
        parse_config({"auth": {"kind": "none"}})


def test_http_requires_url():
    with pytest.raises(ConfigError) as exc:
        parse_config({"server": {"type": "http"}})
    assert "server.url" in str(exc.value)


def test_stdio_requires_command():
    with pytest.raises(ConfigError) as exc:
        parse_config({"server": {"type": "stdio"}})
    assert "server.command" in str(exc.value)


def test_entra_requires_resource_or_scope():
    with pytest.raises(ConfigError):
        parse_config({"server": {"type": "http", "url": "u"}, "auth": {"kind": "entra"}})


def test_allow_and_deny_mutually_exclusive():
    with pytest.raises(ConfigError):
        parse_config(_http_doc(tools={"allow": ["a*"], "deny": ["b*"]}))


def test_unknown_auth_kind_rejected():
    with pytest.raises(ConfigError):
        parse_config(_http_doc(auth={"kind": "magic"}))


def test_resolve_explicit_path(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("server: {type: http, url: u}\n", encoding="utf-8")
    assert resolve_config_path(str(p)) == p


def test_resolve_named_missing():
    with pytest.raises(ConfigError):
        resolve_config_path("definitely-not-a-real-bridge-xyz")


def test_load_named_from_home(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_mcp.config.BRIDGES_DIR", tmp_path)
    (tmp_path / "ado.json").write_text(json.dumps(_http_doc()), encoding="utf-8")
    cfg = load_config("ado")
    assert cfg.name == "ado"
    assert cfg.server.url == "https://mcp.example/org"


def test_bridges_dir_default_location():
    assert BRIDGES_DIR.name == "bridges"
