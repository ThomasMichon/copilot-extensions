"""Actual source-builder evidence consumed by the Worktrees native boundary."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

import runtime_feed_configuration as configuration
import runtime_source_build as builds
from runtime_source_authentication import retain_installation_origin
from test_installer_wheel_acquisition import CERTIFICATE
from test_runtime_feed_configuration import environment
from test_runtime_profile_policy import owned_policy
from test_runtime_source_build import (
    build_only_feed, options, packaging_feed, select_source, source_with_process_owner,
)
from test_runtime_source_build import adopter as adopter  # noqa: F401
from test_runtime_source_build import configured_source as configured_source  # noqa: F401
from test_runtime_source_build import feeds as feeds  # noqa: F401
from test_runtime_source_build import target as target  # noqa: F401
from test_runtime_source_build import tmp_path as tmp_path  # noqa: F401
from test_runtime_source_lock import plan


def _native_owner(name, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "plugins" / "agent-worktrees" / "scripts"
    spec = importlib.util.spec_from_file_location(name, scripts / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_actual_worktrees_builder_binding_reaches_native_policy_without_relabeling_manager(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch,
):
    module, installation, settings = adopter
    old_payload = installation["payload_root"]
    payload = old_payload.with_name("agent-worktrees")
    old_payload.rename(payload)
    installation.update(
        payload_root=payload, plugin_id="agent-worktrees",
        legacy_root=installation["legacy_root"].with_name(".agent-worktrees"),
    )
    settings.write_text(settings.read_text().replace("agent-example@", "agent-worktrees@"))
    project = source_with_process_owner(configured_source, build_only=True)
    manifest = project / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('name="agent-example"', 'name="agent-worktrees"'))
    (project / "plugin.json").write_text(json.dumps({"name": "agent-worktrees", "version": "1.0.0"}))
    project.rename(project.with_name("agent-worktrees"))
    select_source(configured_source)
    selected_plan = plan(adopter)
    assert selected_plan.policy()["owner"]["pluginId"] == "agent-worktrees"
    root = Path(selected_plan.policy()["owner"]["runtimeRoot"])
    assert root == installation["legacy_root"] and root != configured_source[0]
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    build_only_feed(state)
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(
        configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest(),
    )
    association = _native_owner("runtime_install_profiles", monkeypatch)
    native = _native_owner("runtime_install_governance", monkeypatch)
    monkeypatch.setattr(
        builds, "retain_authenticated_source",
        lambda *args, **kwargs: pytest.fail("held origin was acquired again after discovery"),
    )
    arguments = options(adopter, target, env) | {"component": "agent-worktrees"}
    with retain_installation_origin(
        installation=installation, deadline=arguments["deadline"],
    ) as origin, builds.retain_source_runtime_inputs(
        selected_plan, **arguments, authenticated_source=origin,
    ) as source:
        receipt = source.wheels.receipt()
        original = json.dumps(receipt, sort_keys=True)
        policy = association.source_profile_policy(receipt, source.identity)
        assert policy == selected_plan.policy()
        assert json.dumps(receipt, sort_keys=True) == original
        assert set(receipt["binding"]) == {
            "profile", "source_lock", "build_toolchain", "build_profile",
        }
        assert "packaging" not in receipt["distributions"]
        assert source.identity == origin.identity
        governance = native.Governance.__new__(native.Governance)
        governance.root, governance.helper, governance.runtime_profile_policy = root, module, policy
        governance._profile_policy(installation)
        for field in ("source", "component", "source_authentication", "acquisition"):
            changed = json.loads(original)
            changed["binding"]["source_lock"][field] = {}
            with pytest.raises(ValueError, match="Worktrees source profile differs"):
                association.source_profile_policy(changed, source.identity)
        changed = json.loads(original)
        changed["binding"]["build_toolchain"] = {}
        with pytest.raises(ValueError, match="retained construction inputs"):
            association.source_profile_policy(changed, source.identity)
    assert source.identity.plugin == "agent-worktrees"
    assert not (root / "current-version").exists()
    owned_policy(module, installation, allowSourceFallback=False)
    with pytest.raises(native.InstallationRefused, match="profile policy changed"):
        governance._profile_policy(installation)
