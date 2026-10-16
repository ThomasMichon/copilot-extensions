"""Guest doctor never falls through into unrelated registry/session repairs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from agent_worktrees import doctor_cli_parser, guest_environment_cli, maintenance_cli


def _args(*flags: str) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    doctor_cli_parser.add_parser(parser.add_subparsers(dest="command"))
    return parser.parse_args(["doctor", "--guest-distribution", "Example", "--json", *flags])


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("state", ["registered", "not-applicable", "unregistered"])
def test_guest_doctor_is_targeted_and_registration_is_explicit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], apply: bool, state: str,
) -> None:
    status = {
        "status": "ready", "registrationState": state,
        "hostDefault": {"status": "unavailable" if state != "not-applicable" else "observed"},
    }
    inspect = Mock(side_effect=[status, {**status, "registrationState": "registered"}])
    register = Mock(return_value={"created": True})
    monkeypatch.setattr(guest_environment_cli.primitive, "inspect_guest_environment", inspect)
    monkeypatch.setattr(guest_environment_cli.primitive, "register_guest_environment", register)
    monkeypatch.setattr(
        maintenance_cli.cfg, "project_name",
        Mock(side_effect=AssertionError("General doctor must not run")),
    )
    code = maintenance_cli.cmd_doctor(_args(*(["--fix"] if apply else [])))
    report = json.loads(capsys.readouterr().out)
    needs_registration = state == "unregistered"
    assert code == (1 if needs_registration and not apply else 0)
    assert report["scope"] == "guest-environment"
    if needs_registration and apply:
        register.assert_called_once_with("Example")
        assert inspect.call_count == 2
    else:
        register.assert_not_called()
        assert inspect.call_count == 1


@pytest.mark.parametrize("status", ["mismatch", "unavailable"])
def test_unverified_default_blocks_apply_before_enrollment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: str,
) -> None:
    monkeypatch.setattr(
        guest_environment_cli.primitive, "inspect_guest_environment",
        Mock(return_value={"status": status, "registrationState": "not-applicable"}),
    )
    register = Mock()
    monkeypatch.setattr(guest_environment_cli.primitive, "register_guest_environment", register)
    assert maintenance_cli.cmd_doctor(_args("--fix")) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    register.assert_not_called()


def test_ssh_cannot_enroll_without_verified_named_entry(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    primitive = guest_environment_cli.primitive
    monkeypatch.setattr(
        primitive, "inspect_guest_environment",
        Mock(return_value={"status": "ready", "registrationState": "unregistered"}),
    )
    monkeypatch.setattr(
        primitive, "register_guest_environment",
        Mock(side_effect=primitive.InstallationContextError("guest-identity-unverified")),
    )
    assert maintenance_cli.cmd_doctor(_args("--fix")) == 1
    assert "guest-identity-unverified" in json.loads(capsys.readouterr().out)["detail"]


@pytest.mark.parametrize("flag", ["--gc-sessions", "--prune-pivots", "--repair-creation"])
def test_guest_selector_rejects_other_repair_scopes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], flag: str,
) -> None:
    inspect = Mock()
    monkeypatch.setattr(guest_environment_cli.primitive, "inspect_guest_environment", inspect)
    flags = [flag, "example-record"] if flag == "--repair-creation" else [flag]
    assert maintenance_cli.cmd_doctor(_args("--fix", *flags)) == 1
    assert "cannot be combined" in json.loads(capsys.readouterr().out)["detail"]
    inspect.assert_not_called()


def test_doctor_registers_via_real_primitive_then_ssh_is_read_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path,
) -> None:
    primitive = guest_environment_cli.primitive
    current = primitive._current_environment

    def environment(**kwargs: Any) -> tuple[dict[str, Any], Path]:
        return current(**{**kwargs, "os_profile": tmp_path, "platform": "posix"})

    monkeypatch.setattr(primitive, "_current_environment", environment)
    monkeypatch.setattr(primitive, "_is_wsl_guest", lambda: True)
    monkeypatch.setattr(primitive, "_guest_machine_id", lambda: "a" * 32)
    monkeypatch.setattr(primitive, "_host_default_wsl_distribution", lambda: None)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Example")
    assert maintenance_cli.cmd_doctor(_args()) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "registration-required"
    assert list(tmp_path.iterdir()) == []
    assert maintenance_cli.cmd_doctor(_args("--fix")) == 0
    assert json.loads(capsys.readouterr().out)["registration"]["created"] is True
    monkeypatch.delenv("WSL_DISTRO_NAME")

    def snapshot() -> dict[str, tuple[int, bytes | None]]:
        return {
            str(path.relative_to(tmp_path)): (
                path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None,
            )
            for path in [tmp_path, *tmp_path.rglob("*")]
        }

    before = snapshot()
    for flags in ([], ["--fix"], [], ["--fix"]):
        assert maintenance_cli.cmd_doctor(_args(*flags)) == 0
        assert json.loads(capsys.readouterr().out)["guest_environment"]["registrationState"] == "registered"
        assert snapshot() == before
    monkeypatch.setattr(primitive, "_guest_machine_id", lambda: "b" * 32)
    assert maintenance_cli.cmd_doctor(_args("--fix")) == 1
    assert "guest-identity-foreign" in json.loads(capsys.readouterr().out)["detail"]
    assert snapshot() == before
