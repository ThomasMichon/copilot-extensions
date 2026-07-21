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


# --- plugin-shipped bridge resolution (installed-plugins/*/*/agents) ----------
# A plugin may ship its bridge config in-tree; a bare bridge name resolves against
# ``<root>/*/*/{agents,mcp}/<name>.{yaml,yml,json}`` (with the ``.mcp`` infix), so
# no user-space copy under ~/.agent-mcp/bridges is needed.


def _make_plugin_bridge(root, marketplace, plugin, filename, doc=None):
    d = root / marketplace / plugin / "agents"
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(json.dumps(doc or _http_doc()), encoding="utf-8")
    return p


def test_resolve_plugin_bridge(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_PLUGIN_ROOTS", str(tmp_path))
    p = _make_plugin_bridge(tmp_path, "dev-tmichon", "ado-data", "ado.mcp.yaml")
    # File uses the ``.mcp`` infix; the bridge name is the bare ``ado``.
    assert resolve_config_path("ado") == p
    cfg = load_config("ado")
    assert cfg.server.url == "https://mcp.example/org"


def test_resolve_plugin_bridge_no_infix(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_PLUGIN_ROOTS", str(tmp_path))
    p = _make_plugin_bridge(tmp_path, "mkt", "plug", "vei.yaml")
    assert resolve_config_path("vei") == p


def test_user_bridge_wins_over_plugin(tmp_path, monkeypatch):
    # User-space bridges/ takes precedence over a plugin-shipped one.
    bridges = tmp_path / "bridges"
    bridges.mkdir()
    (bridges / "ado.json").write_text(json.dumps(_http_doc()), encoding="utf-8")
    monkeypatch.setattr("agent_mcp.config.BRIDGES_DIR", bridges)
    plugins = tmp_path / "plugins"
    monkeypatch.setenv("AGENT_MCP_PLUGIN_ROOTS", str(plugins))
    _make_plugin_bridge(plugins, "mkt", "plug", "ado.mcp.yaml")
    assert resolve_config_path("ado") == bridges / "ado.json"


def test_ambiguous_plugin_bridge_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_PLUGIN_ROOTS", str(tmp_path))
    _make_plugin_bridge(tmp_path, "mkt-a", "plug-a", "dup.mcp.yaml")
    _make_plugin_bridge(tmp_path, "mkt-b", "plug-b", "dup.mcp.yaml")
    with pytest.raises(ConfigError) as exc:
        resolve_config_path("dup")
    assert "ambiguous" in str(exc.value).lower()


def test_discover_plugin_bridges(tmp_path, monkeypatch):
    from agent_mcp.config import discover_plugin_bridges
    monkeypatch.setenv("AGENT_MCP_PLUGIN_ROOTS", str(tmp_path))
    _make_plugin_bridge(tmp_path, "dev-tmichon", "ado-data", "ado.mcp.yaml")
    _make_plugin_bridge(tmp_path, "dev-tmichon", "incident-management", "icm.mcp.yaml")
    found = discover_plugin_bridges()
    assert set(found) == {"ado", "icm"}


# --- machine-local config overlays (~/.agent-mcp/overrides/<id>.yaml) ---------
# By-convention, env-free per-host override: a deep-merge onto the committed
# config at load time. Keyed by an explicit top-level ``id`` or the file stem
# with a trailing ``.mcp`` stripped. Mappings merge; scalars/lists replace.


def _write(path, mapping):
    import json as _json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(mapping), encoding="utf-8")


def test_overlay_replaces_url(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_mcp.config.OVERRIDES_DIR", tmp_path / "overrides")
    cfg_path = tmp_path / "vei.mcp.yaml"
    _write(cfg_path, {"server": {"type": "http", "url": "https://gateway/vei/mcp/"}})
    _write(tmp_path / "overrides" / "vei.json",
           {"server": {"url": "http://localhost:8420/mcp/"}})
    cfg = load_config(str(cfg_path))
    # url overridden, sibling server.type preserved by the recursive merge
    assert cfg.server.url == "http://localhost:8420/mcp/"
    assert cfg.server.type == "http"


def test_overlay_absent_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_mcp.config.OVERRIDES_DIR", tmp_path / "overrides")
    cfg_path = tmp_path / "vei.mcp.yaml"
    _write(cfg_path, {"server": {"type": "http", "url": "https://gateway/vei/mcp/"}})
    cfg = load_config(str(cfg_path))
    assert cfg.server.url == "https://gateway/vei/mcp/"


def test_overlay_keyed_by_explicit_id(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_mcp.config.OVERRIDES_DIR", tmp_path / "overrides")
    cfg_path = tmp_path / "anything.yaml"
    _write(cfg_path, {"id": "vei", "server": {"type": "http", "url": "https://g/mcp/"}})
    _write(tmp_path / "overrides" / "vei.json", {"server": {"url": "http://local/mcp/"}})
    cfg = load_config(str(cfg_path))
    assert cfg.server.url == "http://local/mcp/"


def test_overlay_stem_strips_dot_mcp(tmp_path, monkeypatch):
    # ``vei.mcp.yaml`` keys on ``vei`` (the ``.mcp`` infix is stripped).
    monkeypatch.setattr("agent_mcp.config.OVERRIDES_DIR", tmp_path / "overrides")
    cfg_path = tmp_path / "vei.mcp.yaml"
    _write(cfg_path, {"server": {"type": "http", "url": "https://g/mcp/"}})
    _write(tmp_path / "overrides" / "vei.yaml", {"server": {"url": "http://local/mcp/"}})
    cfg = load_config(str(cfg_path))
    assert cfg.server.url == "http://local/mcp/"


def test_overlay_overrides_auth_field(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_mcp.config.OVERRIDES_DIR", tmp_path / "overrides")
    cfg_path = tmp_path / "svc.mcp.yaml"
    _write(cfg_path, {
        "server": {"type": "http", "url": "https://gateway/mcp/"},
        "auth": {"kind": "command",
                 "command": ["vault", "get", "Gateway Token", "password"],
                 "parse": "raw", "header": "Authorization"},
    })
    # On-box: no auth needed against the local endpoint.
    _write(tmp_path / "overrides" / "svc.json",
           {"server": {"url": "http://localhost:8420/mcp/"}, "auth": {"kind": "none"}})
    cfg = load_config(str(cfg_path))
    assert cfg.server.url == "http://localhost:8420/mcp/"
    assert cfg.auth.normalized_kind == "none"


def test_overlay_replaces_list_wholesale(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_mcp.config.OVERRIDES_DIR", tmp_path / "overrides")
    cfg_path = tmp_path / "svc.mcp.yaml"
    _write(cfg_path, {
        "server": {"type": "http", "url": "https://g/mcp/"},
        "tools": {"allow": ["vei_*", "gitea_*"]},
    })
    _write(tmp_path / "overrides" / "svc.json", {"tools": {"allow": ["vei_*"]}})
    cfg = load_config(str(cfg_path))
    assert cfg.tools.allow == ["vei_*"]  # replaced, not concatenated


def test_deep_merge_semantics():
    from agent_mcp.config import _deep_merge
    base = {"a": {"b": 1, "c": 2}, "list": [1, 2], "scalar": "x"}
    overlay = {"a": {"c": 3, "d": 4}, "list": [9], "scalar": "y"}
    assert _deep_merge(base, overlay) == {
        "a": {"b": 1, "c": 3, "d": 4},  # nested map merged
        "list": [9],                    # list replaced
        "scalar": "y",                  # scalar replaced
    }
