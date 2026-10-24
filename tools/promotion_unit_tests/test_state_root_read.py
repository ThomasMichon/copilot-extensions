"""Exercise the real state-root handler without unrelated CLI integrations."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-worktrees/src/agent_worktrees/context_cli.py"


@pytest.fixture
def reader(monkeypatch):
    name = "promotion_state_root_reader"
    package = types.ModuleType(name)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, name, package)
    for child in (
        "config", "output", "git_ops", "sessions", "state_root", "tracking",
        "installer", "installation_cli", "machine_instructions", "pr_config",
        "session_binding_cli", "status_updater_cli", "worktree_identity",
        "context_get_keys",
    ):
        module = types.ModuleType(f"{name}.{child}")
        setattr(package, child, module)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    package.context_get_keys.GET_KEYS = {}
    package.context_get_keys.PR_GRAFT_DEPENDENT_KEYS = frozenset()
    procutil = types.ModuleType("agent_procutil")
    procutil.no_window_kwargs = lambda: {}
    monkeypatch.setitem(sys.modules, "agent_procutil", procutil)
    specification = importlib.util.spec_from_file_location(f"{name}.context_cli", SOURCE)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    monkeypatch.setitem(sys.modules, specification.name, module)
    specification.loader.exec_module(module)
    return module


def _config(reader):
    config = types.SimpleNamespace(
        default_repo=types.SimpleNamespace(anchor="/synthetic/harness")
    )

    def load_config(*, include_control_plane_related_pr=True):
        assert include_control_plane_related_pr is False
        return config

    reader.cfg.load_config = load_config
    return config


@pytest.mark.parametrize(
    "arguments,bound,override",
    [
        (["--json"], True, None),
        ([], True, None),
        (["--repo", "alternate", "--json"], True, "alternate"),
        (["--json"], False, None),
        ([], False, None),
    ],
    ids=["json-bound", "plain-bound", "repo-override", "json-unbound", "plain-unbound"],
)
def test_state_root_read_preserves_response_without_pr_overlays(
    reader, capsys, arguments, bound, override
):
    config = _config(reader)
    payload = {
        "state_root": "/synthetic/knowledge" if bound else None,
        "source": "knowledge_repo",
        "repo": "knowledge",
        "stateless": True,
        "requires_external": True,
        "bound": bound,
        "error": None if bound else "No knowledge repository is bound",
    }
    result = types.SimpleNamespace(
        path=payload["state_root"],
        error=payload["error"],
        as_dict=lambda: payload.copy(),
    )

    def resolve_state_root(actual, *, repo_override):
        assert actual is config
        assert repo_override == override
        return result

    reader.state_root_mod.resolve_state_root = resolve_state_root
    assert reader.cmd_state_root_dispatch(arguments) == (0 if bound else 3)
    captured = capsys.readouterr()
    if "--json" in arguments:
        assert json.loads(captured.out) == payload
        assert captured.err == ""
    elif bound:
        assert captured.out == "/synthetic/knowledge\n"
        assert captured.err == ""
    else:
        assert captured.out == ""
        assert captured.err == "No knowledge repository is bound\n"


def test_state_root_conduct_preserves_pair_and_launch_context(reader, capsys):
    config = _config(reader)
    result = object()
    pair = object()
    reader.state_root_mod.resolve_state_root = lambda actual, **kw: result
    reader.state_root_mod._git_toplevel = lambda cwd: "/synthetic/checkout"
    reader.os = types.SimpleNamespace(getcwd=lambda: "/synthetic/launch")

    def resolve_pair(actual, *, cwd):
        assert actual is config
        assert cwd == "/synthetic/launch"
        return pair

    def definition(actual, **values):
        assert actual is result
        assert values == {
            "pair": pair,
            "launch_path": "/synthetic/checkout",
            "launch_anchor": "/synthetic/harness",
        }
        return "State repository definition"

    reader.state_root_mod.resolve_pair = resolve_pair
    reader.state_root_mod.state_repo_definition = definition
    assert reader.cmd_state_root_dispatch(["--conduct"]) == 0
    assert capsys.readouterr().out == "State repository definition\n"


def test_state_root_keeps_config_refusal_visible(reader):
    def refused(*, include_control_plane_related_pr=True):
        assert include_control_plane_related_pr is False
        raise ValueError("invalid state-root binding")

    reader.cfg.load_config = refused
    with pytest.raises(ValueError, match="invalid state-root binding"):
        reader.cmd_state_root_dispatch(["--json"])


def test_state_root_pair_keeps_its_existing_dispatch(reader):
    calls = []
    reader._state_root_pair = lambda as_json: calls.append(as_json) or 3
    assert reader.cmd_state_root_dispatch(["--pair", "--json"]) == 3
    assert calls == [True]
