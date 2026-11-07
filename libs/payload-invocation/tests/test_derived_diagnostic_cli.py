"""Execute every vendored native diagnostic CLI without installation writes."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
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


def _smoke_copy(copy: Path) -> bool:
    return copy.relative_to(ROOT).parts[:2] == ("plugins", "agent-worktrees")


@pytest.mark.parametrize("ancestor", ["clone", "agent-worktrees"])
@pytest.mark.parametrize("plugin", ["agent-worktrees", "agent-vault"])
def test_derived_diagnostic_smoke_ownership_ignores_clone_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ancestor: str, plugin: str,
) -> None:
    root = tmp_path / ancestor / "source"
    monkeypatch.setattr(sys.modules[__name__], "ROOT", root)
    copy = root / "plugins" / plugin / "scripts/installation-context/installation-context.sh"
    assert _smoke_copy(copy) is (plugin == "agent-worktrees")


def _profile_environment(home: Path) -> dict[str, str]:
    environment = _catalog_environment({
        "home": home, "payload": home,
        "environment": {"HOME": str(home), "USERPROFILE": str(home)},
    })
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT"):
        environment.pop(name, None)
    if os.name != "nt":
        uid = os.getuid()
        entry = ":".join((
            "fixture", "x", str(uid), str(os.getgid()), "fixture", str(home), "/bin/sh",
        ))
        lookup = home / "test-bin/getent"
        lookup.write_text(
            "#!/bin/sh\n"
            f'[ "$#" -eq 2 ] && [ "$1" = passwd ] && [ "$2" = {uid} ]'
            " || exit 1\n"
            f"printf '%s\\n' {shlex.quote(entry)}\n",
            encoding="utf-8",
        )
        lookup.chmod(0o755)
    return environment


def _powershell_command(home: Path, script: Path, arguments: list[str]) -> list[str]:
    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    wrapper = home / f"invoke-{arguments[0]}.ps1"
    tokens = [literal(arguments[0])]
    for index in range(1, len(arguments), 2):
        flag, value = arguments[index:index + 2]
        assert flag.startswith("-") and flag[1:].isalnum(), flag
        tokens.extend((flag, literal(value)))
    wrapper.write_text(
        f"& {literal(str(script))} {' '.join(tokens)}\nexit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )
    assert POWERSHELL is not None
    return [POWERSHELL, "-NoProfile", "-File", str(wrapper)]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("exit_code", [0, 3])
def test_derived_diagnostic_host_preserves_named_arguments(
    tmp_path: Path, exit_code: int,
) -> None:
    home = tmp_path / "space ' quoted caf\u00e9 home"
    home.mkdir()
    script = home / "advanced-parameter-fixture.ps1"
    script.write_text(
        "[CmdletBinding()]param([string]$Action,[string]$PayloadRoot,[string]$SourceJson)\n"
        "@{Action=$Action;PayloadRoot=$PayloadRoot;SourceJson=$SourceJson}"
        "|ConvertTo-Json -Compress\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    arguments = [
        "status", "-PayloadRoot", str(home), "-SourceJson", '{"quote":"a\'b","unicode":"\u00e9"}',
    ]
    command = _powershell_command(home, script, arguments)
    host = _PowerShellHost(home, _profile_environment(home))
    before = _snapshot(home)
    try:
        for _ in range(2):
            completed = host.run(command)
            assert completed.returncode == exit_code, completed.stderr
            assert json.loads(completed.stdout) == {
                "Action": arguments[0], "PayloadRoot": arguments[2], "SourceJson": arguments[4],
            }
            assert _snapshot(home) == before
    finally:
        host.close()
    assert _snapshot(home) == before


@pytest.mark.parametrize("copy", [
    pytest.param(
        copy, id=copy.relative_to(ROOT).as_posix(),
        marks=() if _smoke_copy(copy) else pytest.mark.derived_diagnostic_full,
    )
    for copy in _native_copies()
])
@pytest.mark.parametrize("namespaced", [False, True], ids=["mode_legacy", "mode_active"])
@pytest.mark.parametrize("pointer_exists", [False, True], ids=["pointer_absent", "pointer_retained"])
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
    commands = {}
    for action in ("status", "probe-legacy"):
        command = GOVERNANCE._runner_command(
            runner, GOVERNANCE._cli_arguments(layout, legacy, action=action),
        )
        script_index = command.index("-File") + 1 if runner == "powershell" else 1
        command[script_index] = str(copy)
        commands[action] = (
            _powershell_command(home, copy, command[script_index + 1:])
            if runner == "powershell" else command
        )
    host = _PowerShellHost(home, environment) if runner == "powershell" else None
    before = _snapshot(home)
    try:
        for _ in range(2):
            for action in ("status", "probe-legacy"):
                command = commands[action]
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
