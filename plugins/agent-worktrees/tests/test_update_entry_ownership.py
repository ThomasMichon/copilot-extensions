"""Older helpers require revalidated publication ownership, not release hashes."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from test_installer_command_binding import owned_install as shared_owned_install

from agent_worktrees import installer

owned_install = shared_owned_install
PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]
HELPERS = (
    "agent-worktrees-update-entry.sh", "agent-worktrees-update-entry.ps1",
    "agent-worktrees-update-json.awk",
)


@pytest.fixture
def published_engine(owned_install, monkeypatch):
    home, runtime = owned_install
    installer.write_deploy_manifest(home, "fixture-host")
    spec = importlib.util.spec_from_file_location(
        "update_entry_ownership_under_test", REPO / "worktree-manager/scripts/update_entry.py",
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    load_api = module._publication_api

    def hermetic_profile(engine):
        api, primitive = load_api(engine)
        current_environment = primitive._current_environment

        def environment(**arguments):
            return current_environment(**{**arguments, "os_profile": home})

        # Only the OS account profile is redirected, as in the binding suite;
        # source resolution and publication/owner validation remain real.
        monkeypatch.setattr(primitive, "_current_environment", environment)
        return api, primitive

    monkeypatch.setattr(module, "_publication_api", hermetic_profile)
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", raising=False)
    monkeypatch.setenv(
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR", str(runtime / "control-plane-providers.d"),
    )
    previous = home / ".copilot/installed-plugins/example/agent-worktrees"
    (previous / "src/agent_worktrees/__init__.py").write_text(
        'raise AssertionError("installed application must never be imported")\n',
    )
    local_bin = home / ".local/bin"
    local_bin.mkdir(parents=True)
    return module, home, runtime, previous, local_bin


def _helper(module, previous: Path, local_bin: Path, name: str, crlf: bool = False) -> bytes:
    current = module._engine_helper_source(PLUGIN, name).read_bytes().replace(b"\r\n", b"\n")
    older = current + b"\n# prior reviewed publication\n"
    source = module._engine_helper_source(previous, name)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(older)
    (local_bin / name).write_bytes(older.replace(b"\n", b"\r\n") if crlf else older)
    return older


@pytest.mark.parametrize("name", HELPERS)
@pytest.mark.parametrize("crlf", [False, True])
def test_update_entry_ownership_accepts_older_published_helper(published_engine, name, crlf):
    module, home, runtime, previous, local_bin = published_engine
    older = _helper(module, previous, local_bin, name, crlf)
    before = (runtime / "deploy-manifest.json").read_bytes()
    module.validate_manager_ownership(home / ".worktree-manager", REPO / "worktree-manager")
    assert (runtime / "deploy-manifest.json").read_bytes() == before
    assert (local_bin / name).read_bytes().replace(b"\r\n", b"\n") == older
    assert not (home / ".worktree-manager").exists()


def test_update_entry_ownership_accepts_selected_snapshot_after_payload_refresh(published_engine):
    module, home, runtime, previous, local_bin = published_engine
    name = HELPERS[0]
    _helper(module, previous, local_bin, name)
    api, primitive = module._publication_api(PLUGIN)
    resolution = primitive.resolve_context(
        payload_root=previous, plugin_id="agent-worktrees",
        durable_home=home / ".copilot-extensions",
    )
    snapshot = runtime / "snapshots/1.0.0"
    shutil.copytree(previous, snapshot)
    (snapshot / "snapshot-provenance.json").write_text(json.dumps({
        "schema": "copilot-extensions.legacy-snapshot-source", "version": 1,
        "resolution": resolution,
    }), encoding="utf-8")
    (runtime / "payload-dir").write_text(str(snapshot), encoding="utf-8")
    (runtime / "stamped-version").write_text("1.0.0", encoding="utf-8")
    module._engine_helper_source(previous, name).write_bytes(
        module._engine_helper_source(PLUGIN, name).read_bytes(),
    )
    # The read-only binding resolver's exact payload locator changed, but its
    # existing publication builder validates the same owner across snapshots.
    environment = dict(os.environ)
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    assert api["resolve_command_binding"](
        primitive, payload_root=snapshot, plugin_id="agent-worktrees", legacy_root=runtime,
        environment=environment,
    )["status"] != "selected"
    api["publish_legacy_binding"](
        primitive, payload_root=snapshot, plugin_id="agent-worktrees", legacy_root=runtime,
        environment=environment,
    )
    module.validate_manager_ownership(home / ".worktree-manager", REPO / "worktree-manager")


def test_update_entry_ownership_ignores_only_current_payload_locator(
    published_engine, monkeypatch,
):
    module, home, _runtime, previous, local_bin = published_engine
    _helper(module, previous, local_bin, HELPERS[0])
    caller = str(home / "different-current-invocation")
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", caller)
    module.validate_manager_ownership(home / ".worktree-manager", REPO / "worktree-manager")
    assert os.environ["COPILOT_PLUGIN_ROOT"] == caller


def test_update_entry_ownership_retains_prior_owner_during_pending_lifecycle(published_engine):
    module, home, runtime, previous, local_bin = published_engine
    _helper(module, previous, local_bin, HELPERS[0])
    path = runtime / "deploy-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["commandLifecycle"] = {
        "version": 1, "state": "pending",
        "owner": {key: value for key, value in manifest["commandBinding"].items()
                  if key != "payloadRoot"},
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = path.read_bytes()
    module.validate_manager_ownership(home / ".worktree-manager", REPO / "worktree-manager")
    assert path.read_bytes() == before


def test_update_entry_ownership_revalidation_uses_only_base_python(published_engine):
    module, home, _runtime, previous, local_bin = published_engine
    _helper(module, previous, local_bin, HELPERS[0])
    code = """
import pathlib, runpy, sys
home = pathlib.Path(sys.argv[2])
api = runpy.run_path(sys.argv[1])
validate = api["validate_manager_ownership"]
original = validate.__globals__["_publication_api"]
def hermetic_profile(engine):
    binding, primitive = original(engine)
    current = primitive._current_environment
    primitive._current_environment = lambda **kw: current(**{**kw, "os_profile": home})
    return binding, primitive
validate.__globals__["_publication_api"] = hermetic_profile
validate(home / ".worktree-manager", pathlib.Path(sys.argv[3]))
"""
    result = subprocess.run(
        [getattr(sys, "_base_executable", sys.executable), "-I", "-S", "-B", "-c", code,
         str(REPO / "worktree-manager/scripts/update_entry.py"), str(home),
         str(REPO / "worktree-manager")],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_update_entry_ownership_refuses_unattributed_snapshot_bytes(published_engine):
    module, home, runtime, previous, local_bin = published_engine
    name = HELPERS[0]
    _helper(module, previous, local_bin, name)
    snapshot = runtime / "snapshots/1.0.0"
    shutil.copytree(previous, snapshot)
    (snapshot / "snapshot-provenance.json").write_text(json.dumps({
        "schema": "copilot-extensions.legacy-snapshot-source", "version": 1, "resolution": {},
    }), encoding="utf-8")
    (runtime / "payload-dir").write_text(str(snapshot), encoding="utf-8")
    (runtime / "stamped-version").write_text("1.0.0", encoding="utf-8")
    module._engine_helper_source(previous, name).write_bytes(
        module._engine_helper_source(PLUGIN, name).read_bytes(),
    )
    with pytest.raises(RuntimeError, match="Foreign Worktrees command preserved"):
        module.validate_manager_ownership(home / ".worktree-manager", REPO / "worktree-manager")


@pytest.mark.parametrize("name", HELPERS)
def test_update_entry_ownership_refuses_unrelated_helper_with_valid_receipt(
    published_engine, name,
):
    module, home, _runtime, previous, local_bin = published_engine
    _helper(module, previous, local_bin, name)
    foreign = b"unrelated user command\n"
    (local_bin / name).write_bytes(foreign)
    with pytest.raises(RuntimeError, match="Foreign Worktrees command preserved"):
        module.validate_manager_ownership(home / ".worktree-manager", REPO / "worktree-manager")
    assert (local_bin / name).read_bytes() == foreign


@pytest.mark.parametrize("fault", ["unbound", "foreign-owner", "incomplete", "indirect-helper"])
def test_update_entry_ownership_cannot_adopt_unsupported_evidence(published_engine, fault):
    module, home, runtime, previous, local_bin = published_engine
    name = HELPERS[0]
    older = _helper(module, previous, local_bin, name)
    manifest_path = runtime / "deploy-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if fault == "unbound":
        manifest.pop("commandBinding")
    elif fault == "foreign-owner":
        manifest["commandBinding"]["sourceFingerprint"] = "sha256:" + "0" * 64
    elif fault == "incomplete":
        manifest["commandLifecycle"] = {"state": "pending"}
    else:
        source = module._engine_helper_source(previous, name)
        target = previous / "unowned.txt"
        target.write_bytes(older)
        source.unlink()
        try:
            source.symlink_to(target)
        except OSError as error:
            pytest.skip(f"fixture symlink creation unavailable: {error}")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = manifest_path.read_bytes()
    with pytest.raises(RuntimeError):
        module.validate_manager_ownership(home / ".worktree-manager", REPO / "worktree-manager")
    assert manifest_path.read_bytes() == before
    assert (local_bin / name).read_bytes() == older
