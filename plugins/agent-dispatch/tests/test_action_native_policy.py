"""Read-only native admission and explicit coordinator cutover boundaries."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_dispatch.action_policy import ACTIONS
from agent_dispatch.coordinator_cli import _cmd_cutover

PLUGIN = Path(__file__).resolve().parents[1]


def _policy(root: Path, action: str, inhibited: bool) -> None:
    root.mkdir(exist_ok=True)
    (root / "action-controls.json").write_text(json.dumps({action: {"disabled": inhibited}}))


@pytest.mark.parametrize("action", ACTIONS)
def test_native_policy_is_read_only_and_each_invocation_rechecks(
    tmp_path: Path, action: str,
) -> None:
    root = tmp_path / "install"
    command = [
        sys.executable, "-I", "-B", str(PLUGIN / "scripts" / "action_controls.py"),
        "--root", str(root), "--action", action,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout)["ok"]
    assert not root.exists()
    for inhibited in (True, False, True, False):
        _policy(root, action, inhibited)
        before = (root / "action-controls.json").read_bytes()
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert result.returncode == (3 if inhibited else 0), result.stderr
        assert json.loads(result.stdout)["ok"] is not inhibited
        assert (root / "action-controls.json").read_bytes() == before
        if inhibited:
            assert action in result.stderr and str(root) in result.stderr


@pytest.mark.parametrize("conditional", [False, True])
def test_explicit_cutover_denies_before_any_legacy_or_conditional_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conditional: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import zdd.conditional
    import zdd.cutover

    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    _policy(root, "coordinator-replacement", True)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("inhibited cutover reached an effect")

    monkeypatch.setattr(zdd.conditional, "run_conditional_cutover", forbidden)
    monkeypatch.setattr(zdd.cutover, "CutoverOrchestrator", forbidden)
    args = argparse.Namespace(json=True, force=False, recover=False)
    assert _cmd_cutover(
        args, conditional=SimpleNamespace() if conditional else None, routing_dir=tmp_path,
        install_root=root if conditional else None,
    ) == 3
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and result["action"] == "coordinator-replacement"


def test_same_conditional_caller_observes_clear_and_repeated_inhibition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import zdd.conditional

    root = tmp_path / "install"
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    calls = []

    def admitted(*_args: object, **_kwargs: object) -> SimpleNamespace:
        calls.append(True)
        return SimpleNamespace(ok=True, to_dict=lambda: {"ok": True})

    monkeypatch.setattr(zdd.conditional, "run_conditional_cutover", admitted)
    args = argparse.Namespace(
        json=True, force=False, recover=False, health_timeout=1, drain_timeout=1,
    )
    for inhibited in (True, False, True, False):
        _policy(root, "coordinator-replacement", inhibited)
        assert _cmd_cutover(
            args, conditional=SimpleNamespace(), routing_dir=root, install_root=root,
        ) == (3 if inhibited else 0)
        capsys.readouterr()
    assert len(calls) == 2


@pytest.mark.parametrize("selection", ["other-root", "other-context", "normalized-alias"])
def test_service_env_cannot_redirect_a_checked_cutover_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str,
) -> None:
    from agent_dispatch import config, single_instance

    root = tmp_path / "selected"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv("AGENT_DISPATCH_ROUTING_DIR", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(config, "client_token", lambda: None)
    if selection == "other-context":
        line = f"COPILOT_EXTENSIONS_CONTEXT={tmp_path / 'other-context'}"
    elif selection == "normalized-alias":
        nested = other / "nested"
        nested.mkdir()
        (other / "selected").mkdir()
        alias = tmp_path / "alias"
        try:
            alias.symlink_to(nested, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"native symbolic-link support is unavailable: {exc}")
        line = f"AGENT_DISPATCH_INSTALL_DIR={alias}/../selected"
    else:
        _policy(other, "coordinator-replacement", True)
        line = f"AGENT_DISPATCH_INSTALL_DIR={other}"
    (root / "service.env").write_text(line + "\n")

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("cross-installation cutover reached its native lock")

    monkeypatch.setattr(single_instance, "SingleInstance", forbidden)
    args = argparse.Namespace(json=True, force=False, recover=False, drain_timeout=1)
    assert _cmd_cutover(args) == 3
    assert os.environ["AGENT_DISPATCH_INSTALL_DIR"] == str(root)
    assert "COPILOT_EXTENSIONS_CONTEXT" not in os.environ


def test_service_env_keeps_benign_pins_inside_the_selected_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import config, single_instance

    root = tmp_path / "selected"
    root.mkdir()
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv("AGENT_DISPATCH_ROUTING_DIR", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_HOST", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_PORT", raising=False)
    monkeypatch.setattr(config, "client_token", lambda: None)
    (root / "service.env").write_text(
        f"AGENT_DISPATCH_INSTALL_DIR={root}/\n"
        "AGENT_DISPATCH_HOST=127.0.0.2\nAGENT_DISPATCH_PORT=61002\n",
    )
    targets = []

    class ReachedNativeLock(Exception):
        pass

    def boundary(target: Path) -> None:
        targets.append(target)
        raise ReachedNativeLock

    monkeypatch.setattr(single_instance, "SingleInstance", boundary)
    with pytest.raises(ReachedNativeLock):
        _cmd_cutover(argparse.Namespace(json=True, force=False, recover=False, drain_timeout=1))
    assert targets == [root / "serve-start.lock"]
    assert os.environ["AGENT_DISPATCH_INSTALL_DIR"] == str(root)
    assert os.environ["AGENT_DISPATCH_HOST"] == "127.0.0.2"
    assert os.environ["AGENT_DISPATCH_PORT"] == "61002"


def test_conditional_policy_uses_captured_target_not_caller_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zdd.conditional

    root = tmp_path / "selected"
    root.mkdir()
    other = tmp_path / "other"
    _policy(other, "coordinator-replacement", True)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.delenv("AGENT_DISPATCH_ROUTING_DIR", raising=False)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("conditional cutover reached another installation's routing")

    monkeypatch.setattr(zdd.conditional, "run_conditional_cutover", forbidden)
    args = argparse.Namespace(
        json=True, force=False, recover=False, health_timeout=1, drain_timeout=1,
    )
    assert _cmd_cutover(
        args, conditional=SimpleNamespace(), routing_dir=other, install_root=other,
    ) == 3


def test_captured_custom_conditional_route_ignores_caller_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import zdd.conditional

    root = tmp_path / "selected"
    route = tmp_path / "custom-route"
    caller_root = tmp_path / "unrelated-caller"
    _policy(caller_root, "coordinator-replacement", True)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(caller_root))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "caller-route"))
    calls = []

    def admitted(target: Path, **_kwargs: object) -> SimpleNamespace:
        calls.append(target)
        return SimpleNamespace(ok=True, to_dict=lambda: {"ok": True})

    monkeypatch.setattr(zdd.conditional, "run_conditional_cutover", admitted)
    args = argparse.Namespace(
        json=True, force=False, recover=False, health_timeout=1, drain_timeout=1,
    )
    assert _cmd_cutover(
        args, conditional=SimpleNamespace(), routing_dir=route, install_root=root,
    ) == 0
    assert calls == [route]


def test_conditional_cutover_without_captured_policy_root_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_dispatch.install_paths
    import zdd.conditional

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unqualified conditional cutover used caller state")

    monkeypatch.setattr(agent_dispatch.install_paths, "install_dir", forbidden)
    monkeypatch.setattr(zdd.conditional, "run_conditional_cutover", forbidden)
    assert _cmd_cutover(
        argparse.Namespace(json=True), conditional=SimpleNamespace(), routing_dir=tmp_path,
    ) == 3
