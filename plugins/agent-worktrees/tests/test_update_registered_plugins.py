"""Tests for the ``update`` registered-plugin payload refresh.

``_update_registered_plugins`` closes the "phantom deploy" gap (aperture-labs
#2554): ``update`` must ``copilot plugin update`` (or install) EVERY
copilot-extensions plugin registered for the managed repo -- including
payload-only plugins (``runtimeScope: none``) like ``context-handoff`` -- and
do so BEFORE the service payload / runtime steps.
"""

from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import reconcile


def _install_config(monkeypatch: pytest.MonkeyPatch, anchor: str) -> None:
    repo = cfg.RepoConfig(
        anchor=anchor,
        worktree_root=str(Path(anchor).parent / "wt"),
        default_branch="master",
        remote="origin",
    )
    config = cfg.Config(
        srcroot=str(Path(anchor).parent),
        machine="test",
        platform="linux",
        repo_name="anchor",
        repos={"anchor": repo},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)


def _ok(stdout: str = "") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fail(code: int = 1, stderr: str = "boom") -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=code, stdout="", stderr=stderr)


def test_loop_covers_all_registered_including_payload_only(monkeypatch):
    """Every registered plugin (incl. a payload-only one) gets updated."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins",
        lambda repo_dir: ["agent-bridge", "context-handoff", "efforts"],
    )
    # All are already installed -> "update" verb.
    monkeypatch.setattr(
        reconcile, "installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )

    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    m._update_registered_plugins()

    # Marketplace refreshed once.
    assert ["copilot", "plugin", "marketplace", "update", reconcile.MARKETPLACE] in calls
    # Each registered plugin updated (payload-only context-handoff included).
    for name in ("agent-bridge", "context-handoff", "efforts"):
        assert [
            "copilot", "plugin", "update", f"{name}@{reconcile.MARKETPLACE}"
        ] in calls


def test_missing_plugin_uses_install_path(monkeypatch):
    """A plugin whose payload is not installed is installed, not updated."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins", lambda repo_dir: ["context-handoff"]
    )
    monkeypatch.setattr(reconcile, "installed_payload_dir", lambda name: None)

    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    m._update_registered_plugins()

    assert [
        "copilot", "plugin", "install", f"context-handoff@{reconcile.MARKETPLACE}"
    ] in calls
    # It must NOT try the update verb for an uninstalled plugin.
    assert not any(c[:3] == ["copilot", "plugin", "update"] for c in calls)


def test_single_failure_warns_and_continues(monkeypatch):
    """One plugin failing does not abort the rest of the loop."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins",
        lambda repo_dir: ["aaa", "bbb", "ccc"],
    )
    monkeypatch.setattr(
        reconcile, "installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )

    updated: list[str] = []

    def fake_run(argv, **kw):
        # marketplace refresh
        if argv[:3] == ["copilot", "plugin", "marketplace"]:
            return _ok()
        name = argv[3].split("@")[0]
        updated.append(name)
        if name == "bbb":
            return _fail()  # middle plugin fails
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Must not raise despite bbb failing.
    m._update_registered_plugins()

    # All three were attempted (loop continued past the failure).
    assert updated == ["aaa", "bbb", "ccc"]


def test_timeout_on_one_plugin_does_not_abort(monkeypatch):
    """A marketplace/plugin timeout warns and continues."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(
        reconcile, "read_enabled_plugins", lambda repo_dir: ["aaa", "bbb"]
    )
    monkeypatch.setattr(
        reconcile, "installed_payload_dir", lambda name: Path(f"/inst/{name}")
    )

    attempted: list[str] = []

    def fake_run(argv, **kw):
        if argv[:3] == ["copilot", "plugin", "marketplace"]:
            return _ok()
        name = argv[3].split("@")[0]
        attempted.append(name)
        if name == "aaa":
            raise subprocess.TimeoutExpired(cmd=argv, timeout=120)
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    m._update_registered_plugins()

    assert attempted == ["aaa", "bbb"]


def test_no_config_is_non_fatal(monkeypatch):
    """No resolvable project config -> silent no-op, no subprocess calls."""
    def _boom(*a, **k):
        raise RuntimeError("no config")

    monkeypatch.setattr(cfg, "load_config", _boom)

    def fake_run(argv, **kw):  # pragma: no cover - must not be called
        raise AssertionError("subprocess.run should not run without config")

    monkeypatch.setattr(subprocess, "run", fake_run)
    m._update_registered_plugins()  # must not raise


def test_no_registered_plugins_skips_marketplace(monkeypatch):
    """Empty registered set -> no marketplace refresh, no plugin calls."""
    _install_config(monkeypatch, "/repo/anchor")
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo_dir: [])

    def fake_run(argv, **kw):  # pragma: no cover - must not be called
        raise AssertionError("no subprocess calls expected")

    monkeypatch.setattr(subprocess, "run", fake_run)
    m._update_registered_plugins()


def test_ordering_plugins_before_services(monkeypatch):
    """cmd_update refreshes ALL plugin payloads BEFORE service modules/runtimes."""
    order: list[str] = []

    # Step 1: agent-worktrees payload update (subprocess).
    def fake_run(argv, **kw):
        order.append("aw-plugin-update")
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        m, "_update_registered_plugins",
        lambda: order.append("registered-plugins"),
    )
    monkeypatch.setattr(
        m, "_find_installed_plugin_dir", lambda: Path("/plugin/dir")
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")

    # The agent-worktrees installer (runtime) and module/runtime steps.
    real_exists = Path.exists

    def fake_exists(self):
        if str(self).endswith("install.sh"):
            order.append("aw-installer")
            return True
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists)

    def fake_installer_run(argv, **kw):
        # cmd_update runs the installer via subprocess without capture.
        return _ok()

    # cmd_update calls subprocess.run again for the installer; distinguish by
    # capture_output kwarg (plugin update captures; installer does not).
    def routed_run(argv, **kw):
        if kw.get("capture_output"):
            order.append("aw-plugin-update")
        else:
            order.append("aw-installer-run")
        return _ok()

    monkeypatch.setattr(subprocess, "run", routed_run)
    monkeypatch.setattr(
        m, "_update_modules",
        lambda *a, **k: order.append("modules"),
    )
    monkeypatch.setattr(
        m, "_fast_forward_project_anchors",
        lambda: order.append("anchors"),
    )

    args = types.SimpleNamespace(
        recreate_venv=False, skip_modules=None, no_anchor_sync=False
    )
    rc = m.cmd_update(args)
    assert rc == 0

    # Registered plugin payloads happen before modules (services/runtimes).
    assert order.index("registered-plugins") < order.index("modules")
    # And the agent-worktrees payload update precedes the registered loop.
    assert order.index("aw-plugin-update") < order.index("registered-plugins")
