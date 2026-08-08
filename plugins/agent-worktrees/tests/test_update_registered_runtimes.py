"""Tests for the registered-runtime reconcile step of ``update`` (dotfiles #1025).

``update`` runs a runtime installer only for agent-worktrees (self) and the
``modules.json`` services (``_update_modules``). Every other enabled runtime
plugin -- agent-codespaces, agent-containers, … -- only got its PAYLOAD
refreshed, so its versioned venv could serve stale code and ``--force`` never
reached it. ``_reconcile_registered_runtimes`` closes that: it runs each such
plugin's ``scripts/install.* update`` on version drift (or unconditionally under
``--force``), excluding the module/self runtimes handled elsewhere.
"""
from __future__ import annotations

import json
import subprocess
import types
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import reconcile


@pytest.fixture(autouse=True)
def _pin_copilot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "_resolve_copilot", lambda: "copilot")


def _install_config(monkeypatch: pytest.MonkeyPatch, anchor: str = "/repo/anchor") -> None:
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


def _ok() -> types.SimpleNamespace:
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def _capture_installers(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture install.sh runtime invocations; make install.sh 'exist'."""
    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)

    real_exists = Path.exists

    def fake_exists(self):
        if str(self).endswith("install.sh"):
            return True
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", fake_exists)
    return calls


def _stub_reconcile(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: list[str],
    scopes: dict[str, str],
    deployed: dict[str, str | None],
    payload: dict[str, str],
) -> None:
    monkeypatch.setattr(reconcile, "read_enabled_plugins", lambda repo_dir: list(enabled))
    monkeypatch.setattr(
        reconcile, "installed_payload_dir",
        lambda name: Path(f"/inst/{name}") if name in scopes else None,
    )
    monkeypatch.setattr(
        reconcile, "manifest_runtime_scope",
        lambda pdir: scopes.get(Path(pdir).name, "none"),
    )
    monkeypatch.setattr(
        reconcile, "payload_version",
        lambda pdir: payload.get(Path(pdir).name),
    )
    monkeypatch.setattr(
        reconcile, "runtime_deployed_version",
        lambda name, *a, **k: deployed.get(name),
    )


def _installed_names(calls: list[list[str]]) -> set[str]:
    """Plugin names whose install.sh update was invoked."""
    out: set[str] = set()
    for c in calls:
        for i, tok in enumerate(c):
            if tok.endswith("install.sh"):
                out.add(Path(tok).parent.parent.name)
    return out


def test_version_drift_triggers_runtime_install(monkeypatch):
    _install_config(monkeypatch)
    _stub_reconcile(
        monkeypatch,
        enabled=["agent-codespaces"],
        scopes={"agent-codespaces": "universal"},
        deployed={"agent-codespaces": "0.3.4-dev62"},
        payload={"agent-codespaces": "0.3.4-dev102"},
    )
    calls = _capture_installers(monkeypatch)
    m._reconcile_registered_runtimes(Path("/plugin/dir"), "linux", force=False)
    assert "agent-codespaces" in _installed_names(calls)


def test_current_runtime_is_skipped_without_force(monkeypatch):
    _install_config(monkeypatch)
    _stub_reconcile(
        monkeypatch,
        enabled=["agent-codespaces"],
        scopes={"agent-codespaces": "universal"},
        deployed={"agent-codespaces": "0.3.4-dev102"},
        payload={"agent-codespaces": "0.3.4-dev102"},
    )
    calls = _capture_installers(monkeypatch)
    m._reconcile_registered_runtimes(Path("/plugin/dir"), "linux", force=False)
    assert "agent-codespaces" not in _installed_names(calls)


def test_force_reinstalls_even_when_current(monkeypatch):
    _install_config(monkeypatch)
    _stub_reconcile(
        monkeypatch,
        enabled=["agent-codespaces"],
        scopes={"agent-codespaces": "universal"},
        deployed={"agent-codespaces": "0.3.4-dev102"},
        payload={"agent-codespaces": "0.3.4-dev102"},
    )
    calls = _capture_installers(monkeypatch)
    m._reconcile_registered_runtimes(Path("/plugin/dir"), "linux", force=True)
    assert "agent-codespaces" in _installed_names(calls)


def test_payload_only_plugin_is_skipped(monkeypatch):
    _install_config(monkeypatch)
    _stub_reconcile(
        monkeypatch,
        enabled=["context-handoff"],
        scopes={"context-handoff": "none"},
        deployed={},
        payload={"context-handoff": "0.1.0"},
    )
    calls = _capture_installers(monkeypatch)
    m._reconcile_registered_runtimes(Path("/plugin/dir"), "linux", force=True)
    assert _installed_names(calls) == set()


def test_module_and_self_runtimes_are_excluded(monkeypatch, tmp_path):
    _install_config(monkeypatch)
    # A modules.json listing agent-bridge -> it must be excluded here.
    (tmp_path / "modules.json").write_text(
        json.dumps({"modules": [{"name": "agent-bridge"}]}), encoding="utf-8"
    )
    _stub_reconcile(
        monkeypatch,
        enabled=["agent-bridge", "agent-worktrees", "agent-codespaces"],
        scopes={"agent-bridge": "universal", "agent-worktrees": "universal",
                "agent-codespaces": "universal"},
        deployed={"agent-bridge": None, "agent-worktrees": None,
                  "agent-codespaces": None},
        payload={"agent-bridge": "1", "agent-worktrees": "1", "agent-codespaces": "1"},
    )
    calls = _capture_installers(monkeypatch)
    m._reconcile_registered_runtimes(tmp_path, "linux", force=True)
    got = _installed_names(calls)
    assert got == {"agent-codespaces"}
    assert "agent-bridge" not in got and "agent-worktrees" not in got


def test_skip_all_modules_skips_reconcile(monkeypatch):
    _install_config(monkeypatch)
    _stub_reconcile(
        monkeypatch,
        enabled=["agent-codespaces"],
        scopes={"agent-codespaces": "universal"},
        deployed={"agent-codespaces": None},
        payload={"agent-codespaces": "1"},
    )
    calls = _capture_installers(monkeypatch)
    m._reconcile_registered_runtimes(Path("/plugin/dir"), "linux", [], force=True)
    assert _installed_names(calls) == set()


def test_named_skip_excludes_that_runtime(monkeypatch):
    _install_config(monkeypatch)
    _stub_reconcile(
        monkeypatch,
        enabled=["agent-codespaces", "agent-containers"],
        scopes={"agent-codespaces": "universal", "agent-containers": "universal"},
        deployed={"agent-codespaces": None, "agent-containers": None},
        payload={"agent-codespaces": "1", "agent-containers": "1"},
    )
    calls = _capture_installers(monkeypatch)
    m._reconcile_registered_runtimes(
        Path("/plugin/dir"), "linux", ["agent-codespaces"], force=True)
    got = _installed_names(calls)
    assert got == {"agent-containers"}


def test_failure_is_best_effort(monkeypatch):
    _install_config(monkeypatch)
    _stub_reconcile(
        monkeypatch,
        enabled=["aaa", "bbb"],
        scopes={"aaa": "universal", "bbb": "universal"},
        deployed={"aaa": None, "bbb": None},
        payload={"aaa": "1", "bbb": "1"},
    )
    attempted: list[str] = []

    def fake_run(argv, **kw):
        name = None
        for tok in argv:
            if tok.endswith("install.sh"):
                name = Path(tok).parent.parent.name
        attempted.append(name)
        if name == "aaa":
            return types.SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return _ok()

    monkeypatch.setattr(subprocess, "run", fake_run)
    real_exists = Path.exists
    monkeypatch.setattr(
        Path, "exists",
        lambda self: True if str(self).endswith("install.sh") else real_exists(self),
    )
    # Must not raise despite aaa failing; both attempted.
    m._reconcile_registered_runtimes(Path("/plugin/dir"), "linux", force=True)
    assert attempted == ["aaa", "bbb"]


def test_no_config_is_noop(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("no config")

    monkeypatch.setattr(cfg, "load_config", _boom)

    def fake_run(argv, **kw):  # pragma: no cover
        raise AssertionError("no subprocess expected")

    monkeypatch.setattr(subprocess, "run", fake_run)
    m._reconcile_registered_runtimes(Path("/plugin/dir"), "linux", force=True)


def test_module_names_reads_manifest(tmp_path):
    (tmp_path / "modules.json").write_text(
        json.dumps({"modules": [{"name": "agent-bridge"}, {"name": "x"}]}),
        encoding="utf-8",
    )
    assert m._module_names(tmp_path) == {"agent-bridge", "x"}
    assert m._module_names(tmp_path / "nope") == set()
