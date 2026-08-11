"""Tests for the dynamic membership + authored-overlay model (Phase 1 refinement).

Membership is *discovered* from the marketplace (checkout or remote); the
installer-owned catalog is a knowledge overlay, with graceful inference for any
plugin the catalog does not yet describe.
"""

from __future__ import annotations

import io
import json

import configurator.discovery as discovery
import configurator.model as model
from configurator.catalog import find_repo_root, load_catalog
from configurator.discovery import Discovered, DiscoverySource
from configurator.model import build_model, coverage


def test_build_model_from_checkout_overlays_catalog():
    root = find_repo_root()
    if root is None:
        return
    m = build_model(repo_root=root, allow_remote=False)
    assert m.source.kind == "checkout"
    aw = m.get("agent-worktrees")
    assert aw is not None and aw.authored
    assert aw.plugin.kind == "core"
    assert any(s.runs and "install.ps1" in s.runs for s in aw.plugin.steps)


def test_inference_for_uncatalogued_plugin(monkeypatch):
    """A discovered plugin with no catalog entry is kept with inferred defaults."""
    fake = DiscoverySource("checkout", "x", (
        Discovered(name="agent-worktrees", origin="checkout", has_service_yaml=True),
        Discovered(name="brand-new-plugin", origin="checkout", has_pyproject=True,
                   description="a plugin the catalog has never heard of"),
    ))
    monkeypatch.setattr(model, "discover", lambda **kw: fake)
    monkeypatch.setattr(model, "find_repo_root", lambda: None)  # skip service.yaml probing

    m = build_model(allow_remote=False)
    new = m.get("brand-new-plugin")
    assert new is not None
    assert new.authored is False
    assert new.plugin.kind == "library"      # inferred from has_pyproject
    assert new.plugin.steps                  # always has a step
    # The authored one is still overlaid from the catalog.
    assert m.get("agent-worktrees").authored is True


def test_remote_discovery_when_no_checkout(monkeypatch):
    payload = json.dumps({"plugins": [
        {"name": "agent-worktrees", "description": "core"},
        {"name": "some-remote-only-plugin", "description": "remote"},
    ]}).encode("utf-8")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(discovery, "find_repo_root", lambda start=None: None)
    monkeypatch.setattr(discovery.urllib.request, "urlopen",
                        lambda url, timeout=5.0: _Resp(payload))

    src = discovery.discover(ref="main")
    assert src.kind == "remote"
    assert {d.name for d in src.plugins} == {"agent-worktrees", "some-remote-only-plugin"}
    # A remote-only plugin the catalog doesn't know still lands, inferred.
    m = build_model()
    ro = m.get("some-remote-only-plugin")
    assert ro is not None and ro.authored is False and ro.origin == "remote"


def test_offline_fallback_to_catalog(monkeypatch):
    monkeypatch.setattr(model, "discover",
                        lambda **kw: DiscoverySource("none", "", ()))
    m = build_model(allow_remote=False)
    # Falls back to the full authored catalog so the app still works.
    assert set(m.names) == set(load_catalog().names)
    assert all(ep.origin == "catalog" and ep.authored for ep in m.plugins)


def test_coverage_flags_phantom_and_uncovered(monkeypatch):
    # Discovery drops a catalog plugin (=> phantom) and adds an unknown (=> uncovered).
    fake = DiscoverySource("checkout", "x", (
        Discovered(name="agent-worktrees", origin="checkout", has_service_yaml=True),
        Discovered(name="mystery-plugin", origin="checkout"),
    ))
    monkeypatch.setattr(model, "discover", lambda **kw: fake)
    monkeypatch.setattr(model, "find_repo_root", lambda: None)

    cov = coverage(allow_remote=False)
    assert "mystery-plugin" in cov.uncovered
    assert "agent-bridge" in cov.phantom          # in catalog, not discovered here
    assert cov.ok is False                        # phantom is a hard error


def test_reconcile_command_reports_coverage(capsys):
    from configurator.__main__ import main
    rc = main(["plugins", "--reconcile"])
    out = capsys.readouterr().out
    assert "coverage" in out.lower()
    assert rc in (0, 1)
