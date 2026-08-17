"""Tests for the container `namespace-*` CLI seam (dotfiles #892 Increment 3b)."""

from __future__ import annotations

import json
from unittest.mock import patch

from agent_containers.__main__ import main


def test_namespace_list_json(capsys):
    async def _list_specs(self):
        return [{"name": "example-web-1", "display_name": "example-web-1 (example-web)",
                 "description": "Local dev container", "icon": "container",
                 "state": "running"}]

    with patch("agent_containers.resolver.ContainerResolver.list_specs", _list_specs):
        rc = main(["namespace-list"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "example-web-1"


def test_namespace_resolve_json(capsys):
    async def _spec(self, name):
        return {"type": "command", "spawn_command": ["docker", "exec", name], "user": "node"}

    with patch("agent_containers.resolver.ContainerResolver.resolve_spec", _spec):
        rc = main(["namespace-resolve", "example-web-1"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["spawn_command"] == ["docker", "exec", "example-web-1"] and d["user"] == "node"


def test_namespace_resolve_not_found_exit3(capsys):
    async def _spec(self, name):
        raise KeyError(name)

    with patch("agent_containers.resolver.ContainerResolver.resolve_spec", _spec):
        assert main(["namespace-resolve", "nope"]) == 3


def test_namespace_target_repo_is_empty(capsys):
    # Containers do not drive related-repo plugin injection -> always empty.
    assert main(["namespace-target-repo", "example-web-1"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_namespace_ensure_ready_ok_and_fail(capsys):
    async def _ok(self, name):
        return None

    with patch("agent_containers.resolver.ContainerResolver.ensure_ready", _ok):
        assert main(["namespace-ensure-ready", "example-web-1"]) == 0

    async def _fail(self, name):
        raise RuntimeError("not found")

    with patch("agent_containers.resolver.ContainerResolver.ensure_ready", _fail):
        assert main(["namespace-ensure-ready", "example-web-1"]) == 1
