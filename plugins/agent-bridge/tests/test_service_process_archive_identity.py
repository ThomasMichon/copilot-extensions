"""Archive dispatch remains a precise native daemon identity."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_bridge import service_process_identity as identity


def _arguments(platform: str) -> list[str]:
    archive = (
        r"C:\Payload with spaces\scripts\command-errors.pyz" if platform == "win32" else
        "/payload with spaces/scripts/command-errors.pyz"
    )
    return [
        "python.exe" if platform == "win32" else "python3.11",
        "-B", "-X", "utf8", archive, "run", "agent-bridge",
        "agent-bridge-runtime", "agent_bridge", "--", "--json", "start", "--passive",
    ]


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_archive_daemon_protocol_is_recognized(
    monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    monkeypatch.setattr(identity, "sys", SimpleNamespace(platform=platform))
    arguments = _arguments(platform)
    assert identity.bridge_daemon_argv(arguments)
    assert identity.bridge_daemon_argv([*arguments[:4], "--", *arguments[4:]])
    from agent_procutil._daemon_instance_child import _BOOTSTRAP

    helper = r"C:\Runtime\owner" if platform == "win32" else "/runtime/owner"
    assert identity.bridge_daemon_argv([
        arguments[0], "-c", _BOOTSTRAP, helper, *arguments[1:],
    ])


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_other_archive_protocols_and_embedded_arguments_are_not_daemon_identity(
    monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    monkeypatch.setattr(identity, "sys", SimpleNamespace(platform=platform))
    arguments = _arguments(platform)
    for index, replacement in (
        (0, "other"), (4, "command-errors.pyz"), (4, "/payload/other.pyz"),
        (5, "run-script"), (6, "other-command"), (7, "other-source"),
        (8, "other_module"), (9, "-"), (11, "status"),
    ):
        changed = list(arguments)
        changed[index] = replacement
        assert not identity.bridge_daemon_argv(changed), (index, replacement)
    assert not identity.bridge_daemon_argv([*arguments[:4], *arguments[5:]])
    assert not identity.bridge_daemon_argv([*arguments[:4], "other.py", *arguments[4:]])
    assert not identity.bridge_daemon_argv([arguments[0], "-c", "pass", *arguments[4:]])
    assert not identity.bridge_daemon_argv(arguments[:10])


def test_archive_identity_survives_native_windows_quoting() -> None:
    if sys.platform != "win32":
        pytest.skip("native Windows command-line splitting")
    arguments = _arguments("win32")
    actual = identity.split_windows_command(subprocess.list2cmdline(arguments))
    assert actual == arguments
    assert identity.bridge_daemon_argv(actual)
