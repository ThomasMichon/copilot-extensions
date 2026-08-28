"""Tests for the dependency-light legacy installer/bootstrap mutation gate."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _payload(
    tmp_path: Path,
    *,
    paths: object | None = None,
    services: list[dict[str, str]] | None = None,
    tasks: list[dict[str, str]] | None = None,
    omit_tasks: bool = False,
) -> tuple[Path, Path]:
    marketplace = tmp_path / "marketplace"
    payload = marketplace / "payloads" / "plugins" / "agent-example"
    bootstrap = payload / "scripts" / "installation-context"
    bootstrap.mkdir(parents=True)
    for name in (
        "installation-context.sh",
        "installation-context.ps1",
        "json-query.awk",
        "legacy-entrypoint-probe.sh",
        "legacy-entrypoint-probe.ps1",
    ):
        shutil.copy2(LIB / name, bootstrap / name)
    (bootstrap / "installation-context.sh").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
action="${1:-}"; shift
if [[ "$action" == validate ]]; then
    context=""
    while [[ $# -gt 0 ]]; do
        if [[ "$1" == --context ]]; then context="$2"; shift 2; else shift; fi
    done
    [[ -f "$context" && "$(cat "$context")" == *'"valid": true'* ]] || exit 1
    cat "$context"
    exit 0
fi
[[ "$action" == probe-legacy ]] || exit 64
payload=""; plugin=""; legacy=""; probe=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --payload-root) payload="$2"; shift 2 ;;
        --plugin-id) plugin="$2"; shift 2 ;;
        --legacy-root) legacy="$2"; shift 2 ;;
        --legacy-probe-json) probe="$2"; shift 2 ;;
        *) exit 64 ;;
    esac
done
[[ -n "$payload" && -n "$plugin" && -n "$legacy" && -n "$probe" ]] || exit 64
[[ -z "${PROBE_CAPTURE:-}" ]] || printf '%s' "$probe" > "$PROBE_CAPTURE"
[[ -z "${COPILOT_EXTENSIONS_CONTEXT:-}" ]] || exit 9
if [[ -n "${FAKE_RESOLVER_STATUS:-}" ]]; then exit "$FAKE_RESOLVER_STATUS"; fi
if [[ "$probe" == *'"result":"absent"'* ]]; then
    printf '%s\\n' '{"allowMutation":false,"probeReason":"namespaced-requested"}'
    exit 3
fi
exit 0
""",
        encoding="utf-8",
    )
    (bootstrap / "installation-context.sh").chmod(0o755)
    (bootstrap / "installation-context.ps1").write_text(
        """param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Action,
    [string]$PayloadRoot,
    [string]$PluginId,
    [string]$LegacyRoot,
    [string]$LegacyProbeJson,
    [string]$Context,
    [string]$DurableHome
)
if ($Action -eq 'validate') {
    $document = Get-Content -LiteralPath $Context -Raw | ConvertFrom-Json
    if (-not $document.valid) { exit 1 }
    Get-Content -LiteralPath $Context -Raw
    exit 0
}
$probe = $LegacyProbeJson | ConvertFrom-Json
if ($env:PROBE_CAPTURE) {
    [IO.File]::WriteAllText($env:PROBE_CAPTURE, $LegacyProbeJson)
}
if ($env:COPILOT_EXTENSIONS_CONTEXT) { exit 9 }
if ($env:FAKE_RESOLVER_STATUS) { exit [int]$env:FAKE_RESOLVER_STATUS }
if ($probe.result -eq 'absent') {
    [Console]::Out.Write('{"allowMutation":false,"probeReason":"namespaced-requested"}')
    exit 3
}
exit 0
""",
        encoding="utf-8",
    )
    _write_json(
        marketplace / ".plugin" / "marketplace.json",
        {
            "name": "Example Directory",
            "metadata": {"pluginRoot": "payloads"},
            "plugins": [{"name": "agent-example", "source": "plugins/agent-example"}],
        },
    )
    footprint: dict[str, object] = {
        "paths": paths if paths is not None else [".agent-example"],
        "services": services or [],
    }
    if not omit_tasks:
        footprint["tasks"] = tasks or []
    _write_json(
        payload / "payload-invocation.json",
        {
            "schema": "copilot-extensions.payload-invocation",
            "version": 1,
            "command": "agent-example",
            "module": "agent_example",
            "runtimeRoot": ".agent-example",
            "installation": {"legacyFootprint": footprint},
        },
    )
    profile = tmp_path / "profile"
    _write_json(
        profile / ".copilot-extensions" / "installation-mode.json",
        {
            "schema": "copilot-extensions.installation-mode",
            "version": 1,
            "installationMode": {"enabled": True},
        },
    )
    return payload, profile


def _run(
    runner: str,
    payload: Path,
    profile: Path,
    *,
    path: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    bootstrap = payload / "scripts" / "installation-context"
    legacy_root = profile / ".agent-example"
    if runner == "posix":
        command = [
            "bash",
            str(bootstrap / "legacy-entrypoint-probe.sh"),
            "--payload-root",
            str(payload),
            "--legacy-root",
            str(legacy_root),
        ]
    else:
        assert POWERSHELL is not None
        command = [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bootstrap / "legacy-entrypoint-probe.ps1"),
            "-PayloadRoot",
            str(payload),
            "-LegacyRoot",
            str(legacy_root),
        ]
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(profile),
            "USERPROFILE": str(profile),
            "COPILOT_HOME": str(profile / ".copilot"),
        }
    )
    if path is not None:
        env["PATH"] = path
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        command,
        cwd=payload,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


RUNNERS = ["posix"] + (["powershell"] if POWERSHELL else [])


@pytest.mark.parametrize("runner", RUNNERS)
def test_clean_namespaced_request_refuses_legacy_mutation(
    tmp_path: Path,
    runner: str,
) -> None:
    payload, profile = _payload(tmp_path)

    result = _run(runner, payload, profile)

    assert result.returncode == 3, result.stderr
    assert result.stdout == ""
    assert "namespaced-requested" in result.stderr
    assert not (profile / ".agent-example").exists()


@pytest.mark.parametrize("runner", RUNNERS)
def test_present_legacy_path_keeps_legacy_authoritative(
    tmp_path: Path,
    runner: str,
) -> None:
    payload, profile = _payload(tmp_path)
    (profile / ".agent-example").mkdir()

    result = _run(runner, payload, profile)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("runner", RUNNERS)
def test_incomplete_footprint_is_conservative_not_absent(
    tmp_path: Path,
    runner: str,
) -> None:
    payload, profile = _payload(tmp_path, omit_tasks=True)

    result = _run(runner, payload, profile)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("runner", RUNNERS)
def test_non_string_path_is_unknown_not_absent(
    tmp_path: Path,
    runner: str,
) -> None:
    payload, profile = _payload(tmp_path, paths=[123])
    capture = tmp_path / f"{runner}-probe.json"

    result = _run(
        runner,
        payload,
        profile,
        extra_env={"PROBE_CAPTURE": str(capture)},
    )

    assert result.returncode == 0, result.stderr
    evidence = json.loads(capture.read_text(encoding="utf-8"))
    assert evidence["declared"] is True
    assert evidence["result"] == "unknown"
    assert evidence["checkedAt"].endswith("Z")


@pytest.mark.parametrize("runner", RUNNERS)
def test_scalar_footprint_list_is_unknown_not_declared(
    tmp_path: Path,
    runner: str,
) -> None:
    payload, profile = _payload(tmp_path, paths=".agent-example")
    capture = tmp_path / f"{runner}-scalar-probe.json"

    result = _run(
        runner,
        payload,
        profile,
        extra_env={"PROBE_CAPTURE": str(capture)},
    )

    assert result.returncode == 0, result.stderr
    evidence = json.loads(capture.read_text(encoding="utf-8"))
    assert evidence["declared"] is False
    assert evidence["result"] == "unknown"


@pytest.mark.parametrize("runner", RUNNERS)
def test_loaded_systemd_user_service_counts_as_present(
    tmp_path: Path,
    runner: str,
) -> None:
    payload, profile = _payload(
        tmp_path,
        paths=[".agent-example"],
        services=[
            {
                "platform": "posix",
                "manager": "systemd-user",
                "name": "agent-example.service",
            }
        ],
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text("#!/bin/sh\nprintf 'loaded\\n'\n", encoding="utf-8")
    systemctl.chmod(0o755)

    result = _run(
        runner,
        payload,
        profile,
        path=os.pathsep.join((str(bin_dir), os.environ.get("PATH", ""))),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_windows_scheduled_task_counts_as_present(tmp_path: Path) -> None:
    payload, profile = _payload(
        tmp_path,
        tasks=[
            {
                "platform": "windows",
                "manager": "windows-scheduled-task",
                "name": "agent-example",
            }
        ],
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    task_command = bin_dir / "Get-ScheduledTask.ps1"
    task_command.write_text(
        "param([string]$TaskName)\n"
        "if ($TaskName -eq 'agent-example') { [pscustomobject]@{ TaskName = $TaskName } }\n",
        encoding="utf-8",
    )
    capture = tmp_path / "windows-task-probe.json"

    result = _run(
        "powershell",
        payload,
        profile,
        path=os.pathsep.join((str(bin_dir), os.environ.get("PATH", ""))),
        extra_env={
            "OS": "Windows_NT",
            "PROBE_CAPTURE": str(capture),
        },
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text(encoding="utf-8"))["result"] == "present"


@pytest.mark.parametrize("runner", RUNNERS)
def test_unrelated_inherited_context_is_ignored(
    tmp_path: Path,
    runner: str,
) -> None:
    payload, profile = _payload(tmp_path)
    context = tmp_path / "other-context.json"
    _write_json(context, {"pluginId": "agent-other", "valid": True})

    result = _run(
        runner,
        payload,
        profile,
        extra_env={"COPILOT_EXTENSIONS_CONTEXT": str(context)},
    )

    assert result.returncode == 3, result.stderr
    assert "namespaced-requested" in result.stderr


@pytest.mark.parametrize("runner", RUNNERS)
def test_malformed_foreign_context_fails_closed(
    tmp_path: Path,
    runner: str,
) -> None:
    payload, profile = _payload(tmp_path)
    context = tmp_path / "malformed-context.json"
    _write_json(context, {"pluginId": "agent-other"})

    result = _run(
        runner,
        payload,
        profile,
        extra_env={"COPILOT_EXTENSIONS_CONTEXT": str(context)},
    )

    assert result.returncode == 1
    assert "failed before a safe decision" in result.stderr


@pytest.mark.parametrize("runner", RUNNERS)
def test_resolver_failure_fails_closed(
    tmp_path: Path,
    runner: str,
) -> None:
    payload, profile = _payload(tmp_path)

    result = _run(
        runner,
        payload,
        profile,
        extra_env={"FAKE_RESOLVER_STATUS": "7"},
    )

    assert result.returncode == 1
    assert "failed before a safe decision" in result.stderr


@pytest.mark.parametrize("runner", RUNNERS)
def test_agent_index_status_does_not_create_legacy_stage(
    tmp_path: Path,
    runner: str,
) -> None:
    profile = tmp_path / "profile"
    payload = (
        profile
        / ".copilot"
        / "installed-plugins"
        / "example"
        / "agent-index"
    )
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    suffix = "sh" if runner == "posix" else "ps1"
    source = REPO / "plugins" / "agent-index" / "scripts" / f"install.{suffix}"
    shutil.copy2(source, scripts / source.name)
    shutil.copy2(REPO / "plugins" / "agent-index" / "pyproject.toml", payload)
    if runner == "posix":
        command = ["bash", str(scripts / "install.sh"), "status"]
    else:
        assert POWERSHELL is not None
        command = [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(scripts / "install.ps1"),
            "status",
        ]
    env = os.environ.copy()
    env.update({"HOME": str(profile), "USERPROFILE": str(profile)})

    result = subprocess.run(
        command,
        cwd=payload,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not (profile / ".agent-index").exists()


def test_exemplar_footprints_and_mutation_boundaries_are_complete() -> None:
    expected = {
        "agent-machines": {
            "paths": {
                ".agent-machines",
                ".local/bin/agent-machines",
                ".local/bin/agent-machines.cmd",
                ".local/bin/agent-machines.ps1",
            },
            "services": [],
            "tasks": [],
        },
        "agent-index": {
            "paths": {
                ".agent-index",
                ".local/bin/agent-index",
                ".local/bin/agent-index.cmd",
                ".local/bin/agent-index.ps1",
                ".config/systemd/user/agent-index.service",
                ".config/systemd/user/agent-index-engine.service",
            },
            "services": [
                {
                    "platform": "posix",
                    "manager": "systemd-user",
                    "name": "agent-index.service",
                },
                {
                    "platform": "posix",
                    "manager": "systemd-user",
                    "name": "agent-index-engine.service",
                },
            ],
            "tasks": [
                {
                    "platform": "windows",
                    "manager": "windows-scheduled-task",
                    "name": "agent-index",
                },
                {
                    "platform": "windows",
                    "manager": "windows-scheduled-task",
                    "name": "agent-index-engine",
                },
            ],
        },
    }
    for plugin, footprint in expected.items():
        manifest = json.loads(
            (REPO / "plugins" / plugin / "payload-invocation.json").read_text(
                encoding="utf-8"
            )
        )
        actual = manifest["installation"]["legacyFootprint"]
        assert set(actual["paths"]) == footprint["paths"]
        assert actual["services"] == footprint["services"]
        assert actual["tasks"] == footprint["tasks"]

    direct_installers = (
        REPO / "plugins" / "agent-machines" / "scripts" / "init.sh",
        REPO / "plugins" / "agent-machines" / "scripts" / "init.ps1",
        REPO / "plugins" / "agent-index" / "scripts" / "install.sh",
        REPO / "plugins" / "agent-index" / "scripts" / "install.ps1",
    )
    for path in direct_installers:
        text = path.read_text(encoding="utf-8")
        assert text.index("legacy-entrypoint-probe") < text.index(
            "# === install-contract:v4 self-stage"
        )
    index_installers = direct_installers[2:]
    for path in index_installers:
        text = path.read_text(encoding="utf-8")
        assert text.index("read-only-status") < text.index(
            "# === install-contract:v4 self-stage"
        )

    bootstrap_mutators = (
        REPO / "plugins" / "agent-machines" / "scripts" / "bootstrap-check.sh",
        REPO / "plugins" / "agent-machines" / "scripts" / "bootstrap-check.ps1",
        REPO / "plugins" / "agent-index" / "scripts" / "bootstrap-check.sh",
        REPO / "plugins" / "agent-index" / "scripts" / "bootstrap-check.ps1",
        REPO / "plugins" / "agent-index" / "scripts" / "ensure-service.sh",
        REPO / "plugins" / "agent-index" / "scripts" / "ensure-service.ps1",
    )
    for path in bootstrap_mutators:
        text = path.read_text(encoding="utf-8")
        assert "legacy-entrypoint-probe" in text
        if "bootstrap-check" in path.name:
            gate = (
                "Test-LegacyMutationAllowed"
                if path.suffix == ".ps1"
                else "legacy_mutation_allowed ||"
            )
            assert text.rindex(gate) < text.index("reconciling in background")
        if path.suffix == ".ps1":
            assert "$global:LASTEXITCODE = 1" in text
            assert "| Out-Null" in text
            if path.name == "ensure-service.ps1":
                assert text.index("$global:LASTEXITCODE = 1") < text.index(
                    "Start-Process"
                )

    for path in (
        REPO / "plugins" / "agent-machines" / "scripts" / "init.ps1",
        REPO / "plugins" / "agent-index" / "scripts" / "install.ps1",
    ):
        text = path.read_text(encoding="utf-8")
        assert "payload-origin" in text
        assert "COPILOT_PLUGIN_STAGED_FROM" in text
        assert "Threading.Mutex" in text
        assert text.index("WaitOne([TimeSpan]::FromSeconds(20))") < text.index(
            "Remove-Item $payloadDirMarker, $payloadOriginMarker"
        )
        assert "Remove-Item $payloadDirMarker, $payloadOriginMarker" in text
        assert text.rindex("WriteAllText($payloadOriginMarker") < text.rindex(
            "WriteAllText($payloadDirMarker"
        )

    machines_sh = (
        REPO / "plugins" / "agent-machines" / "scripts" / "init.sh"
    ).read_text(encoding="utf-8")
    assert 'ORIGINAL_ARGS=("$@")' in machines_sh
    assert 'set -- "${ORIGINAL_ARGS[@]}"' in machines_sh

    for path in direct_installers[:2:]:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".ps1":
            assert "$PSBoundParameters['InstallDir'] = $InstallDir" in text

    for path in direct_installers[2:]:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".ps1":
            assert "$PSBoundParameters['InstallDir'] = $InstallDir" in text
        else:
            assert text.index('_probe="$_payload/scripts/installation-context/') < text.index(
                '_lock="$_root/.provision.lock"'
            )
    assert machines_sh.index(
        '_probe="$_payload/scripts/installation-context/'
    ) < machines_sh.index('_lock="$_root/.provision.lock"')

    for plugin, timeouts in {
        "agent-machines": [30, 10],
        "agent-index": [30, 20, 10, 10],
    }.items():
        hooks = json.loads(
            (REPO / "plugins" / plugin / "hooks.json").read_text(encoding="utf-8")
        )
        assert [
            hook["timeoutSec"] for hook in hooks["hooks"]["sessionStart"]
        ] == timeouts
