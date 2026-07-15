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


def test_stdio_npm_declaration():
    cfg = parse_config({
        "server": {"type": "stdio", "npm": "gitea-mcp"},
        "auth": {"kind": "none"},
    })
    assert cfg.server.npm == "gitea-mcp"
    assert cfg.server.command == []
    assert cfg.server.npm_args == []
    assert cfg.server.launch_desc == "npm:gitea-mcp"


def test_stdio_npm_with_args():
    cfg = parse_config({
        "server": {"type": "stdio", "npm": "some-mcp", "args": ["--port", "0"]},
    })
    assert cfg.server.npm == "some-mcp"
    assert cfg.server.npm_args == ["--port", "0"]
    assert cfg.server.command == []


def test_stdio_command_wins_over_npm():
    # An explicit command takes precedence; args fold into it and npm is ignored.
    cfg = parse_config({
        "server": {"type": "stdio", "command": "npx",
                   "args": ["-y", "x"], "npm": "ignored"},
    })
    assert cfg.server.command == ["npx", "-y", "x"]
    assert cfg.server.npm is None


def test_stdio_accepts_npm_without_command():
    # Validation is satisfied by npm alone (no ConfigError).
    cfg = parse_config({"server": {"type": "stdio", "npm": "gitea-mcp"}})
    assert cfg.server.npm == "gitea-mcp"


def test_entra_requires_resource_or_scope():
    with pytest.raises(ConfigError):
        parse_config({"server": {"type": "http", "url": "u"}, "auth": {"kind": "entra"}})


def test_allow_and_deny_mutually_exclusive():
    with pytest.raises(ConfigError):
        parse_config(_http_doc(tools={"allow": ["a*"], "deny": ["b*"]}))


def test_unknown_auth_kind_rejected():
    with pytest.raises(ConfigError):
        parse_config(_http_doc(auth={"kind": "magic"}))


def test_command_parses_request_and_args():
    doc = {
        "server": {"type": "stdio", "command": "npx"},
        "auth": {
            "kind": "command",
            "command": "git-credential-vault",
            "args": ["get"],
            "request": {"protocol": "https", "host": "h"},
            "parse": "keyvalue",
            "field": "password",
            "target_env": "API_KEY",
        },
    }
    cfg = parse_config(doc)
    assert cfg.auth.command == ["git-credential-vault", "get"]
    assert cfg.auth.request == {"protocol": "https", "host": "h"}
    assert cfg.auth.parse == "keyvalue"
    assert cfg.auth.field_name == "password"


def test_command_requires_command():
    with pytest.raises(ConfigError) as exc:
        parse_config({"server": {"type": "stdio", "command": "npx"},
                      "auth": {"kind": "command"}})
    assert "command" in str(exc.value)


def test_command_invalid_parse_rejected():
    with pytest.raises(ConfigError) as exc:
        parse_config({"server": {"type": "stdio", "command": "npx"},
                      "auth": {"kind": "command", "command": "vault", "parse": "xml"}})
    assert "auth.parse" in str(exc.value)


def test_auth_list_parses_to_extra_auths():
    doc = {
        "server": {"type": "stdio", "command": "npx"},
        "auth": [
            {"kind": "command", "command": ["vault", "get", "A", "password"],
             "parse": "raw", "target_env": "PW"},
            {"kind": "command", "command": ["vault", "get", "B", "password"],
             "parse": "raw", "target_env": "KEY"},
        ],
    }
    cfg = parse_config(doc)
    assert len(cfg.auths) == 2
    assert cfg.auth.target_env == "PW"
    assert [a.target_env for a in cfg.auths] == ["PW", "KEY"]


def test_auth_list_requires_target_env():
    doc = {
        "server": {"type": "stdio", "command": "npx"},
        "auth": [
            {"kind": "command", "command": ["vault", "get", "A", "password"],
             "parse": "raw", "target_env": "PW"},
            {"kind": "command", "command": ["vault", "get", "B", "password"],
             "parse": "raw"},  # missing target_env
        ],
    }
    with pytest.raises(ConfigError) as exc:
        parse_config(doc)
    assert "target_env" in str(exc.value)


def test_auth_list_rejects_duplicate_target_env():
    doc = {
        "server": {"type": "stdio", "command": "npx"},
        "auth": [
            {"kind": "command", "command": ["vault", "get", "A", "password"],
             "parse": "raw", "target_env": "SAME"},
            {"kind": "command", "command": ["vault", "get", "B", "password"],
             "parse": "raw", "target_env": "SAME"},
        ],
    }
    with pytest.raises(ConfigError) as exc:
        parse_config(doc)
    assert "duplicate target_env" in str(exc.value)


def test_empty_auth_list_is_none():
    cfg = parse_config({"server": {"type": "stdio", "command": "npx"}, "auth": []})
    assert len(cfg.auths) == 1
    assert cfg.auth.kind == "none"


def test_inject_mismatch_rejected_on_http():
    with pytest.raises(ConfigError) as exc:
        parse_config({"server": {"type": "http", "url": "u"},
                      "auth": {"kind": "env", "value": "x", "inject": "env"}})
    assert "not supported" in str(exc.value)


def test_multi_auth_rejected_on_http():
    with pytest.raises(ConfigError) as exc:
        parse_config({
            "server": {"type": "http", "url": "u"},
            "auth": [
                {"kind": "command", "command": "a", "parse": "raw", "target_env": "X"},
                {"kind": "command", "command": "b", "parse": "raw", "target_env": "Y"},
            ],
        })
    assert "stdio" in str(exc.value)


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
