"""Layered related-repo plugin selection for container and local Dev Box workers."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_bridge import related_plugins as rp
from agent_bridge.transport import PluginRef

_real_read_layered_related = rp._read_layered_related

_SELECTED = {"source": "dev-locally@synthetic", "enable": True}
_LAYERED_ROWS: list[dict[str, Any]] = [
    {"name": "other", "locus": {"container": {"repo": "elsewhere"}},
     "plugins": [{"source": "other@synthetic", "enable": True}]},
    {"name": "product", "locus": {"container": {"repo": "example-org/product-venue"}},
     "provenance": {"layer": "machine"},
     "plugins": [_SELECTED, {"source": "product-harness@synthetic", "enable": True}]},
]
_LEGACY = "related:\n  product:\n    plugins:\n      - { source: legacy@synthetic }\n"


def _write_related(anchor: Path, body: str) -> None:
    folder = anchor / ".agent-worktrees"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "related.yaml").write_text(body, encoding="utf-8")


def _serve(monkeypatch: pytest.MonkeyPatch, views: dict[Path, list[dict[str, Any]] | None]) -> list[Path]:
    seen: list[Path] = []

    def read(anchor: Path) -> list[dict[str, Any]] | None:
        seen.append(anchor)
        return views.get(anchor)

    monkeypatch.setattr(rp, "_read_layered_related", read)
    return seen


def test_layered_view_supplies_plugins_when_checkout_declares_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_related(tmp_path, "related: {}\n")
    seen = _serve(monkeypatch, {tmp_path: _LAYERED_ROWS})
    for repo in ("product", "EXAMPLE-ORG/product-venue"):
        assert rp.declared_related_plugins(repo, [tmp_path]) == [PluginRef("dev-locally@synthetic")]
    assert seen == [tmp_path, tmp_path]


def test_layered_selection_outranks_legacy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_related(tmp_path, _LEGACY)
    _serve(monkeypatch, {tmp_path: _LAYERED_ROWS})
    assert rp.declared_related_plugins("product", [tmp_path]) == [PluginRef("dev-locally@synthetic")]


def test_empty_layered_selection_is_authoritative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _write_related(first, _LEGACY)
    _serve(monkeypatch, {first: [{"name": "product", "plugins": []}], second: _LAYERED_ROWS})
    assert rp.declared_related_plugins("product", [first, second]) == []


def test_unmatched_layered_view_continues_to_next_anchor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _write_related(first, _LEGACY)
    seen = _serve(monkeypatch, {first: [{"name": "unrelated", "plugins": []}], second: _LAYERED_ROWS})
    assert rp.declared_related_plugins("product", [first, second]) == [PluginRef("dev-locally@synthetic")]
    assert seen == [first, second]


@pytest.mark.parametrize("rows", [None, [], [{"name": "product", "locus": {}}]])
def test_legacy_file_used_per_anchor_without_layered_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]] | None,
) -> None:
    """No owner, an empty view, or an owner that predates the ``plugins`` field."""
    _write_related(tmp_path, _LEGACY)
    _serve(monkeypatch, {tmp_path: rows})
    assert rp.declared_related_plugins("product", [tmp_path]) == [PluginRef("legacy@synthetic")]


def test_empty_legacy_selection_is_authoritative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    _write_related(first, "related:\n  product:\n    plugins: []\n")
    _serve(monkeypatch, {second: _LAYERED_ROWS})
    assert rp.declared_related_plugins("product", [first, second]) == []


def _fake_owner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str) -> None:
    script = tmp_path / "fake_owner.py"
    script.write_text(body, encoding="utf-8")
    monkeypatch.setattr(
        "agent_bridge.session_lifecycle_cli._agent_worktrees_launch_prefix",
        lambda: [sys.executable, str(script)],
    )


def test_layered_reader_invokes_owner_related_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_owner(monkeypatch, tmp_path, (
        "import json, sys\n"
        "print(json.dumps({'related': [{'name': 'argv', 'plugins': sys.argv[1:]}, 'junk']}))\n"
    ))
    assert _real_read_layered_related(tmp_path) == [
        {"name": "argv", "plugins": ["related", "list", "--json", "--repo", str(tmp_path)]},
    ]


@pytest.mark.parametrize("body", [
    "import sys\nsys.exit(3)\n",
    "print('not json')\n",
    "print('{\"related\": {}}')\n",
])
def test_layered_reader_fails_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    _fake_owner(monkeypatch, tmp_path, body)
    assert _real_read_layered_related(tmp_path) is None


def test_layered_reader_without_owner_returns_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent_bridge.session_lifecycle_cli._agent_worktrees_launch_prefix", lambda: None)
    assert _real_read_layered_related(tmp_path) is None


def test_layered_reader_timeout_bounds_descendants_holding_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_owner(monkeypatch, tmp_path, (
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n"
    ))
    monkeypatch.setattr(rp, "_LAYERED_TIMEOUT_SECONDS", 2)
    started = time.monotonic()
    assert _real_read_layered_related(tmp_path) is None
    assert time.monotonic() - started < 30


# --- Venue wiring -------------------------------------------------------------


def _capture_plan(monkeypatch: pytest.MonkeyPatch) -> list[list[dict[str, Any]]]:
    observed: list[list[dict[str, Any]]] = []

    def build(specs: list[dict[str, Any]], roots: list[Path], **_kwargs: Any) -> dict[str, Any]:
        observed.append(specs)
        return {"plugins": specs}

    monkeypatch.setattr("agent_bridge.venue_plugins.build_plan", build)
    monkeypatch.setattr("agent_bridge.venue_plugins.requires_repository_binding", lambda *_a: False)
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])
    return observed


def _controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, related: str) -> Path:
    root = tmp_path / "controller"
    _write_related(root, related)
    _serve(monkeypatch, {root: _LAYERED_ROWS})
    return root


@pytest.mark.asyncio
async def test_container_stages_layered_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge.venue_plugins import prepare_venue_plugins

    root = _controller(tmp_path, monkeypatch, "related: {}\n")
    observed = _capture_plan(monkeypatch)
    monkeypatch.setattr("agent_bridge.venue_plugins.build_prepare_script", lambda *_a: "PREPARE")
    transport = SimpleNamespace(boundary="container", run=AsyncMock(return_value=(0, "__VENUE_PLUGINS__{}", "")))
    await prepare_venue_plugins(
        {"name": "example", "repo": "product"}, transport, "/selected", controller_cwd=str(root),
    )
    assert observed == [[_SELECTED]]


@pytest.mark.asyncio
async def test_local_devbox_stages_layered_plugins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_bridge.venue_plugins import local_plugin_request

    root = _controller(tmp_path, monkeypatch, "related: {}\n")
    observed = _capture_plan(monkeypatch)
    monkeypatch.setattr(
        "agent_bridge.privileged_plugins.controller_source", lambda _target: (True, str(root)),
    )
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    request = await local_plugin_request(SimpleNamespace(venue={}, project="product"), str(checkout), {})
    assert observed == [[_SELECTED]]
    assert request is not None and request["repo"] == str(checkout.resolve())


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["codespace", "ssh"])
async def test_codespace_and_ssh_plans_keep_committed_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, venue: str,
) -> None:
    from agent_bridge.venue_plugins import prepare_venue_plugins, venue_plugin_plan

    root = _controller(tmp_path, monkeypatch, _LEGACY)
    observed = _capture_plan(monkeypatch)
    queried = _serve(monkeypatch, {root: _LAYERED_ROWS})
    legacy = {"source": "legacy@synthetic", "enable": True}
    if venue == "ssh":
        plan = await venue_plugin_plan({"repo": "product"}, controller_cwd=str(root))
    else:
        provider = AsyncMock(return_value=(0, '{"plugins": []}', ""))
        monkeypatch.setattr("agent_bridge.session_host.container_transport._run_provider", provider)
        monkeypatch.setattr("agent_bridge.transport._reresolve_stale_interpreter", lambda argv: argv)
        monkeypatch.setattr("agent_bridge.venue_plugins.build_prepare_script", lambda *_a: "PREPARE")
        transport = SimpleNamespace(
            boundary="codespace", run=AsyncMock(return_value=(0, "__VENUE_PLUGINS__{}", "")),
        )
        await prepare_venue_plugins(
            {"name": "example", "repo": "product", "provider_command": ["provider"]},
            transport, "/selected", controller_cwd=str(root),
        )
        plan = {"plugins": observed[0]}
    assert queried == []
    assert observed == [[legacy]]
    assert plan["plugins"] == [legacy]
