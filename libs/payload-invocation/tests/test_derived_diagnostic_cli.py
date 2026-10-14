"""Execute every vendored native diagnostic CLI without installation writes."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from test_command_binding import (
    POWERSHELL,
    ROOT,
    _PowerShellHost,
    _catalog_environment,
    _load,
    _snapshot,
)

GOVERNANCE = _load(
    ROOT / "libs/installation-context/tests/test_installation_mode_governance.py",
    "derived_cli_governance",
)
INVENTORY = _load(ROOT / "tools/sync-installation-context.py", "derived_cli_inventory")


def _native_copies() -> list[Path]:
    name = "installation-context.ps1" if os.name == "nt" else "installation-context.sh"
    return [
        destination for source, destination in INVENTORY.vendor_pairs()
        if source.name == name
    ]


def _profile_environment(home: Path) -> dict[str, str]:
    environment = _catalog_environment({
        "home": home, "payload": home,
        "environment": {"HOME": str(home), "USERPROFILE": str(home)},
    })
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT"):
        environment.pop(name, None)
    if os.name != "nt":
        import pwd

        account = pwd.getpwuid(os.getuid())
        entry = ":".join((
            account.pw_name, account.pw_passwd, str(account.pw_uid),
            str(account.pw_gid), account.pw_gecos, str(home), account.pw_shell,
        ))
        lookup = home / "test-bin/getent"
        lookup.write_text(
            "#!/bin/sh\n"
            f'[ "$#" -eq 2 ] && [ "$1" = passwd ] && [ "$2" = {account.pw_uid} ]'
            " || exit 1\n"
            f"printf '%s\\n' {shlex.quote(entry)}\n",
            encoding="utf-8",
        )
        lookup.chmod(0o755)
    return environment


@pytest.mark.parametrize("copy", [
    pytest.param(
        copy, id=copy.relative_to(ROOT).as_posix(),
        marks=() if "agent-worktrees" in copy.parts else pytest.mark.derived_diagnostic_full,
    )
    for copy in _native_copies()
])
@pytest.mark.parametrize("namespaced", [False, True], ids=["legacy", "active"])
@pytest.mark.parametrize("pointer_exists", [False, True], ids=["absent", "retained"])
def test_derived_native_diagnostic_cli_read_only(
    tmp_path: Path, copy: Path, namespaced: bool, pointer_exists: bool,
) -> None:
    runner = "powershell" if os.name == "nt" else "posix"
    if runner == "powershell" and POWERSHELL is None:
        pytest.skip("PowerShell is not installed")
    if runner == "posix" and GOVERNANCE.BASH is None:
        pytest.skip("Native Bash runner is unavailable")
    home = tmp_path / "home"
    home.mkdir()
    layout = GOVERNANCE._cell_layout(home)
    legacy = home / "legacy"
    legacy.mkdir()
    (legacy / "retained-state").write_text("retained\n", encoding="utf-8")
    profile_environment = GOVERNANCE._profile_environment(home)
    if namespaced:
        GOVERNANCE._activation(layout, environment=profile_environment)
        GOVERNANCE._write_json(
            home / ".copilot-extensions/installation-mode.json",
            GOVERNANCE._policy(True, str(layout["marketplace_id"])),
        )
    if pointer_exists:
        pointer = Path(layout["durable"]) / GOVERNANCE.PLUGIN_ID / "runtime-root"
        pointer.parent.mkdir(parents=True)
        pointer.write_text("retained-foreign-pointer\n", encoding="utf-8")
    environment = _profile_environment(home)
    copies = _native_copies()
    assert copies
    assert len(copies) == len(INVENTORY.ADOPTERS)
    host = _PowerShellHost(home, environment) if runner == "powershell" else None
    before = _snapshot(home)
    try:
        for _ in range(2):
            for action in ("status", "probe-legacy"):
                command = GOVERNANCE._runner_command(
                    runner, GOVERNANCE._cli_arguments(layout, legacy, action=action),
                )
                script_index = command.index("-File") + 1 if host else 1
                command[script_index] = str(copy)
                completed = host.run(command) if host else subprocess.run(
                    command, cwd=home, env=environment, capture_output=True,
                    text=True, encoding="utf-8", check=False, timeout=30,
                )
                assert completed.returncode == (
                    3 if namespaced and action == "probe-legacy" else 0
                ), (copy, action, completed.stdout, completed.stderr)
                value = json.loads(completed.stdout)
                assert value["actualMode"] == (
                    "namespaced" if namespaced else "legacy"
                ), (copy, action, value)
                assert value["status"] == "ready", (copy, action, value)
                assert value["runtimeRoot"] == str(
                    Path(layout["plugin_root"] if namespaced else legacy).resolve()
                ), (copy, action, value)
                assert value["policy"]["authoritative"] is True
                assert value["policy"]["path"] == str(
                    (home / ".copilot-extensions/installation-mode.json").resolve()
                )
                assert value["reason"] == (
                    "namespaced-active" if namespaced else "policy-default-false"
                ), (copy, action, value)
                if action == "probe-legacy":
                    assert value["allowMutation"] is (not namespaced)
                    assert value["probeReason"] == (
                        "namespaced-active" if namespaced else "legacy-active"
                    )
                assert _snapshot(home) == before, (copy, action)
    finally:
        if host:
            host.close()
    assert _snapshot(home) == before
