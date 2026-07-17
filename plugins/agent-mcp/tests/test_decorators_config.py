from __future__ import annotations

import pytest

from agent_mcp.config import DECORATOR_TYPES, ConfigError, parse_config
from agent_mcp.decorators import REGISTRY, build_decorators, known_types

from ._fake import make_ctx

BASE_SERVER = {"type": "http", "url": "https://example/mcp"}


def _cfg(**extra):
    return parse_config({"server": dict(BASE_SERVER), **extra})


def test_registry_matches_declared_types():
    assert set(REGISTRY) == set(DECORATOR_TYPES)
    assert set(known_types()) == set(DECORATOR_TYPES)


def test_parse_decorator_stack():
    cfg = _cfg(decorators=[
        {"type": "defer", "mode": "lazy", "expose": ["search_*"]},
        {"type": "rename", "namespace": "ado"},
        {"type": "filter", "allow": ["repo_*"]},
    ])
    assert [d.type for d in cfg.decorators] == ["defer", "rename", "filter"]
    assert cfg.decorators[0].options["mode"] == "lazy"
    assert cfg.decorators[1].options["namespace"] == "ado"


def test_unknown_decorator_type_rejected():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "nope"}])


def test_decorator_missing_type_rejected():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"mode": "lazy"}])


def test_defer_invalid_mode_rejected():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "defer", "mode": "bogus"}])


def test_storage_http_requires_url():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "storage", "backend": "http"}])


def test_storage_rule_requires_path():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "storage", "rules": [
            {"tool": "x", "outputs": [{"summary": True}]}]}])


def test_storage_rule_requires_outputs_or_inputs():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "storage", "rules": [{"tool": "x"}]}])


def test_storage_rules_valid():
    cfg = _cfg(decorators=[{"type": "storage", "rules": [
        {"tool": "get_*", "outputs": [{"path": "items", "summary": {"head": 3}}],
         "inputs": [{"path": "payload"}]}]}])
    assert cfg.decorators[0].options["rules"][0]["tool"] == "get_*"


def test_transform_inline_rule_valid():
    cfg = _cfg(decorators=[{"type": "transform", "tool": "list_*", "extract": "value"}])
    assert cfg.decorators[0].type == "transform"


def test_transform_rules_valid():
    cfg = _cfg(decorators=[{"type": "transform", "rules": [
        {"tool": "wit_*", "pick": ["id", "fields.System.Title"]}]}])
    assert cfg.decorators[0].options["rules"][0]["pick"] == ["id", "fields.System.Title"]


def test_transform_requires_an_op():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "transform", "rules": [{"tool": "x"}]}])


def test_transform_empty_rejected():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "transform"}])


def test_filter_allow_and_deny_rejected():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "filter", "allow": ["a"], "deny": ["b"]}])


def test_build_decorators_appends_legacy_tools_filter():
    cfg = _cfg(tools={"allow": ["repo_*"]},
              decorators=[{"type": "rename", "namespace": "ado"}])
    ctx, _ = make_ctx()
    stack = build_decorators(cfg, ctx)
    assert [d.type for d in stack] == ["rename", "filter"]  # legacy filter last
    assert stack[-1].allow == ["repo_*"]


def test_build_decorators_no_legacy_filter_when_inactive():
    cfg = _cfg(decorators=[{"type": "rename", "namespace": "ado"}])
    ctx, _ = make_ctx()
    stack = build_decorators(cfg, ctx)
    assert [d.type for d in stack] == ["rename"]


def test_empty_decorators_default():
    cfg = _cfg()
    assert cfg.decorators == []


def test_gate_valid():
    cfg = _cfg(decorators=[{
        "type": "gate",
        "match_tools": ["get_details"],
        "preflight": {"tool": "lookup", "args_from": {"id": "$args.recordId"},
                      "cache": "per-key"},
        "allow_when": {"path": "isSensitive", "equals": False},
        "on_deny": "stub",
    }])
    assert cfg.decorators[0].type == "gate"
    assert cfg.decorators[0].options["match_tools"] == ["get_details"]


def test_gate_requires_match_tools():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "gate",
                          "preflight": {"tool": "lookup"},
                          "allow_when": {"path": "x", "equals": 1}}])


def test_gate_requires_preflight_tool():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "gate", "match_tools": ["a"],
                          "preflight": {}, "allow_when": {"path": "x", "equals": 1}}])


def test_gate_requires_allow_when():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "gate", "match_tools": ["a"],
                          "preflight": {"tool": "lookup"}}])


def test_gate_invalid_on_deny_rejected():
    with pytest.raises(ConfigError):
        _cfg(decorators=[{"type": "gate", "match_tools": ["a"],
                          "preflight": {"tool": "lookup"},
                          "allow_when": {"path": "x", "equals": 1},
                          "on_deny": "bogus"}])
