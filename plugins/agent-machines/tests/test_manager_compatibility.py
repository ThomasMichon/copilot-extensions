"""Standalone skew is negotiated before updating, never by retrying a failed update."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from agent_machines import manager_compatibility as compatibility
from agent_machines.automatic_update_ownership import suite_update_transaction
from agent_machines.fleet_update import CommandResult

from ._manager_compatibility_helpers import (
    GUARDED_HELP,
    LEGACY_HELP,
    ManagerProtocol,
    default_policy,
    installed_manager,
)


@pytest.mark.parametrize("guarded", [False, True])
def test_manager_compatibility_negotiates_exact_update_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guarded: bool,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=guarded)
    runner = ManagerProtocol(installation, guarded)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    expected = [*installation.invocation, "update"]
    if guarded:
        expected.extend(["--automatic-trigger", "legacy-agent-machines"])
    assert result.status == "ok", result.detail
    assert result.mode == ("guarded" if guarded else "legacy")
    assert result.returncode == 0
    assert result.command == expected
    assert runner.updates == [expected]
    assert sum(
        installation.arguments(command) == ["--version"] for command in runner.calls
    ) == (1 if guarded else 2)
    if not guarded:
        assert not any("automatic-updates" in command for command in runner.calls)


@pytest.mark.parametrize("owner", ["manager", "external", "disabled"])
def test_manager_compatibility_any_explicit_policy_prevents_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch)
    policy = {
        **default_policy(),
        "owner": owner,
        "project": "example" if owner == "manager" else None,
        "external_controller": "example-controller" if owner == "external" else None,
    }
    (installation.root / "automatic-updates.json").write_text(json.dumps(policy), encoding="utf-8")
    runner = ManagerProtocol(installation, False)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "deferred"
    assert runner.calls == []


@pytest.mark.parametrize(
    "args,code,stdout,stderr",
    [
        (("--version",), 0, "worktree-manager 9.9.9\n", ""),
        (("--version",), 1, "worktree-manager 1.2.3\n", "probe failed"),
        (("--help",), 0, "", ""),
        (("--help",), 1, LEGACY_HELP, "probe failed"),
        (("--help",), 0, LEGACY_HELP.replace("  update ", "  omitted "), ""),
        (("--help",), 0, LEGACY_HELP + "\nautomatic-updates capability unknown\n", ""),
        (("--help",), 0, GUARDED_HELP + "  automatic-updates      duplicate\n", ""),
        (("automatic-updates", "ownership", "--json"), 0, "not JSON", ""),
        (("automatic-updates", "ownership", "--json"), 0, "{}", ""),
        (("automatic-updates", "ownership", "--json"), 1, "{}", "ownership failed"),
    ],
)
def test_manager_compatibility_malformed_or_failed_probe_never_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[str, ...],
    code: int,
    stdout: str,
    stderr: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)
    runner = ManagerProtocol(installation, True)
    runner.replies[args] = CommandResult([], code, stdout, stderr)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "error"
    assert result.command is None
    assert runner.updates == []
    assert any(tuple(installation.arguments(command)) == args for command in runner.calls)


@pytest.mark.parametrize(
    "case", ["wrong-root", "invalid-policy", "unexpected-field", "claimed-owner"]
)
def test_manager_compatibility_ownership_reply_is_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)
    reply: dict[str, Any] = {"root": str(installation.root), "policy": default_policy()}
    if case == "wrong-root":
        reply["root"] = str(tmp_path / "different-manager")
    elif case == "invalid-policy":
        reply["policy"]["version"] = 2
    elif case == "unexpected-field":
        reply["unknown"] = True
    else:
        reply["policy"].update(owner="manager", project="example", provenance="user")
    runner = ManagerProtocol(installation, True)
    runner.replies[("automatic-updates", "ownership", "--json")] = CommandResult(
        [],
        0,
        json.dumps(reply),
    )
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "error"
    assert runner.updates == []


@pytest.mark.parametrize(
    "case", ["missing-completion", "bad-marker", "wrong-version", "foreign-stub"]
)
def test_manager_compatibility_requires_complete_matching_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch)
    if case == "missing-completion":
        (installation.slot / ".install-complete").unlink()
    elif case == "bad-marker":
        (installation.root / "current-version").write_text("../escape", encoding="utf-8")
    elif case == "wrong-version":
        (installation.slot / "src/worktree_manager/__init__.py").write_text(
            '__version__ = "9.9.9"\n',
            encoding="utf-8",
        )
    else:
        installation.command.write_text("foreign same-name command", encoding="utf-8")
    runner = ManagerProtocol(installation, False)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "error"
    assert runner.calls == []


def test_manager_compatibility_alternate_root_is_not_a_standalone_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "other-manager"))
    runner = ManagerProtocol(installation, False)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "error"
    assert runner.calls == []


@pytest.mark.parametrize("case", ["marker", "payload", "stub", "completion"])
def test_manager_compatibility_generation_change_during_probe_defers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch)

    def change(args: list[str]) -> None:
        if args != ["--help"]:
            return
        if case == "marker":
            (installation.root / "current-version").write_text("2.0.0", encoding="utf-8")
        elif case == "payload":
            (installation.slot / "src/worktree_manager/__main__.py").write_text(
                "# changed generation\n",
                encoding="utf-8",
            )
        elif case == "stub":
            installation.command.write_text("changed generation", encoding="utf-8")
        else:
            (installation.slot / ".install-complete").unlink()

    runner = ManagerProtocol(installation, False, before=change)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "deferred"
    assert runner.updates == []


def test_manager_compatibility_enrollment_winning_lock_acquisition_prevents_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch)

    @contextmanager
    def enrollment_wins(home: Path | None) -> Iterator[None]:
        with suite_update_transaction(home):
            (installation.root / "automatic-updates.json").write_text(
                json.dumps(default_policy()),
                encoding="utf-8",
            )
            yield

    monkeypatch.setattr(compatibility, "suite_update_transaction", enrollment_wins)
    runner = ManagerProtocol(installation, False)
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "deferred"
    assert runner.updates == []


def test_manager_compatibility_changed_capability_under_legacy_lock_defers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=True)
    runner = ManagerProtocol(installation, False)

    def upgrade(args: list[str]) -> None:
        if args == ["--help"] and sum(
            installation.arguments(call) == args for call in runner.calls
        ) == 2:
            runner.guarded = True

    runner.before = upgrade
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == "deferred"
    assert runner.updates == []


@pytest.mark.parametrize("guarded,code", [(False, 2), (False, 5), (True, 2), (True, 5)])
def test_manager_compatibility_update_failure_is_never_retried_unflagged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guarded: bool,
    code: int,
) -> None:
    installation = installed_manager(tmp_path, monkeypatch, retained=guarded)
    runner = ManagerProtocol(
        installation,
        guarded,
        update=lambda argv: CommandResult(argv, code, "", "update failed"),
    )
    result = compatibility.run_compatible_manager_update(runner=runner, home=installation.home)
    assert result.status == ("deferred" if code == 5 else "error")
    assert result.returncode == code
    assert len(runner.updates) == 1
    assert ("--automatic-trigger" in runner.updates[0]) is guarded
