"""CLI tests for the fast-path single-agent lookup: `agent-bridge agent <name>`.

This command backs agent_dispatch's single-agent existence/project checks
(spawn preflight) with a static/topology-only lookup that never enumerates
namespace/CodeSpace/container providers -- unlike `agents` (the full
listing), which awaits every registered namespace resolver and can be slow
(see agent_registry.py's AGENT_BRIDGE_NAMESPACE_LIST_RESOLVER_TIMEOUT
docstring for the reliability issue this sidesteps for the common case).
"""

from __future__ import annotations

import argparse

import pytest

from agent_bridge import __main__ as m
from agent_bridge.client import BridgeClientError


class _FakeClient:
    def __init__(self, *, agent=None, raise_status=None):
        self._agent = agent
        self._raise_status = raise_status
        self.requested: list[str] = []

    def get_agent(self, name, **_kwargs):
        self.requested.append(name)
        if self._raise_status is not None:
            raise BridgeClientError(self._raise_status, "not found")
        return self._agent or {}


def _agent_args(name, *, json=False):
    return argparse.Namespace(name=name, json=json)


def test_agent_found_prints_human_readable(monkeypatch, capsys):
    client = _FakeClient(agent={
        "name": "odsp-web-harness-safe-acp",
        "display_name": "odsp-web-harness safe ACP",
        "target_type": "local",
        "aliases": [],
        "host": "",
        "managed": False,
    })
    monkeypatch.setattr(m, "_get_client", lambda **kw: client)
    m._cmd_agent_show(_agent_args("odsp-web-harness-safe-acp"))
    out = capsys.readouterr().out
    assert "odsp-web-harness safe ACP" in out
    assert client.requested == ["odsp-web-harness-safe-acp"]


def test_agent_found_json(monkeypatch, capsys):
    client = _FakeClient(agent={"name": "cloud1", "target_type": "local"})
    monkeypatch.setattr(m, "_get_client", lambda **kw: client)
    m._cmd_agent_show(_agent_args("cloud1", json=True))
    out = capsys.readouterr().out
    assert '"name": "cloud1"' in out


def test_agent_not_found_exits_nonzero(monkeypatch, capsys):
    client = _FakeClient(raise_status=404)
    monkeypatch.setattr(m, "_get_client", lambda **kw: client)
    with pytest.raises(SystemExit) as exc_info:
        m._cmd_agent_show(_agent_args("no-such-agent"))
    assert exc_info.value.code == 1
    assert "no-such-agent" in capsys.readouterr().out


def test_agent_not_found_json_prints_null(monkeypatch, capsys):
    client = _FakeClient(raise_status=404)
    monkeypatch.setattr(m, "_get_client", lambda **kw: client)
    with pytest.raises(SystemExit):
        m._cmd_agent_show(_agent_args("no-such-agent", json=True))
    assert capsys.readouterr().out.strip() == "null"


def test_agent_reraises_non_404_errors(monkeypatch):
    client = _FakeClient(raise_status=500)
    monkeypatch.setattr(m, "_get_client", lambda **kw: client)
    with pytest.raises(BridgeClientError):
        m._cmd_agent_show(_agent_args("odsp-web-harness-safe-acp"))


def test_agent_subcommand_parses():
    parser = m.build_parser()
    args = parser.parse_args(["agent-show", "odsp-web-harness-safe-acp"])
    assert args.name == "odsp-web-harness-safe-acp"
    assert args.func is m._cmd_agent_show
