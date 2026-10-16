"""Maintenance must leave replaceable payloads before it waits or builds."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("bash")


def _block(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"# === install-contract:v5 {name}")
    end_marker = f"# === end install-contract:v5 {name.split(' (')[0]} ==="
    end = text.index(end_marker, start) + len(end_marker)
    return text[start:end]


def _env(home: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_"))
    }
    env.update({
        "HOME": home.as_posix(),
        "USERPROFILE": str(home),
        "COPILOT_PLUGIN_INSTALL_STAGED": "1",
        "COPILOT_PLUGIN_STAGED_FROM": "unrelated-parent-payload",
        "COPILOT_PLUGIN_INSTALL_SMOKE": "1",
        "COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP": "8",
        "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "20",
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
    })
    return env


def _entry(tmp_path: Path, extension: str, contextual: bool = False) -> tuple[Path, Path]:
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-example"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    canonical = ROOT / "plugins" / "agent-worktrees" / "scripts" / f"install.{extension}"
    header = "param([string]$Action = 'install')\n" if extension == "ps1" else "#!/usr/bin/env bash\nset -e\n"
    helper = f"installer-watchdog.{extension}"
    shutil.copyfile(ROOT / "libs" / "installer-engine" / helper, scripts / helper)
    header += (
        ". (Join-Path $PSScriptRoot 'installer-watchdog.ps1')\n"
        if extension == "ps1" else
        '. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/installer-watchdog.sh"\n'
    )
    staging = _block(canonical, "self-stage")
    if contextual:
        text = canonical.read_text(encoding="utf-8")
        marker = (
            "    # The standard self-stage block is byte-identical"
            if extension == "ps1"
            else "    # The standard self-stage block is intentionally byte-identical"
        )
        staging = text[text.index(marker):text.index("# === install-contract:v5 self-stage")]
        if extension == "ps1":
            header += (
                "$ErrorActionPreference = 'Stop'\n"
                "$contextPayload = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path\n"
                "$InstallDir = Join-Path $env:USERPROFILE 'runtime'\n"
                "$hostExe = (Get-Process -Id $PID).Path\n"
                "if ($true) {\n"
            )
        else:
            header += (
                'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
                'PLUGIN_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"\n'
                '__aw_install_root="$HOME/runtime"\n'
                'mkdir -p "$__aw_install_root"\n'
                'if true; then\n'
            )
    entry = scripts / f"install.{extension}"
    entry.write_text(
        header + staging + "\n"
        + _block(canonical, "smoke seam (test-only)") + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return payload, entry


def _command(entry: Path, extension: str) -> list[str]:
    if extension == "ps1":
        assert PWSH
        return [PWSH, "-NoProfile", "-File", str(entry)]
    assert BASH
    return [BASH, entry.as_posix()]


@pytest.mark.skipif(not BASH, reason="bash required")
@pytest.mark.parametrize(
    "action",
    ["slot-provision", "slot-validate", "slot-complete", "slot-completion-validate",
     "slot-cutover", "cell-provision"],
)
def test_relative_machines_slot_entry_loads_watchdog_after_leaving_payload(tmp_path, action):
    source = (ROOT / "plugins" / "agent-machines" / "scripts" / "init.sh").read_text("utf-8")
    end = source.index("\n# === install-contract:v5 self-stage")
    scripts = tmp_path / "payload" / "scripts"
    scripts.mkdir(parents=True)
    entry = scripts / "init.sh"
    entry.write_text(
        source[:end] + '\nprintf "ready:%s\\n" "$PWD"\n', encoding="utf-8",
    )
    shutil.copyfile(
        ROOT / "libs" / "installer-engine" / "installer-watchdog.sh",
        scripts / "installer-watchdog.sh",
    )
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [BASH, entry.relative_to(tmp_path).as_posix(), action],
        cwd=tmp_path, env=_env(home), capture_output=True, text=True, timeout=10,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("ready:")
    assert _reported_path(result.stdout.strip().removeprefix("ready:")) == home.resolve()


def _reported_path(value: str) -> Path:
    if os.name == "nt" and value.startswith("/") and BASH:
        result = subprocess.run(
            [BASH, "-c", 'cygpath -w "$1"', "bash", value],
            capture_output=True, text=True, check=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        value = result.stdout.strip()
    return Path(value).resolve()


def _wait_for_smoke_receipt(
    receipt: Path,
    poll: Callable[[], int | None],
    timeout: float = 30,
) -> dict[str, object]:
    # A cold parent shell starts before the staged child's 20-second watchdog.
    deadline = time.monotonic() + timeout
    last_error: FileNotFoundError | json.JSONDecodeError | UnicodeDecodeError | None = None
    while True:
        child_exit = poll()
        try:
            data = json.loads(receipt.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError) as error:
            last_error = error
        else:
            if not isinstance(data, dict):
                pytest.fail(f"staged receipt is not a JSON object: {data!r}")
            return data
        if child_exit is not None:
            pytest.fail(f"child exited with code {child_exit}; no complete staged receipt: {last_error}")
        if time.monotonic() >= deadline:
            pytest.fail(f"receipt wait timed out; no complete staged receipt: {last_error}")
        time.sleep(0.05)


@pytest.mark.parametrize("initial", [None, "", '{"staged":'])
def test_smoke_receipt_waits_for_complete_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: str | None,
) -> None:
    receipt = tmp_path / "smoke.json"
    if initial is not None:
        receipt.write_text(initial, encoding="utf-8")

    def complete_receipt(_delay: float) -> None:
        receipt.write_text('{"staged": true}', encoding="utf-8")

    monkeypatch.setattr(time, "sleep", complete_receipt)
    assert _wait_for_smoke_receipt(receipt, lambda: None) == {"staged": True}


def test_smoke_receipt_rejects_incomplete_json_after_child_exit(tmp_path: Path) -> None:
    receipt = tmp_path / "smoke.json"
    receipt.write_text("", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="child exited with code 0; no complete staged receipt"):
        _wait_for_smoke_receipt(receipt, lambda: 0)


def test_smoke_receipt_parses_after_observing_child_exit(tmp_path: Path) -> None:
    receipt = tmp_path / "smoke.json"
    receipt.write_text('{"staged":', encoding="utf-8")

    def finish_child() -> int:
        receipt.write_text('{"staged": true}', encoding="utf-8")
        return 0

    assert _wait_for_smoke_receipt(receipt, finish_child) == {"staged": True}


def test_smoke_receipt_retries_partial_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = tmp_path / "smoke.json"
    receipt.write_bytes(b'{"working_dir":"\xc3')

    def complete_receipt(_delay: float) -> None:
        receipt.write_bytes(b'{"working_dir":"\xc3\xa9"}')

    monkeypatch.setattr(time, "sleep", complete_receipt)
    assert _wait_for_smoke_receipt(receipt, lambda: None) == {"working_dir": "\u00e9"}


def test_smoke_receipt_rejects_partial_utf8_after_child_exit(tmp_path: Path) -> None:
    receipt = tmp_path / "smoke.json"
    receipt.write_bytes(b'{"working_dir":"\xc3')
    with pytest.raises(pytest.fail.Exception, match="child exited with code 0; no complete staged receipt"):
        _wait_for_smoke_receipt(receipt, lambda: 0)


def test_smoke_receipt_timeout_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(pytest.fail.Exception, match="receipt wait timed out; no complete staged receipt"):
        _wait_for_smoke_receipt(tmp_path / "smoke.json", lambda: None, timeout=0)


def test_smoke_receipt_rejects_non_object_json(tmp_path: Path) -> None:
    receipt = tmp_path / "smoke.json"
    receipt.write_text("[]", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="not a JSON object"):
        _wait_for_smoke_receipt(receipt, lambda: None)


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("stage_flag", ["", "1", "context-install"])
@pytest.mark.parametrize("contextual", [False, True], ids=["legacy", "context"])
def test_inherited_stage_flag_cannot_pin_payload(
    tmp_path: Path, extension: str, stage_flag: str, contextual: bool,
) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload, entry = _entry(tmp_path, extension, contextual)
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    # MSYS retains its launch CWD in an emulated-exec supervisor. Windows
    # launchers must supply HOME up front; native POSIX exercises relocation.
    launch_cwd = home if os.name == "nt" and extension == "sh" else payload
    env = _env(home)
    env.update({"TEMP": str(home), "TMP": str(home), "TMPDIR": str(home)})
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = stage_flag
    proc = subprocess.Popen(
        _command(entry, extension), cwd=launch_cwd, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kwargs,
    )
    try:
        receipt = home / ".agent-example" / "smoke.json"
        data = _wait_for_smoke_receipt(receipt, proc.poll)
        assert data["staged"] is True
        ran_from = data["ran_from"]
        working_dir = data["working_dir"]
        staged_from = data["staged_from"]
        assert isinstance(ran_from, str)
        assert isinstance(working_dir, str)
        assert isinstance(staged_from, str)
        assert _reported_path(ran_from).is_relative_to(home.resolve())
        assert _reported_path(working_dir) == home.resolve()
        assert staged_from.replace("\\", "/").endswith("/example/agent-example")
        if contextual:
            assert not (home / ".agent-worktrees").exists()
        assert proc.poll() is None
        backup = payload.with_name("replaced-payload")
        payload.rename(backup)
        payload.mkdir()
        assert proc.poll() is None
        stdout, stderr = proc.communicate(timeout=20)
        assert proc.returncode == 0, (stdout, stderr)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=25)


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_self_stage_watchdog_still_times_out(tmp_path: Path, extension: str) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    _, entry = _entry(tmp_path, extension)
    env = _env(home)
    env["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "1"
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        _command(entry, extension), cwd=home, env=env,
        capture_output=True, text=True, timeout=15, **kwargs,
    )
    assert result.returncode == 124, (result.stdout, result.stderr)
    assert "WATCHDOG-KILL" in (home / ".agent-example" / "reconcile.err.log").read_text()


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("contextual", [False, True], ids=["legacy", "context"])
def test_watchdog_cutover_can_extend_the_build_deadline(
    tmp_path: Path, extension: str, contextual: bool,
) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload, entry = _entry(tmp_path, extension, contextual)
    (payload / "plugin.json").write_text(
        '{"name":"agent-example","cutoverDeadlineSeconds":12}\n', encoding="utf-8",
    )
    env = _env(home)
    env.update({
        "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "4",
        "COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP": "7",
        "COPILOT_PLUGIN_INSTALL_SMOKE_PHASE": "cutover",
    })
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    started = time.monotonic()
    result = subprocess.run(
        _command(entry, extension), cwd=home, env=env,
        capture_output=True, text=True, timeout=30, **kwargs,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert time.monotonic() - started >= 6, "cutover never exercised the extended lifetime"
    assert (home / ".agent-example" / "smoke.json").is_file()
    log = home / (".agent-example" if not contextual else "runtime") / "reconcile.err.log"
    assert not log.exists() or "WATCHDOG-KILL" not in log.read_text()


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("contextual", [False, True], ids=["legacy", "context"])
def test_watchdog_cutover_cannot_extend_past_the_hard_cap(
    tmp_path: Path, extension: str, contextual: bool,
) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload, entry = _entry(tmp_path, extension, contextual)
    (payload / "plugin.json").write_text(
        '{"name":"agent-example","cutoverDeadlineSeconds":3}\n', encoding="utf-8",
    )
    text = entry.read_text(encoding="utf-8")
    call = (
        "Enter-InstallPhase -Name $env:COPILOT_PLUGIN_INSTALL_SMOKE_PHASE"
        if extension == "ps1" else
        'enter_install_phase "$COPILOT_PLUGIN_INSTALL_SMOKE_PHASE"'
    )
    assert text.count(call) == 1
    entry.write_text(text.replace(
        call, call + (" -BudgetSec 100" if extension == "ps1" else " 100"),
    ), encoding="utf-8", newline="\n")
    env = _env(home)
    env.update({
        "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "4",
        "COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP": "12",
        "COPILOT_PLUGIN_INSTALL_SMOKE_PHASE": "cutover",
    })
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    started = time.monotonic()
    result = subprocess.run(
        _command(entry, extension), cwd=home, env=env,
        capture_output=True, text=True, timeout=25, **kwargs,
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 124, (result.stdout, result.stderr)
    log = home / (".agent-example" if not contextual else "runtime") / "reconcile.err.log"
    assert "phase=cutover, build=4s, cutover=3s" in log.read_text()
    assert 6 <= elapsed < 16


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("action", ["engine", "engine-update"])
def test_watchdog_index_engine_actions_extend_for_startup(
    tmp_path: Path, extension: str, action: str,
) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload, entry = _entry(tmp_path, extension)
    (payload / "plugin.json").write_text(
        '{"name":"agent-example","cutoverDeadlineSeconds":15}\n', encoding="utf-8",
    )
    text = (ROOT / "plugins/agent-index/scripts" / f"install.{extension}").read_text(encoding="utf-8")
    names = (
        ("Ensure-EngineRunning", "Restart-EngineDaemon")
        if extension == "ps1" else ("_register_engine_daemon", "_restart_engine_daemon")
    )
    functions = []
    for name in names:
        start = f"function {name}" if extension == "ps1" else f"{name}() {{"
        tail = text.split(start, 1)[1]
        next_function = r"\nfunction \w" if extension == "ps1" else r"\n[a-z_]+\(\) [\{(]"
        functions.append(start + re.split(next_function, tail, maxsplit=1)[0])
    if extension == "ps1":
        branch = next(line for line in text.splitlines() if line.startswith(f"    '{action}' {{"))
        fixture = (
            "$ErrorActionPreference = 'Stop'\n$NoService = $false\n"
            "$EngineVenvPython = 'Invoke-EngineFixture'\n$EngineHome = $env:USERPROFILE\n"
            "$EngineEnvFile = 'fixture.env'\n$EngineTaskName = 'fixture'\n"
            "function Test-Path { param($Path) return $true }\n"
            "function Get-ActiveSlotPython { return 'Invoke-EngineFixture' }\n"
            "function Write-EngineFiles { }\nfunction Test-EnginePort { return $false }\n"
            "function Get-ScheduledTask { param($TaskName, $ErrorAction) return $null }\n"
            "function Write-Ok { param($Msg) }\nfunction Write-Skip { param($Msg) }\n"
            "function Write-Warn { param($Msg) }\n"
            "function Install-Engine { param([switch]$Upgrade) Start-Sleep -Seconds 2; return $true }\n"
            "function Invoke-WithEnvFile { param($Path, $Extra, $Body) & $Body }\n"
            "function Invoke-EngineFixture {\n"
            "  if ($args -contains 'start') { Start-Sleep -Seconds 7 }\n"
            "  $global:LASTEXITCODE = 0\n"
            "}\n"
            + "\n".join(functions)
            + "\nswitch ($Action) {\n" + branch + "\n}\n"
        )
    else:
        start = text.index(f"    {action})")
        branch = text[start:text.index(";;", start) + 2]
        fixture = (
            'NO_SERVICE=0\nENGINE_HOME="$HOME"\nENGINE_ENV_FILE="$HOME/engine.env"\n'
            'ENGINE_LAUNCHER="$HOME/engine.sh"\nENGINE_VENV_PYTHON="$(command -v bash)"\n'
            'UNIT_DIR="$HOME/units"\nENGINE_SYSTEMD_UNIT=fixture.service\n'
            'mkdir -p "$UNIT_DIR"; touch "$UNIT_DIR/$ENGINE_SYSTEMD_UNIT"\n'
            '_ok() { :; }; _skip() { :; }; _warn() { :; }; _write_engine_files() { :; }\n'
            '_install_engine() { sleep 2; }\n'
            'systemctl() {\n'
            '  case "$2" in\n'
            '    start|restart) sleep 7; touch "$HOME/started" ;;\n'
            '    is-active) [[ -f "$HOME/started" ]] ;;\n'
            '    *) return 0 ;;\n'
            '  esac\n'
            '}\n'
            + "\n".join(functions)
            + '\ncase "$1" in\n' + branch + "\nesac\n"
        )
    with entry.open("a", encoding="utf-8") as stream:
        stream.write(fixture)
    env = _env(home)
    env.pop("COPILOT_PLUGIN_INSTALL_SMOKE")
    env["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "6"
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    started = time.monotonic()
    result = subprocess.run(
        _command(entry, extension) + [action], cwd=home, env=env,
        capture_output=True, text=True, timeout=30, **kwargs,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert time.monotonic() - started >= 9, "engine startup did not outlive the build budget"


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("superseded_after_lock", [False, True])
def test_watchdog_dispatch_cutover_lock_wait_extends_build_deadline(
    tmp_path: Path, extension: str, superseded_after_lock: bool,
) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload, entry = _entry(tmp_path, extension)
    (payload / "plugin.json").write_text(
        '{"name":"agent-example","cutoverDeadlineSeconds":15}\n', encoding="utf-8",
    )
    source = (ROOT / "plugins/agent-dispatch/scripts" / f"install.{extension}").read_text(
        encoding="utf-8",
    )
    if extension == "ps1":
        function = "function Invoke-Update" + source.split("function Invoke-Update", 1)[1].split(
            "\nfunction Invoke-Start", 1,
        )[0]
        fixture = (
            "$ErrorActionPreference = 'Stop'\n$NoService = $false\n"
            "$script:GlobalActivationLockTimeoutSeconds = 450\n$script:checks = 0\n"
            "function Install-Runtime { Start-Sleep -Seconds 2 }\n"
            "function Invoke-DowngradeGuard { }; function Write-UpdateMarker { }\n"
            "function Clear-UpdateMarker { }; function Write-Skip { param($m) }\n"
            "function Test-ActivationSupersededNow {\n"
            "  $script:checks++\n"
            f"  return (${str(superseded_after_lock).lower()} -and $script:checks -eq 2)\n"
            "}\n"
            "function Enter-PluginSnapshotLock {\n"
            "  param($InstallDir, $TimeoutSeconds)\n"
            "  Start-Sleep -Seconds 7\n"
            "  [IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'lock-complete'), 'done')\n"
            "  return [System.Threading.Mutex]::new($true)\n"
            "}\n"
            "function Test-CoordinatorHealthy { return $false }\n"
            "function Install-CoordinatorTask {\n"
            "  param([switch]$NoStart)\n"
            "  [IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'reconciled'), 'done')\n"
            "}\n"
            "function Remove-CoordinatorFirewallRule { }; function Install-SupervisorTask { }\n"
            "function Confirm-CoordinatorRunning { }; function Invoke-SupervisorAudit { }\n"
            + function + "\nInvoke-Update\n"
        )
    else:
        function = "do_update() {" + source.split("do_update() {", 1)[1].split(
            "\ndo_start()", 1,
        )[0]
        fixture = (
            'INSTALL_DIR="$HOME/runtime"\nmkdir -p "$INSTALL_DIR"\n'
            'SUPERVISOR_UNIT=fixture\nSUPERVISOR_ENV_FILE=fixture.env\nchecks=0\n'
            '_ensure_runtime() { sleep 2; }\n'
            '_downgrade_guard() { :; }; _write_update_marker() { :; }; _clear_update_marker() { :; }\n'
            '_skip() { :; }; _supervisor_audit() { :; }; _install_supervisor_service() { :; }\n'
            '_activation_superseded_now() {\n'
            '  checks=$((checks + 1))\n'
            f'  [[ "{int(superseded_after_lock)}" == 1 && "$checks" == 2 ]]\n'
            '}\n'
            'flock() { if [[ "$1" != -u ]]; then sleep 7; touch "$HOME/lock-complete"; fi; }\n'
            '_coordinator_cutover() { enter_install_phase cutover; return 1; }\n'
            '_install_service() { touch "$HOME/reconciled"; }\n'
            + function + "\ndo_update\n"
        )
    with entry.open("a", encoding="utf-8") as stream:
        stream.write(fixture)
    environment = _env(home)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE")
    environment["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "6"
    environment.pop("COPILOT_EXT_NO_FLOCK", None)
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    started = time.monotonic()
    result = subprocess.run(
        _command(entry, extension), cwd=home, env=environment,
        capture_output=True, text=True, timeout=30, **flags,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert time.monotonic() - started >= 9, "the actual lock wait never exceeded the build budget"
    assert (home / "lock-complete").is_file()
    assert (home / "reconciled").exists() is not superseded_after_lock


def _native_watchdog(
    tmp_path: Path, extension: str, bodies: dict[str, str], arguments: list[str],
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    helper = ROOT / "libs" / "installer-engine" / f"installer-watchdog.{extension}"
    header = (
        f"$ErrorActionPreference = 'Stop'\nSet-StrictMode -Version Latest\n. '{helper}'\n"
        if extension == "ps1" else f'#!/usr/bin/env bash\nset -euo pipefail\n. "{helper.as_posix()}"\n'
    )
    entry = tmp_path / f"probe.{extension}"
    entry.write_text(header + bodies[extension] + "\n", encoding="utf-8")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.run(
        _command(entry, extension) + arguments, cwd=tmp_path, env=env or _env(tmp_path),
        capture_output=True, text=True, timeout=15, **kwargs,
    )


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("manifest,environment,expected", [
    ({}, {}, (480, 300)),
    ({"installerDeadlineSeconds": 12, "cutoverDeadlineSeconds": 7}, {}, (12, 7)),
    ({"cutoverDeadlineSeconds": 7}, {"COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "8"}, (8, 7)),
    ({}, {
        "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "8",
        "AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "0",
    }, (0, 300)),
    ({}, {"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "-1"}, (-1, 300)),
    ({}, {"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": " +09 "}, (9, 300)),
    ({"installerDeadlineSeconds": "unused"},
     {"AGENT_EXAMPLE_INSTALL_DEADLINE_SEC": "8"}, (8, 300)),
])
def test_native_watchdog_budget(
    tmp_path: Path, extension: str, manifest: dict, environment: dict, expected: tuple,
) -> None:
    (tmp_path / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    env = _env(tmp_path)
    env.pop("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC", None)
    env.update(environment)
    result = _native_watchdog(tmp_path, extension, {
        "ps1": (
            "$b = Get-InstallWatchdogBudget -Payload $args[0] -Name 'agent-example'\n"
            '"$($b.BuildSec) $($b.CutoverSec)"'
        ),
        "sh": 'get_install_watchdog_budget "$1" agent-example',
    }, [tmp_path.as_posix()], env)
    assert result.returncode == 0, result.stderr
    assert tuple(map(int, result.stdout.split())) == expected


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("name", ["agent-bridge", "agent-dispatch"])
@pytest.mark.parametrize("override", [None, "7200", "0"])
def test_watchdog_update_marker_covers_the_effective_outer_window(
    tmp_path, extension, name, override,
):
    payload = ROOT / "plugins" / name
    text = (payload / "scripts" / f"install.{extension}").read_text("utf-8")
    variable = "$UpdateMarkerTtlDefault" if extension == "ps1" else "UPDATE_MARKER_TTL_DEFAULT"
    assignment = next(line for line in text.splitlines() if line.startswith(variable + (" =" if extension == "ps1" else "=")))
    env = _env(tmp_path)
    env.pop("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC", None)
    if override is not None:
        env["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = override
    manifest = json.loads((payload / "plugin.json").read_text("utf-8"))
    build = int(override) if override is not None else manifest.get("installerDeadlineSeconds", 480)
    minimum = (build if build > 0 else 480) + manifest.get("cutoverDeadlineSeconds", 300) + 30
    result = _native_watchdog(tmp_path, extension, {
        "ps1": "$PluginDir=$args[0]\n" + assignment + "\n$UpdateMarkerTtlDefault",
        "sh": 'PLUGIN_DIR="$1"\n' + assignment + '\nprintf "%s\\n" "$UPDATE_MARKER_TTL_DEFAULT"',
    }, [payload.as_posix()], env)
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) >= max(1200, minimum)


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("name", ["agent-bridge", "agent-dispatch"])
def test_watchdog_marker_joiner_extends_but_never_shortens_expiry(tmp_path, extension, name):
    source = (ROOT / "plugins" / name / "scripts" / f"install.{extension}").read_text("utf-8")
    if extension == "ps1":
        functions = []
        for function in ("Get-UpdateMarkerMutex", "Write-UpdateMarker", "Clear-UpdateMarker"):
            start = source.index(f"function {function} {{")
            functions.append(source[start:source.index("\n}\n", start) + 3])
        body = (
            "$UpdateMarker=Join-Path $args[0] 'update-in-progress'\n"
            "$script:UpdateMarkerHeld=$false\n" + "\n".join(functions) + "\n"
            "Write-UpdateMarker -TtlSeconds 5\n$first=Get-Content $UpdateMarker\n"
            "$script:UpdateMarkerHeld=$false\nWrite-UpdateMarker -TtlSeconds 3600\n"
            "$second=Get-Content $UpdateMarker\n"
            "$script:UpdateMarkerHeld=$false\nWrite-UpdateMarker -TtlSeconds 1\n"
            "$third=Get-Content $UpdateMarker\nClear-UpdateMarker\n"
            "$script:UpdateMarkerHeld=$true\nClear-UpdateMarker\n"
            "$script:UpdateMarkerHeld=$true\nClear-UpdateMarker\n"
            '"$first $second $third $(Test-Path $UpdateMarker)"'
        )
    else:
        functions = []
        for function in ("_write_update_marker", "_clear_update_marker"):
            match = re.search(rf"^{function}\(\) \{{.*?^\}}$", source, re.M | re.S)
            assert match
            functions.append(match.group())
        body = (
            'UPDATE_MARKER="$1/update-in-progress"\n'
            'UPDATE_MARKER_REFCOUNT="$UPDATE_MARKER.refcount"\n'
            'UPDATE_MARKER_REFCOUNT_LOCK="$UPDATE_MARKER.refcount.lock"\n'
            "_UPDATE_MARKER_HELD=false\n" + "\n".join(functions) + "\n"
            # Git Bash checks the expiry logic only; native Linux owns flock proof.
            + ("flock() { :; }\n" if os.name == "nt" else "")
            + '_write_update_marker 5\nfirst="$(cat "$UPDATE_MARKER")"\n'
            '_UPDATE_MARKER_HELD=false\n_write_update_marker 3600\nsecond="$(cat "$UPDATE_MARKER")"\n'
            '_UPDATE_MARKER_HELD=false\n_write_update_marker 1\nthird="$(cat "$UPDATE_MARKER")"\n'
            '_clear_update_marker\n_UPDATE_MARKER_HELD=true\n_clear_update_marker\n'
            '_UPDATE_MARKER_HELD=true\n_clear_update_marker\n'
            'printf "%s %s %s " "$first" "$second" "$third"\n'
            'if [[ -e "$UPDATE_MARKER" ]]; then echo True; else echo False; fi'
        )
    result = _native_watchdog(tmp_path, extension, {extension: body}, [tmp_path.as_posix()])
    assert result.returncode == 0, result.stdout + result.stderr
    first, second, third, remains = result.stdout.split()
    assert int(second) >= int(first) + 3500, "Joining long holder did not extend the expiry"
    assert int(third) >= int(second)
    assert remains == "False"


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"), reason="Windows PowerShell required")
@pytest.mark.parametrize("name", ["agent-bridge", "agent-dispatch"])
def test_watchdog_marker_policy_supports_windows_powershell(tmp_path, monkeypatch, name):
    monkeypatch.setitem(globals(), "PWSH", shutil.which("powershell"))
    test_watchdog_update_marker_covers_the_effective_outer_window(tmp_path, "ps1", name, None)
    test_watchdog_marker_joiner_extends_but_never_shortens_expiry(tmp_path, "ps1", name)


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("name", sorted(
    path.parents[1].name for path in ROOT.glob("plugins/*/scripts/bootstrap-check.sh")
    if "# --- Good boot-citizen guard" in path.read_text("utf-8")
))
@pytest.mark.parametrize("age", [601, 1800])
def test_watchdog_bootstrap_never_reaps_a_live_reconcile_from_age(
    tmp_path, extension, name, age,
):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    lock = runtime / "reconcile.lock"
    lock.write_text("12345", encoding="utf-8")
    os.utime(lock, (time.time() - age, time.time() - age))
    birth = "2001-01-01T00:00:00Z"
    (runtime / "reconcile-status.json").write_text(json.dumps({
        "launched_pid": 12345, "worker_started_at": birth,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age)),
    }), encoding="utf-8")
    signal_receipt = tmp_path / "signalled"
    source = (ROOT / "plugins" / name / "scripts" / f"bootstrap-check.{extension}").read_text("utf-8")
    start = source.index("# --- Good boot-citizen guard")
    label = "[agent-ssh]" if name == "agent-ssh" else "[$name]"
    if extension == "ps1":
        end = source.index(f'    [Console]::Error.WriteLine("{label} runtime', start)
        body = (
            f"$InstallDir='{runtime}'\n"
            f"$statusFile=Join-Path $InstallDir 'reconcile-status.json'; $name='{name}'\n"
            "$lockFile=Join-Path $InstallDir 'reconcile.lock'\n"
            f"function Get-Process {{ param($Id) return @{{Id=$Id; StartTime=[datetime]::Parse('{birth}'); Handle=1; HasExited=$false}} }}\n"
            f"function Stop-Process {{ [IO.File]::WriteAllText('{signal_receipt}', 'signal') }}\n"
            f"function taskkill.exe {{ [IO.File]::WriteAllText('{signal_receipt}', 'signal'); $global:LASTEXITCODE=0 }}\n"
            "function Exit-SessionStart { exit 0 }\n"
            + source[start:end] + "\n'relaunch'\n"
        )
    else:
        end = source.index(f'echo "{label} runtime', start)
        body = (
            f'InstallDir="{runtime.as_posix()}"\n'
            f'py="{Path(sys.executable).as_posix()}"\nstatus_file="$InstallDir/reconcile-status.json"\n'
            'kill() { if [[ "$1" == -0 ]]; then return 0; fi; '
            f'printf signal >"{signal_receipt.as_posix()}"; }}\n'
            + source[start:end] + '\nprintf "relaunch\\n"\n'
        )
    result = _native_watchdog(tmp_path, extension, {extension: body}, [])
    assert result.returncode == 0, result.stderr
    assert not signal_receipt.exists(), "Age alone authorized a signal to a live process"
    assert "relaunch" not in result.stdout
    assert lock.read_text("utf-8") == "12345"


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("name,minimum", [
    ("agent-dispatch", 450 + 420 + 60 + 300),
    ("agent-index", 420 + 60 + 300),
    ("agent-mcp", 420 + 60 + 300),
    ("agent-worktrees", 300 + 420 + 60 + 300),
    ("agent-bridge", 540 + 180 + 300),
])
def test_native_watchdog_declared_cutover_covers_lifecycle_near_build_deadline(
    tmp_path: Path, extension: str, name: str, minimum: int,
) -> None:
    payload = ROOT / "plugins" / name
    manifest = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))
    build = manifest.get("installerDeadlineSeconds", 480)
    cutover = manifest.get("cutoverDeadlineSeconds", 300)
    env = _env(tmp_path)
    env.pop("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC", None)
    phase = tmp_path / "phase.json"
    entered = 100 + build - 1
    phase.write_text(json.dumps({
        "phase": "cutover", "extend_until": entered + cutover,
    }), encoding="utf-8")
    result = _native_watchdog(tmp_path, extension, {
        "ps1": (
            "$b = Get-InstallWatchdogBudget -Payload $args[0] -Name $args[1]\n"
            "$s = Get-InstallWatchdogDeadline -BuildSec $b.BuildSec "
            "-CutoverSec $b.CutoverSec -StartedAt 100 -PhaseFile $args[2]\n"
            '"$($b.BuildSec) $($b.CutoverSec) $($s.Deadline) $($s.Phase)"'
        ),
        "sh": (
            'read -r build cutover < <(get_install_watchdog_budget "$1" "$2")\n'
            'printf "%s %s " "$build" "$cutover"\n'
            'get_install_watchdog_deadline "$build" "$cutover" 100 "$3"'
        ),
    }, [payload.as_posix(), name, phase.as_posix()], env)
    assert result.returncode == 0, result.stderr
    actual_build, actual_cutover, deadline, state = result.stdout.split()
    assert (int(actual_build), int(actual_cutover), state) == (build, cutover, "cutover")
    assert int(deadline) - entered >= minimum
    assert int(deadline) <= 100 + build + cutover


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("raw", ["2147483648", "-2147483649", "18446744073709551616"])
def test_native_watchdog_rejects_deadline_overflow(tmp_path: Path, extension: str, raw: str) -> None:
    (tmp_path / "plugin.json").write_text("{}", encoding="utf-8")
    env = _env(tmp_path)
    env["AGENT_EXAMPLE_INSTALL_DEADLINE_SEC"] = raw
    result = _native_watchdog(tmp_path, extension, {
        "ps1": "Get-InstallWatchdogBudget -Payload $args[0] -Name 'agent-example'",
        "sh": 'get_install_watchdog_budget "$1" agent-example',
    }, [tmp_path.as_posix()], env)
    assert result.returncode != 0
    assert "32-bit integer" in result.stderr


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("field,value", [
    ("installerDeadlineSeconds", 0),
    ("installerDeadlineSeconds", "12"),
    ("cutoverDeadlineSeconds", -1),
    ("cutoverDeadlineSeconds", True),
    ("cutoverDeadlineSeconds", 1.5),
    ("cutoverDeadlineSeconds", 18446744073709551616),
])
def test_native_watchdog_rejects_invalid_manifest_budget(
    tmp_path: Path, extension: str, field: str, value: object,
) -> None:
    (tmp_path / "plugin.json").write_text(json.dumps({field: value}), encoding="utf-8")
    env = _env(tmp_path)
    env.pop("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC", None)
    result = _native_watchdog(tmp_path, extension, {
        "ps1": "Get-InstallWatchdogBudget -Payload $args[0] -Name 'agent-example'",
        "sh": 'get_install_watchdog_budget "$1" agent-example',
    }, [tmp_path.as_posix()], env)
    assert result.returncode != 0
    assert field in result.stderr


@pytest.mark.parametrize("extension", ["ps1", "sh"])
@pytest.mark.parametrize("receipt,expected", [
    (None, (110, "build")),
    ({"phase": "cutover", "extend_until": 105}, (110, "cutover")),
    ({"phase": "cutover", "extend_until": 120}, (120, "cutover")),
    ({"phase": "cutover", "extend_until": 999}, (130, "cutover")),
    ({"phase": "cutover", "extend_until": "999"}, (110, "invalid")),
    ({"phase": "unknown", "extend_until": 999}, (110, "invalid")),
    ("{partial", (110, "invalid")),
])
def test_native_watchdog_caps_phase_extension(
    tmp_path: Path, extension: str, receipt: object, expected: tuple,
) -> None:
    phase = tmp_path / "phase.json"
    if receipt is not None:
        phase.write_text(
            receipt if isinstance(receipt, str) else json.dumps(receipt), encoding="utf-8",
        )
    result = _native_watchdog(tmp_path, extension, {
        "ps1": (
            "$s = Get-InstallWatchdogDeadline -BuildSec 10 -CutoverSec 20 -StartedAt 100 "
            '-PhaseFile $args[0]\n"$($s.Deadline) $($s.Phase)"'
        ),
        "sh": 'get_install_watchdog_deadline 10 20 100 "$1"',
    }, [phase.as_posix()])
    assert result.returncode == 0, result.stderr
    deadline, state = result.stdout.split()
    assert (int(deadline), state) == expected


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_watchdog_native_phase_writer_publishes_an_atomic_receipt(tmp_path: Path, extension: str) -> None:
    phase = tmp_path / "phase.json"
    env = _env(tmp_path)
    env.update({
        "COPILOT_PLUGIN_INSTALL_PHASE_FILE": phase.as_posix(),
        "COPILOT_PLUGIN_INSTALL_CUTOVER_BUDGET_SEC": "17",
    })
    before = time.time()
    result = _native_watchdog(tmp_path, extension, {
        "ps1": "Enter-InstallPhase -Name cutover\nEnter-InstallPhase -Name cutover",
        "sh": "enter_install_phase cutover\nenter_install_phase cutover",
    }, [], env)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(phase.read_text(encoding="utf-8"))
    assert receipt["phase"] == "cutover"
    assert before + 16 <= receipt["extend_until"] <= time.time() + 17
    assert list(tmp_path.glob("phase.json.*.tmp")) == []


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"), reason="Windows PowerShell 5.1")
def test_native_watchdog_supports_windows_powershell(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setitem(globals(), "PWSH", shutil.which("powershell"))
    (tmp_path / "plugin.json").write_text('{"cutoverDeadlineSeconds":7}', encoding="utf-8")
    phase = tmp_path / "phase.json"
    env = _env(tmp_path)
    env.update({
        "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC": "8",
        "COPILOT_PLUGIN_INSTALL_PHASE_FILE": phase.as_posix(),
        "COPILOT_PLUGIN_INSTALL_CUTOVER_BUDGET_SEC": "7",
    })
    result = _native_watchdog(tmp_path, "ps1", {
        "ps1": (
            "$b = Get-InstallWatchdogBudget -Payload $args[0] -Name 'agent-example'\n"
            "Enter-InstallPhase -Name cutover\nEnter-InstallPhase -Name cutover\n"
            '"$($b.BuildSec) $($b.CutoverSec)"'
        ),
    }, [tmp_path.as_posix()], env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "8 7"
    assert json.loads(phase.read_text(encoding="utf-8"))["phase"] == "cutover"
    assert not list(tmp_path.glob("phase.json.*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows reader sharing modes")
def test_phase_receipt_replacement_survives_a_held_watchdog_reader(tmp_path: Path) -> None:
    phase = tmp_path / "phase.json"
    env = _env(tmp_path)
    env["COPILOT_PLUGIN_INSTALL_PHASE_FILE"] = phase.as_posix()
    before = time.time()
    result = _native_watchdog(tmp_path, "ps1", {
        "ps1": (
            "Enter-InstallPhase -Name cutover -BudgetSec 10\n"
            "$original = [IO.File]::ReadAllText($env:COPILOT_PLUGIN_INSTALL_PHASE_FILE)\n"
            "$stream = Open-InstallPhaseReceipt -Path $env:COPILOT_PLUGIN_INSTALL_PHASE_FILE\n"
            "try {\n"
            "  Enter-InstallPhase -Name cutover -BudgetSec 20\n"
            "  $reader = [IO.StreamReader]::new($stream)\n"
            "  try { if ($reader.ReadToEnd() -cne $original) { throw 'Reader lost its snapshot' } }\n"
            "  finally { $reader.Dispose() }\n"
            "} finally { $stream.Dispose() }\n"
        ),
    }, [], env)
    assert result.returncode == 0, result.stderr
    assert json.loads(phase.read_text(encoding="utf-8"))["extend_until"] >= before + 19
    assert not list(tmp_path.glob("phase.json.*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows native process handles")
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_watchdog_refuses_pid_actions_after_child_exits_during_deadline_check(
    tmp_path: Path, monkeypatch, shell: str,
) -> None:
    interpreter = shutil.which(shell)
    if not interpreter:
        pytest.skip(f"{shell} unavailable")
    monkeypatch.setitem(globals(), "PWSH", interpreter)
    result = _native_watchdog(tmp_path, "ps1", {
        "ps1": (
            "$start = [Diagnostics.ProcessStartInfo]::new()\n"
            "$start.FileName = (Get-Process -Id $PID).Path\n"
            "$start.Arguments = '-NoProfile -NonInteractive -Command \"Start-Sleep -Milliseconds 200\"'\n"
            "$start.UseShellExecute = $false\n$start.CreateNoWindow = $true\n"
            "$child = [Diagnostics.Process]::Start($start)\n"
            "$script:pidActions = 0\n"
            "function taskkill.exe { $script:pidActions++; throw 'PID kill after exit' }\n"
            "function Stop-Process { param($Id, [switch]$Force, $ErrorAction) $script:pidActions++ }\n"
            "function Get-InstallWatchdogDeadline {\n"
            "  param($BuildSec, $CutoverSec, $StartedAt, $PhaseFile)\n"
            "  $child.WaitForExit()\n"
            "  return [pscustomobject]@{ Deadline = 1L; Phase = 'build' }\n"
            "}\n"
            "try {\n"
            "  $budget = [pscustomobject]@{ BuildSec = 1L; CutoverSec = 1L }\n"
            "  $code = Wait-InstallWatchdog -Child $child -Budget $budget -StartedAt 1 "
            "-PhaseFile 'unused' -Name fixture -Stage fixture -LogPath (Join-Path $PWD 'kill.log')\n"
            "  if ($script:pidActions -ne 0) { throw \"Unexpected PID actions: $script:pidActions\" }\n"
            "  if ($code -ne 0) { throw \"Exited child returned $code instead of its own status\" }\n"
            "} finally { if (-not $child.HasExited) { $child.Kill() }; $child.Dispose() }\n"
        ),
    }, [])
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "kill.log").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native process handles")
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("target", ["matching", "wrong-birth", "exited"])
def test_watchdog_tree_termination_requires_live_matching_identity(
    tmp_path: Path, monkeypatch, target: str, shell: str,
) -> None:
    interpreter = shutil.which(shell)
    if not interpreter:
        pytest.skip(f"{shell} unavailable")
    monkeypatch.setitem(globals(), "PWSH", interpreter)
    result = _native_watchdog(tmp_path, "ps1", {
        "ps1": (
            "$start = [Diagnostics.ProcessStartInfo]::new()\n"
            "$start.FileName = (Get-Process -Id $PID).Path\n"
            "$start.Arguments = '-NoProfile -NonInteractive -Command \"Start-Sleep -Seconds 30\"'\n"
            "$start.UseShellExecute = $false\n$start.CreateNoWindow = $true\n"
            "$child = [Diagnostics.Process]::Start($start)\n"
            "$expected = $child.StartTime.ToUniversalTime().Ticks\n"
            "$mode = $args[0]\n"
            "try {\n"
            "  if ($mode -eq 'wrong-birth') {\n"
            "    $refused = $false\n"
            "    try { Stop-InstallWatchdogTree -Child $child -ExpectedStartTimeTicks ($expected + 1) }\n"
            "    catch { $refused = $_.Exception.Message -match 'identity mismatch' }\n"
            "    if (-not $refused -or $child.HasExited) { throw 'Wrong identity was not refused safely' }\n"
            "  } else {\n"
            "    if ($mode -eq 'exited') { $child.Kill(); $child.WaitForExit() }\n"
            "    $terminated = Stop-InstallWatchdogTree -Child $child -ExpectedStartTimeTicks $expected\n"
            "    if ($mode -eq 'exited' -and $terminated) { throw 'Exited target was reported killed' }\n"
            "    if ($mode -eq 'matching' -and -not $terminated) { throw 'Matching target was not killed' }\n"
            "    if (-not $child.WaitForExit(3000)) { throw 'Owned target survived termination' }\n"
            "  }\n"
            "} finally { if (-not $child.HasExited) { $child.Kill() }; $child.Dispose() }\n"
        ),
    }, [target])
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("extension", ["ps1", "sh"])
def test_unavailable_home_refuses_in_place_install(tmp_path: Path, extension: str) -> None:
    if (extension == "ps1" and not PWSH) or (extension == "sh" and not BASH):
        pytest.skip(f"{extension} interpreter unavailable")
    payload, entry = _entry(tmp_path, extension)
    home = tmp_path / "not-a-directory"
    home.write_text("file", encoding="utf-8")
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    environment = _env(home)
    if extension == "ps1":
        # PowerShell on POSIX needs a real HOME just to initialize. Its installer
        # relocates using USERPROFILE, which remains the invalid target here.
        environment["HOME"] = str(tmp_path)
    proc = subprocess.run(
        _command(entry, extension), cwd=payload, env=environment,
        capture_output=True, text=True, timeout=20, **kwargs,
    )
    assert proc.returncode != 0
    assert "self-stage failed" in proc.stderr
    assert "running in place" not in proc.stdout + proc.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell smoke seam")
@pytest.mark.parametrize("failure", ["invalid-phase", "receipt-publication"])
def test_watchdog_powershell_smoke_phase_failure_cannot_report_success(tmp_path: Path, failure: str) -> None:
    payload, entry = _entry(tmp_path, "ps1")
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    env["COPILOT_PLUGIN_INSTALL_SMOKE_PHASE"] = (
        "invalid" if failure == "invalid-phase" else "cutover"
    )
    if failure == "receipt-publication":
        helper = payload / "scripts" / "installer-watchdog.ps1"
        with helper.open("a", encoding="utf-8") as stream:
            stream.write(
                "\nfunction Enter-InstallPhase { throw 'injected receipt publication failure' }\n"
            )
    result = subprocess.run(
        _command(entry, "ps1"), cwd=home, env=env, capture_output=True,
        text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Smoke seam failed:" in result.stderr


def test_all_reconcile_launchers_choose_home() -> None:
    for script in (ROOT / "plugins").glob("*/scripts/bootstrap-check.ps1"):
        text = script.read_text(encoding="utf-8").replace("`\n", " ")
        for line in text.splitlines():
            if "Start-Process -FilePath 'conhost.exe'" in line:
                assert "-WorkingDirectory $env:USERPROFILE" in line, script
    for script in (ROOT / "plugins").glob("*/scripts/bootstrap-check.sh"):
        text = script.read_text(encoding="utf-8")
        for line in text.splitlines():
            if re.search(r"^\s*(?:\(.*)?nohup bash ", line):
                assert line.lstrip().startswith("nohup bash "), script
                before = text[:text.index(line)].rstrip().splitlines()[-1]
                assert before.lstrip().startswith('cd "$HOME" || '), script


@pytest.mark.skipif(os.name == "nt" or not BASH, reason="native POSIX signal contract")
def test_reconcile_launch_survives_hup_with_home_cwd(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    receipt = home / "ready"
    completed = home / "completed"
    worker = home / "worker.sh"
    worker.write_text(
        'printf "%s\\n%s\\n" "$BASHPID" "$PWD" >"$HOME/ready"\n'
        'sleep 1\nprintf "done" >"$HOME/completed"\n', encoding="utf-8",
    )
    text = (ROOT / "plugins" / "agent-codespaces" / "scripts" / "bootstrap-check.sh").read_text()
    launch_start = text.index('cd "$HOME" || { echo "reconcile launch failed:')
    launch_end = text.index("\n", text.index("nohup bash ", launch_start))
    launch = text[launch_start:launch_end]
    parent = subprocess.Popen(
        [BASH, "-c", 'target=("$1")\n' + launch + "\nwait", "bash", str(worker)],
        cwd=payload, env={**os.environ, "HOME": str(home)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not receipt.exists() and parent.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert receipt.exists()
        worker_pid, cwd = receipt.read_text().splitlines()
        assert Path(cwd).resolve() == home.resolve()
        assert int(worker_pid) != parent.pid
        payload.rename(tmp_path / "replaced-payload")
        payload.mkdir()
        os.killpg(parent.pid, signal.SIGHUP)
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while not completed.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert completed.read_text() == "done"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if not completed.exists():
            try:
                os.killpg(parent.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(os.name != "nt" or not BASH, reason="MSYS Windows launch contract")
def test_windows_bash_refuses_payload_launch_cwd(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    payload, entry = _entry(tmp_path, "sh")
    proc = subprocess.run(
        _command(entry, "sh"), cwd=payload, env=_env(home),
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert proc.returncode != 0
    assert "launch from HOME or use install.ps1" in proc.stderr
    assert not (home / ".agent-example" / "smoke.json").exists()
