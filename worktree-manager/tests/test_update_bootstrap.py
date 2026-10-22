"""Fresh bootstrap repair precedes, and does not replace, suite orchestration."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from worktree_manager import __main__ as manager
from worktree_manager import core_install, update_bootstrap


def test_update_bootstrap_delivery_paths_are_sandboxed():
    home = Path(os.environ["USERPROFILE"])
    assert home == Path.home() and home.name == "fake-home"
    assert "WORKTREE_MANAGER_ROOT" not in os.environ
    assert "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR" not in os.environ


@pytest.mark.parametrize("rc", [0, 17])
def test_update_bootstrap_repairs_before_orchestration(monkeypatch, rc):
    calls = []
    monkeypatch.setattr(
        update_bootstrap, "_repair_installation",
        lambda source: calls.append("repair") or update_bootstrap.RepairResult(rc),
    )
    monkeypatch.setattr(
        manager, "_cmd_update_steps",
        lambda rest, run, **kwargs: calls.append(rest) or 5,
    )
    flags = ["--project", "sample", "--force", "--skip-modules", "a b"]
    assert manager._cmd_update(["--repair-engine", "/fresh/source", *flags]) == (rc or 5)
    assert calls == (["repair"] if rc else ["repair", flags])


def test_update_bootstrap_acquires_source_before_native_repair(monkeypatch, tmp_path):
    root = tmp_path / "runtime"
    (root / "bin").mkdir(parents=True)
    identity = {
        "installationId": "fixture", "pluginId": "agent-worktrees", "mode": "legacy",
        "runtimeRoot": str(root), "sourceFingerprint": "fixture", "environment": {},
    }
    installation = update_bootstrap._Installation(root, tmp_path / "payload", identity, tmp_path)
    calls = []
    monkeypatch.setattr(
        update_bootstrap, "_refresh_source",
        lambda source, env: calls.append("acquire") or update_bootstrap._RefreshedSource(
            identity, installation.payload,
        ),
    )
    monkeypatch.setattr(
        update_bootstrap, "_incumbent",
        lambda source, env, payload: calls.append(("admit", payload)) or installation,
    )
    monkeypatch.setattr(
        update_bootstrap, "_invoke",
        lambda target, action, env: calls.append(("native", target, action)) or 19,
    )
    assert update_bootstrap.repair_engine(tmp_path / "fresh-source") == 19
    assert calls == [
        "acquire", ("admit", installation.payload), ("admit", installation.payload),
        ("native", installation, "update"),
    ]


def test_update_bootstrap_missing_installer_is_failure(monkeypatch, capsys):
    monkeypatch.setattr(core_install, "install_script", lambda path: None)
    assert update_bootstrap.repair_engine(Path("/missing/fresh-source")) == 1
    assert "unavailable" in capsys.readouterr().err


def test_update_bootstrap_does_not_route_ordinary_update_through_repair(monkeypatch):
    monkeypatch.setattr(
        update_bootstrap, "_repair_installation",
        lambda source: pytest.fail("ordinary Manager update must not require bootstrap staging"),
    )
    monkeypatch.setattr(manager, "_cmd_update_steps", lambda rest, run, **kwargs: 5)
    assert manager._cmd_update(["--force"]) == 5


def test_update_bootstrap_missing_source_never_repairs_or_orchestrates(monkeypatch, capsys):
    monkeypatch.setattr(
        update_bootstrap, "_repair_installation",
        lambda source: pytest.fail("missing bootstrap source must not repair"),
    )
    monkeypatch.setattr(
        manager, "_cmd_update_steps",
        lambda *args, **kwargs: pytest.fail("missing bootstrap source must not orchestrate"),
    )
    assert manager._cmd_update(["--repair-engine"]) == 2
    assert "requires its fresh source checkout" in capsys.readouterr().err


def test_update_bootstrap_fresh_manager_does_not_reselect_source(monkeypatch):
    from worktree_manager import engine_client, self_install

    monkeypatch.setattr(
        update_bootstrap, "_repair_installation",
        lambda source: update_bootstrap.RepairResult(0),
    )
    monkeypatch.setattr(
        self_install, "self_update",
        lambda **kwargs: pytest.fail("fresh Manager must not fetch a second source"),
    )
    calls = []
    monkeypatch.setattr(
        engine_client, "run_engine_passthrough",
        lambda project, args: calls.append((project, args)) or 0,
    )
    assert manager._cmd_update([
        "--repair-engine", "/fresh/source", "--project", "sample", "--force",
    ]) == 0
    assert calls == [("sample", ["update", "--no-manager", "--force"])]
