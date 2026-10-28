"""Read-only native admission and explicit coordinator cutover boundaries."""

from __future__ import annotations

import argparse
import json
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
            args, conditional=SimpleNamespace(), routing_dir=tmp_path,
        ) == (3 if inhibited else 0)
        capsys.readouterr()
    assert len(calls) == 2
