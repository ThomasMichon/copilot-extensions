"""Exercise the shipped file-based pane argv consumer with PowerShell."""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "bin" / "pane-wrapper.ps1"
LAUNCHER = WRAPPER.with_name("pane-launch.ps1")
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell 7 is required")


def _test_env(tmp_path: Path) -> dict[str, str]:
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE"}
    env = {key: value for key, value in os.environ.items() if key.upper() in keep}
    for key, name in {
        "APPDATA": "roaming", "LOCALAPPDATA": "local", "AGENT_HOME": "profile",
        "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state", "XDG_CACHE_HOME": "cache",
        "WORKTREE_MANAGER_ROOT": "manager",
    }.items():
        path = tmp_path / name
        path.mkdir(exist_ok=True)
        env[key] = str(path)
    (tmp_path / "profile").mkdir(exist_ok=True)
    env.update(
        USERPROFILE=str(tmp_path / "profile"),
        HOME=str(tmp_path / "profile"), WORKTREE_PANE_MIN_RUNTIME="0",
        TEMP=str(tmp_path), TMP=str(tmp_path),
        PSMUX_NO_WARM="1", PSMUX_DATA_DIR=str(tmp_path / "psmux-data"),
        PSMUX_CONFIG_FILE=os.devnull,
    )
    return env


@pytest.mark.parametrize("key", ["TMUX", "TMUX_PANE", "PSMUX_SESSION", "PSMUX_TARGET_SESSION"])
def test_native_pane_fixture_drops_parent_mux_session(tmp_path, monkeypatch, key):
    monkeypatch.setenv(key, "unrelated-parent-pane")
    env = _test_env(tmp_path)
    assert key not in env
    assert env["PSMUX_DATA_DIR"] == str(tmp_path / "psmux-data")
    assert os.environ[key] == "unrelated-parent-pane"


def _powershell_producer(
    tmp_path: Path, wrapper: Path, argv: list[str], *, timeout: float = 15,
) -> list[str]:
    source = (WRAPPER.parent / "launch-session.ps1").read_text("utf-8")
    start = source.index("                    $paneArgsRoot = Join-Path")
    end = source.index("\n                }\n                $savedAuth", start)
    payload = tmp_path / "producer-input.json"
    payload.write_text(json.dumps(argv), encoding="utf-8")
    script = tmp_path / "produce.ps1"
    script.write_text(
        "$paneWrapper=$args[0]\n"
        "$RuntimeDir=$args[2]\n"
        "$paneLaunch=Join-Path (Split-Path -Parent $paneWrapper) 'pane-launch.ps1'\n"
        "$inputJson=[System.Text.Json.JsonDocument]::Parse([IO.File]::ReadAllText($args[1]))\n"
        "$wrapperArgs=@($inputJson.RootElement.EnumerateArray() | ForEach-Object { $_.GetString() })\n"
        "$inputJson.Dispose()\n"
        + source[start:end]
        + "\nConvertTo-Json -InputObject @($paneCmd) -Compress -EscapeHandling EscapeNonAscii\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(wrapper), str(payload),
         str(tmp_path / "runtime")],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout, env=_test_env(tmp_path),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _run(manifest: Path, env: dict[str, str], *, receipt: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PWSH, "-NoProfile", "-NoLogo", "-File", str(LAUNCHER),
         "-Manifest", str(manifest), *(["-Receipt", str(receipt)] if receipt else [])],
        capture_output=True, text=True, encoding="utf-8", timeout=15, env=env,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )


def test_manifest_round_trip_and_fresh_retry(tmp_path):
    child = tmp_path / "child with spaces.ps1"
    child.write_text(
        "[IO.File]::WriteAllText($args[0], "
        "(ConvertTo-Json -InputObject @($args[1..($args.Count-1)]) -Compress))\n",
        encoding="utf-8",
    )
    expected = ["", "two words", 'a"quote', "a'quote", "trailing\\",
                "$literal; & text", "--allow-all", "\u03bb", "2026-10-08T12:00:00Z"]
    env = _test_env(tmp_path)
    for attempt in range(2):
        output = tmp_path / f"captured-{attempt}.json"
        command = _powershell_producer(
            tmp_path, WRAPPER,
            [PWSH, "-NoProfile", "-File", str(child), str(output), *expected],
        )
        manifest = Path(command[-1][1:-1].replace("''", "'"))
        receipt = Path(str(manifest) + ".receipt.json")
        try:
            result = _run(manifest, env, receipt=receipt)
            assert result.returncode == 0, result.stderr
            assert json.loads(output.read_text("utf-8")) == expected
            assert not manifest.exists()
            acknowledged = json.loads(receipt.read_text("utf-8"))
            assert acknowledged["manifest"] == manifest.name
            assert acknowledged["status"] == "consumed"
            assert acknowledged["pid"] > 0
            assert set(acknowledged) == {"version", "manifest", "status", "stage", "pid"}
        finally:
            manifest.unlink(missing_ok=True)
            receipt.unlink(missing_ok=True)


def _run_launch_plan_parser(tmp_path, plan_json, shell):
    source = (WRAPPER.parent / "launch-session.ps1").read_text("utf-8")
    start = source.index('$plan = ($jsonOutput -join "`n")')
    end = source.index("\nif ($plan.action -eq 'exec') {", start)
    payload = tmp_path / "plan.json"
    payload.write_text(plan_json, encoding="utf-8")
    script = tmp_path / "plan-argv.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$PSModuleAutoLoadingPreference='None'\n"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n"
        "Import-Module ([IO.Path]::Combine($PSHOME, 'Modules', "
        "'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1')) "
        "-ErrorAction Stop\n"
        "$jsonOutput=@([IO.File]::ReadAllText($args[0]))\n"
        + source[start:end]
        + "\nConvertTo-Json -InputObject @($plan.cmd) -Compress\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [shell, "-NoProfile", "-File", str(script), str(payload)],
        capture_output=True, text=True, encoding="utf-8", timeout=15, env=_test_env(tmp_path),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("shell", [PWSH, shutil.which("powershell")])
def test_launch_plan_preserves_string_argv_before_manifest_creation(tmp_path, nested, shell):
    if not shell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    expected = [
        "program", "", "2026-10-08T12:00:00+05:30", "/Date(0)/", "--allow-all",
        "two words", 'a"quote', "a'quote", "trailing\\", "\u03bb", "line1\r\nline2", "\t",
    ]
    plan = {"action": "exec", "cmd": expected}
    result = _run_launch_plan_parser(
        tmp_path, json.dumps({"launch": plan} if nested else plan), shell,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("shell", [PWSH, shutil.which("powershell")])
@pytest.mark.parametrize("command,message", [
    ("program", "Launch command must be a string argv array."),
    (42, "Launch command must be a string argv array."),
    (["program", None], "Launch command contains a non-string argument."),
    (["program", 42], "Launch command contains a non-string argument."),
])
def test_launch_plan_parser_rejects_non_string_argv(tmp_path, nested, shell, command, message):
    if not shell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    plan = {"action": "exec", "cmd": command}
    result = _run_launch_plan_parser(
        tmp_path, json.dumps({"launch": plan} if nested else plan), shell,
    )
    assert result.returncode != 0
    assert message in result.stderr


@pytest.mark.parametrize("plan_json", [
    '{"action":"exec","cmd":["first"],"cmd":["last"]}',
    '{"launch":{"action":"exec","cmd":["first"]},"launch":{"action":"exec","cmd":["last"]}}',
])
def test_launch_plan_parser_preserves_last_duplicate_property(tmp_path, plan_json):
    result = _run_launch_plan_parser(tmp_path, plan_json, PWSH)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["last"]


@pytest.mark.parametrize("content", ['{}', '[]', 'null', '["ok",null]', '[1]', '{'])
def test_bad_manifest_fails_before_child_execution(tmp_path, content):
    manifest = tmp_path / "bad.json"
    manifest.write_text(content, encoding="utf-8")
    result = _run(manifest, _test_env(tmp_path))
    assert result.returncode == 3
    assert "Could not consume pane argument handoff" in result.stderr


def test_missing_manifest_fails_loudly(tmp_path):
    result = _run(tmp_path / "missing.json", _test_env(tmp_path))
    assert result.returncode == 3
    assert "Could not consume pane argument handoff" in result.stderr


def test_missing_manifest_publishes_a_sanitized_rejection_receipt(tmp_path):
    manifest = tmp_path / "missing.json"
    receipt = Path(str(manifest) + ".receipt.json")
    result = _run(manifest, _test_env(tmp_path), receipt=receipt)
    assert result.returncode == 3
    acknowledged = json.loads(receipt.read_text("utf-8"))
    assert acknowledged == {
        "version": 1, "manifest": manifest.name, "status": "rejected",
        "stage": "manifest-open", "pid": acknowledged["pid"],
    }
    assert acknowledged["pid"] > 0
    assert str(tmp_path) not in result.stderr + receipt.read_text("utf-8")


@pytest.mark.parametrize("content,stage", [
    ("{", "manifest-json"),
    ('{"version":1,"wrapper":"missing","argv":["private-argument"]}', "wrapper-path"),
])
def test_rejected_manifest_receipt_is_sanitized(tmp_path, content, stage):
    manifest = tmp_path / "rejected.json"
    receipt = Path(str(manifest) + ".receipt.json")
    manifest.write_text(content, encoding="utf-8")
    result = _run(manifest, _test_env(tmp_path), receipt=receipt)
    assert result.returncode == 3
    acknowledged = json.loads(receipt.read_text("utf-8"))
    assert acknowledged["status"] == "rejected"
    assert acknowledged["stage"] == stage
    assert "private-argument" not in receipt.read_text("utf-8") + result.stderr
    assert not manifest.exists()


def test_foreign_receipt_path_is_rejected_before_consumption(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    receipt = tmp_path / "unrelated.json"
    receipt.write_text("retain")
    result = _run(manifest, _test_env(tmp_path), receipt=receipt)
    assert result.returncode == 3
    assert receipt.read_text() == "retain"
    assert manifest.exists()


def _parent_receipt_waiter(tmp_path, *, short_timeout=False):
    source = (WRAPPER.parent / "launch-session.ps1").read_text("utf-8")
    helpers = source[
        source.index("    function Remove-AwPaneArgsFile"):
        source.index("    $maxCreateAttempts")
    ]
    marker = source.index("            # Cold nested PowerShell startup")
    start = source.rfind("        if ($paneArgsFile) {", 0, marker)
    end = source.index("\n        if ($ahpTokenFile) {", marker)
    wait = source[start:end]
    if short_timeout:
        wait = wait.replace(".AddSeconds(30)", ".AddMilliseconds(150)")
    script = tmp_path / "parent-wait.ps1"
    script.write_text(
        "$paneArgsFile=$args[0]\n$paneReceiptFile=\"$paneArgsFile.receipt.json\"\n"
        "$plan=[pscustomobject]@{worktree_id='synthetic-worktree'}\n"
        "$script:LaunchProject='example'\n$ahpTokenFile=$env:TEST_TOKEN\n"
        "if($ahpTokenFile){$tokenLock=[IO.File]::Open($ahpTokenFile,"
        "[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)}\n"
        "function Write-SetupLog($message,$level) { $event=if($level -eq 'WARN')"
        "{'warning'}else{'diagnostic'}; [IO.File]::AppendAllText($env:TEST_LOG,$event+\"`n\") }\n"
        "function Write-AwMuxFailure { [IO.File]::AppendAllText($env:TEST_LOG,\"failure`n\") }\n"
        "function Stop-AwOwnedPsmuxSession { [IO.File]::AppendAllText($env:TEST_LOG,\"stop`n\") }\n"
        + helpers
        + "\n[IO.File]::WriteAllText($env:TEST_READY,'waiting')\n"
        + wait,
        encoding="utf-8",
    )
    return script


@pytest.mark.parametrize("receipt_content", [None, '{"status":"consumed"}'])
def test_parent_requires_valid_receipt_and_logs_before_owned_cleanup(tmp_path, receipt_content):
    manifest = tmp_path / "parent.json"
    # A vanished manifest alone must never be mistaken for acknowledgment.
    if receipt_content:
        Path(str(manifest) + ".receipt.json").write_text(receipt_content)
    log = tmp_path / "diagnostic.log"
    env = dict(_test_env(tmp_path), TEST_LOG=str(log), TEST_READY=str(tmp_path / "ready"))
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(_parent_receipt_waiter(tmp_path, short_timeout=True)),
         str(manifest)],
        capture_output=True, text=True, timeout=15, env=env,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 3
    assert log.read_text().splitlines() == ["diagnostic", "failure", "stop"]
    assert "saved conversation and worktree are preserved" in result.stderr
    assert "--no-mux" in result.stderr
    assert ("invalid" if receipt_content else "not-acknowledged") in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive token handoff cleanup")
def test_locked_token_cleanup_does_not_mask_pane_startup_failure(tmp_path):
    manifest = tmp_path / "parent.json"
    Path(str(manifest) + ".receipt.json").write_text('{"status":"consumed"}')
    token = tmp_path / "protected-token"
    token.write_text("synthetic")
    log = tmp_path / "diagnostic.log"
    env = dict(
        _test_env(tmp_path), TEST_LOG=str(log), TEST_READY=str(tmp_path / "ready"),
        TEST_TOKEN=str(token),
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(_parent_receipt_waiter(tmp_path, short_timeout=True)),
         str(manifest)],
        capture_output=True, text=True, timeout=15, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 3
    assert "Pane argument handoff failed" in result.stderr
    assert log.read_text().splitlines() == ["diagnostic", "failure", "stop", "warning"]
    assert token.exists()


def test_parent_accepts_real_consumer_startup_beyond_old_five_second_window(tmp_path):
    wrapper = tmp_path / "no-op.ps1"
    wrapper.write_text("exit 0\n")
    manifest = tmp_path / "delayed.json"
    manifest.write_text(json.dumps({"version": 1, "wrapper": str(wrapper), "argv": ["payload"]}))
    receipt = Path(str(manifest) + ".receipt.json")
    delayed = tmp_path / "delayed-launch.ps1"
    delayed.write_text(
        LAUNCHER.read_text("utf-8").replace(
            "$stage = 'manifest-open'", "Start-Sleep -Seconds 6\n$stage = 'manifest-open'",
        ),
        encoding="utf-8",
    )
    ready = tmp_path / "waiting"
    log = tmp_path / "diagnostic.log"
    env = dict(_test_env(tmp_path), TEST_LOG=str(log), TEST_READY=str(ready))
    parent = subprocess.Popen(
        [PWSH, "-NoProfile", "-File", str(_parent_receipt_waiter(tmp_path)), str(manifest)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and parent.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        started = time.monotonic()
        consumer = subprocess.run(
            [PWSH, "-NoProfile", "-File", str(delayed), "-Manifest", str(manifest),
             "-Receipt", str(receipt)],
            capture_output=True, text=True, timeout=20, env=env,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        assert consumer.returncode == 0, consumer.stderr
        _stdout, stderr = parent.communicate(timeout=10)
        assert parent.returncode == 0, stderr
        assert time.monotonic() - started > 5
        assert not log.exists(), "healthy delayed startup must not trigger teardown"
        assert not manifest.exists() and not receipt.exists()
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=10)


def test_custom_wrapper_preserves_argv_and_exit_code(tmp_path):
    wrapper = tmp_path / "custom wrapper.ps1"
    output = tmp_path / "custom-args.json"
    wrapper.write_text(
        "[IO.File]::WriteAllText($args[0], "
        "(ConvertTo-Json -InputObject @($args[1..($args.Count-1)]) -Compress))\n"
        "exit 37\n",
        encoding="utf-8",
    )
    expected = ["", "--allow-all", "2026-10-08T12:00:00Z", 'a"quote', "line\nbreak"]
    manifest = tmp_path / "custom.json"
    manifest.write_text(
        json.dumps({"version": 1, "wrapper": str(wrapper), "argv": [str(output), *expected]}),
        encoding="utf-8",
    )
    result = _run(manifest, _test_env(tmp_path))
    assert result.returncode == 37, result.stderr
    assert json.loads(output.read_text("utf-8")) == expected
    assert not manifest.exists()


def test_custom_wrapper_failure_is_not_success_shaped(tmp_path):
    wrapper = tmp_path / "broken.ps1"
    wrapper.write_text("throw 'synthetic failure'\n", encoding="utf-8")
    manifest = tmp_path / "broken.json"
    manifest.write_text(
        json.dumps({"version": 1, "wrapper": str(wrapper), "argv": ["payload"]}),
        encoding="utf-8",
    )
    result = _run(manifest, _test_env(tmp_path))
    assert result.returncode == 3
    assert "Pane wrapper launch failed" in result.stderr
    assert not manifest.exists()


def test_concurrent_manifest_consumers_execute_once(tmp_path):
    wrapper = tmp_path / "once.ps1"
    output = tmp_path / "executions.txt"
    wrapper.write_text("[IO.File]::AppendAllText($args[0], \"once`n\")\n", encoding="utf-8")
    manifest = tmp_path / "once.json"
    manifest.write_text(json.dumps({
        "version": 1, "wrapper": str(wrapper), "argv": [str(output)],
    }), encoding="utf-8")
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    held_launcher = tmp_path / "held-launcher.ps1"
    anchor = "    $handoff = $document.RootElement"
    held_launcher.write_text(LAUNCHER.read_text("utf-8").replace(
        anchor, anchor + "\n[IO.File]::WriteAllText($env:PANE_TEST_READY,'ready')\n"
        "while (-not (Test-Path -LiteralPath $env:PANE_TEST_RELEASE))"
        " { Start-Sleep -Milliseconds 20 }\n",
    ), encoding="utf-8")
    env = dict(_test_env(tmp_path), PANE_TEST_READY=str(ready), PANE_TEST_RELEASE=str(release))
    first = subprocess.Popen(
        [PWSH, "-NoProfile", "-File", str(held_launcher), "-Manifest", str(manifest)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        second = _run(manifest, env)
        assert second.returncode == 3, second.stderr
        assert not output.exists()
        release.write_text("go")
        _stdout, stderr = first.communicate(timeout=10)
        assert first.returncode == 0, stderr
        assert output.read_text("utf-8").splitlines() == ["once"]
        assert not manifest.exists()
    finally:
        release.write_text("go")
        if first.poll() is None:
            first.kill()
        first.communicate(timeout=10)


@pytest.mark.parametrize("argv", [[], None, [1], ["ok", None], "not an array"])
def test_invalid_manifest_argv_is_rejected(tmp_path, argv):
    manifest = tmp_path / "invalid-argv.json"
    manifest.write_text(
        json.dumps({"version": 1, "wrapper": str(WRAPPER), "argv": argv}), encoding="utf-8",
    )
    result = _run(manifest, _test_env(tmp_path))
    assert result.returncode == 3
    assert "Could not consume pane argument handoff" in result.stderr


def test_launcher_retry_regenerates_consumed_manifest(tmp_path):
    source = (WRAPPER.parent / "launch-session.ps1").read_text("utf-8")
    helper_start = source.index("function Invoke-AwPsmuxFromHome {")
    helper_end = source.index("\n}\n", helper_start) + 3
    start = source.index("    $savedPsmuxSession = ")
    end = source.index("    if ($newSessionExit -ne 0) {", start)
    fake = tmp_path / "fake-psmux.ps1"
    fake.write_text(
        "$index=[array]::IndexOf($args,'-Manifest')\n"
        "$path=([string]$args[$index+1]).Substring(1,([string]$args[$index+1]).Length-2)"
        ".Replace(\"''\",\"'\")\n"
        "if (-not (Test-Path -LiteralPath $path)) { exit 2 }\n"
        "[IO.File]::AppendAllText($env:TEST_LOG,$path+\"`n\")\n"
        "[IO.File]::Delete($path)\n"
        "if (@(Get-Content -LiteralPath $env:TEST_LOG).Count -eq 1) { exit 1 }\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script = tmp_path / "retry.ps1"
    script.write_text(
        "$script:AwPsmuxBin=$args[0]\n$paneWrapper=$args[1]\n"
        "$RuntimeDir=$args[2]\n"
        "$paneLaunch=Join-Path (Split-Path -Parent $paneWrapper) 'pane-launch.ps1'\n"
        "$plan=[pscustomobject]@{work_dir=$args[2]}\n"
        "$sessName='fixture-session'\n"
        "$wrapperArgs=@('payload','two words')\n$ahpArgs=@()\n$envFlags=@()\n"
        "function Assert-AwColdResume {}\n"
        "function Write-SetupLog {}\nfunction Write-SetupStatus {}\n"
        "$script:stops=0\nfunction Stop-AwOwnedPsmuxSession { $script:stops++ }\n"
        "function Read-AwMuxRetryChoice { $false }\n"
        + source[helper_start:helper_end] + "\n"
        + source[start:end]
        + "\n[pscustomobject]@{attempts=$totalCreateAttempts; stops=$script:stops;"
        " code=$newSessionExit; error=$newSessionError} | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    log = tmp_path / "attempts.txt"
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(fake), str(WRAPPER), str(tmp_path)],
        capture_output=True, text=True, timeout=15,
        env=dict(_test_env(tmp_path), TEST_LOG=str(log)),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "attempts": 2, "stops": 1, "code": 0, "error": "",
    }
    paths = log.read_text("utf-8").splitlines()
    assert len(set(paths)) == 2
    assert all(not Path(path).exists() for path in paths)


def test_manifest_write_failure_keeps_original_error_through_retry_cleanup(tmp_path):
    source = (WRAPPER.parent / "launch-session.ps1").read_text("utf-8")
    start = source.index("    $savedPsmuxSession = ")
    end = source.index("    if ($newSessionExit -ne 0) {", start)
    retry = source[start:end].replace(
        "[IO.File]::WriteAllText($paneArgsFile, $argsJson)",
        "throw [IO.IOException]::new('synthetic manifest write failure')",
    ).replace("$retryDelayMs = 1000", "$retryDelayMs = 0")
    script = tmp_path / "failed-manifest.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n$paneWrapper=$args[0]\n$RuntimeDir=$args[1]\n"
        "$paneLaunch=Join-Path (Split-Path -Parent $paneWrapper) 'pane-launch.ps1'\n"
        "$plan=[pscustomobject]@{work_dir=$args[1]}\n$wrapperArgs=@('payload')\n"
        "$ahpArgs=@()\n$envFlags=@()\n"
        "function Assert-AwColdResume {}\nfunction Write-SetupLog {}\n"
        "function Write-SetupStatus {}\nfunction Read-AwMuxRetryChoice { $false }\n"
        "$script:stops=0\nfunction Stop-AwOwnedPsmuxSession { $script:stops++ }\n"
        + retry
        + "\n[pscustomobject]@{attempts=$totalCreateAttempts; stops=$script:stops;"
        "code=$newSessionExit; error=$newSessionError} | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(WRAPPER), str(tmp_path)],
        capture_output=True, text=True, timeout=15, env=_test_env(tmp_path),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "attempts": 3, "stops": 3, "code": 1, "error": "synthetic manifest write failure",
    }
    assert not result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing contract")
def test_launcher_locked_cleanup_logs_and_preserves_failure_path(tmp_path):
    source = (WRAPPER.parent / "launch-session.ps1").read_text("utf-8")
    start = source.index("    function Remove-AwPaneArgsFile")
    end = source.index("    $maxCreateAttempts", start)
    script = tmp_path / "locked-cleanup.ps1"
    script.write_text(
        "$script:warnings=@()\n"
        "function Write-SetupLog($message,$level) { $script:warnings+=@($message) }\n"
        + source[start:end]
        + "\n$stream=[IO.File]::Open($args[0],[IO.FileMode]::Open,"
        "[IO.FileAccess]::Read,[IO.FileShare]::None)\n"
        "try { Remove-AwPaneArgsFile $args[0]; $retained=Test-Path -LiteralPath $args[0] }"
        " finally { $stream.Dispose() }\n"
        "Remove-AwPaneArgsFile $args[0]\n"
        "[pscustomobject]@{retained=$retained; removed=(-not (Test-Path -LiteralPath $args[0]));"
        " warnings=@($script:warnings)} | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "locked.json"
    manifest.write_text("{}")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(manifest)],
        capture_output=True, text=True, timeout=15, env=_test_env(tmp_path),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout)
    assert state["retained"] and state["removed"]
    assert len(state["warnings"]) == 1
    assert "retained for expiry" in state["warnings"][0]


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("PSMUX_FILE_LAUNCH_E2E") != "1",
    reason="opt-in real Windows PSMux consumer check",
)
def test_real_psmux_direct_child_control(tmp_path):
    psmux = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    if not psmux:
        pytest.skip("PSMux is required")
    name = "aw-direct-test-" + uuid.uuid4().hex
    try:
        result = subprocess.run(
            [psmux, "new-session", "-d", "-s", name, "-c", str(tmp_path),
             "pwsh.exe", "-NoProfile", "-NoLogo", "-Command", "Start-Sleep -Seconds 20"],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
            env=_test_env(tmp_path), creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert result.returncode == 0, result.stderr
        time.sleep(2)
        live = subprocess.run(
            [psmux, "has-session", "-t", name],
            capture_output=True, text=True, timeout=5,
            env=_test_env(tmp_path), creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert live.returncode == 0, live.stderr
    finally:
        subprocess.run(
            [psmux, "kill-session", "-t", name],
            capture_output=True, text=True, timeout=10,
            env=_test_env(tmp_path), creationflags=subprocess.CREATE_NO_WINDOW,
        )


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("PSMUX_FILE_LAUNCH_E2E") != "1",
    reason="opt-in real Windows PSMux consumer check",
)
@pytest.mark.timeout(180)
@pytest.mark.parametrize("producer", ["powershell", "python"])
def test_real_psmux_file_launch_two_cycles(tmp_path, producer, monkeypatch):
    root = WRAPPER.parents[2]
    # This opt-in cross-plugin fixture supplies AW; standalone Manager does not.
    monkeypatch.syspath_prepend(str(root / "libs" / "repository-policy" / "src"))
    monkeypatch.syspath_prepend(str(root / "plugins" / "agent-worktrees" / "src"))
    from agent_worktrees import config

    monkeypatch.setattr(config, "install_dir", lambda: tmp_path / "runtime")
    psmux = os.environ.get("PSMUX_TEST_BIN") or shutil.which("psmux")
    if not psmux:
        pytest.skip("PSMux is required")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    folder = tmp_path / "wrapper with spaces & $literal ' quote \u03bb"
    folder.mkdir()
    wrapper = folder / WRAPPER.name
    shutil.copy2(WRAPPER, wrapper)
    shutil.copy2(LAUNCHER, folder / LAUNCHER.name)
    child = folder / "capture.ps1"
    child.write_text(
        "[IO.File]::WriteAllText($args[0], "
        "(ConvertTo-Json -InputObject @($args[1..($args.Count-1)]) -Compress))\n"
        "Start-Sleep -Seconds 20\n",
        encoding="utf-8",
    )
    expected = ["", "two words", 'a"quote', "a'quote", "--allow-all", "\u03bb"]
    from agent_worktrees import locks

    observer_path = root / "plugins" / "agent-worktrees" / "tests" / "test_status_monitor_windows.py"
    observer_names = {
        "_ProcessEntry", "_kernel32", "_user32", "_process_snapshot",
        "_window_snapshot", "_foreground_state", "_creation_time", "_descendants",
    }
    definitions = [
        node for node in ast.parse(observer_path.read_text("utf-8")).body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in observer_names
    ]
    assert {node.name for node in definitions} == observer_names
    observer = {
        "ctypes": ctypes, "wintypes": wintypes,
        "m": SimpleNamespace(locks=locks),
    }
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(observer_path), "exec"), observer)
    roots: set[int] = set()
    descendants: set[int] = set()
    visible: set[int] = set()
    foreground: set[int] = set()
    stop = threading.Event()

    def observe():
        while not stop.is_set():
            processes = observer["_process_snapshot"]()
            for root in roots.copy():
                descendants.update(observer["_descendants"](root, processes))
            for hwnd, window in observer["_window_snapshot"](processes).items():
                if window[0] in descendants:
                    visible.add(hwnd)
            active = observer["_foreground_state"](processes)
            if active[1] in descendants:
                foreground.add(active[0])
            stop.wait(0.05)

    with ThreadPoolExecutor(max_workers=1) as executor:
        monitor = executor.submit(observe)
        try:
            _exercise_psmux_cycles(
                tmp_path, producer, psmux, folder, wrapper, child, expected, roots, observer,
            )
        finally:
            stop.set()
            monitor.result(timeout=3)
    assert not visible, "PSMux launch surfaced an owned visible window"
    assert not foreground, "PSMux launch took foreground focus"
    deadline = time.monotonic() + 3
    while descendants & observer["_process_snapshot"]().keys() and time.monotonic() < deadline:
        time.sleep(0.05)
    remaining = observer["_process_snapshot"]()
    leaked = {pid: remaining[pid] for pid in descendants if pid in remaining}
    assert not leaked, f"owned descendants leaked: {leaked}"


def _exercise_psmux_cycles(tmp_path, producer, psmux, folder, wrapper, child, expected, roots, observer):
    from agent_worktrees import locks

    for cycle in range(2):
        name = "aw-file-test-" + uuid.uuid4().hex
        output = tmp_path / f"capture-{cycle}.json"
        child_argv = [PWSH, "-NoProfile", "-File", str(child), str(output), *expected]
        if producer == "powershell":
            command = _powershell_producer(tmp_path, wrapper, child_argv, timeout=45)
        else:
            from agent_worktrees import sessions

            command = sessions._mux_pane_cmd(
                "test-worktree", child_argv, is_tmux=False, pane_wrapper=str(wrapper),
            )
        manifest = Path(command[-1][1:-1].replace("''", "'"))
        receipt = Path(str(manifest) + ".receipt.json")
        from agent_worktrees import config
        from agent_worktrees.sessions_pane_args import sweep_mux_pane_args

        assert manifest.parent == config.install_dir() / "pane-args"
        delayed = time.time() - 60
        os.utime(manifest, (delayed, delayed))
        sweep_mux_pane_args()
        handoff = json.loads(manifest.read_text("utf-8"))
        assert handoff["wrapper"] == str(wrapper)
        expected_argv = (["-AwWt", "test-worktree"] if producer == "python" else []) + child_argv
        assert handoff["argv"] == expected_argv
        assert command[command.index("-File") + 1] == (
            "'" + str(folder / LAUNCHER.name).replace("'", "''") + "'"
        )
        pane_owner = None
        try:
            process = subprocess.Popen(
                [psmux, "new-session", "-d", "-s", name, "-c", str(tmp_path),
                 *command],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                env=_test_env(tmp_path),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            roots.add(process.pid)
            try:
                _stdout, stderr = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
                raise
            assert process.returncode == 0, (
                f"{stderr}; receipt={receipt.read_text('utf-8') if receipt.exists() else 'absent'}"
            )
            pane = subprocess.run(
                [psmux, "display-message", "-p", "-t", name, "#{pane_pid}"],
                capture_output=True, text=True, timeout=5,
                env=_test_env(tmp_path), creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert pane.returncode == 0 and pane.stdout.strip().isdigit(), pane.stderr
            pane_pid = int(pane.stdout.strip())
            pane_birth = locks.process_start_time(pane_pid)
            assert pane_birth is not None, "could not identify the owned pane process"
            pane_owner = pane_pid, pane_birth
            roots.add(pane_pid)
            deadline = time.monotonic() + 45
            while (
                not output.exists()
                and locks.process_start_time(pane_pid) == pane_birth
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            if not output.exists():
                snapshot = observer["_process_snapshot"]()
                owned = (
                    observer["_descendants"](pane_pid, snapshot)
                    if locks.process_start_time(pane_pid) == pane_birth else set()
                )
                diagnostic = subprocess.run(
                    [psmux, "capture-pane", "-p", "-t", name],
                    capture_output=True, text=True, encoding="utf-8", timeout=5,
                    env=_test_env(tmp_path), creationflags=subprocess.CREATE_NO_WINDOW,
                )
                pytest.fail(
                    f"manifest_pending={manifest.exists()}; "
                    f"receipt={receipt.read_text('utf-8') if receipt.exists() else 'absent'}; "
                    f"owned_processes={ {pid: snapshot[pid] for pid in owned if pid in snapshot} }; "
                    f"{diagnostic.stdout}; "
                    f"{diagnostic.stderr}; {stderr}"
                )
            assert json.loads(output.read_text("utf-8")) == expected
            assert not manifest.exists()
            if producer == "powershell":
                acknowledged = json.loads(receipt.read_text("utf-8"))
                assert acknowledged["status"] == "consumed"
                assert acknowledged["manifest"] == manifest.name
        finally:
            stopped = subprocess.run(
                [psmux, "kill-session", "-t", name],
                capture_output=True, text=True, timeout=10, env=_test_env(tmp_path),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert stopped.returncode == 0, stopped.stderr
            if pane_owner is not None:
                pane_pid, pane_birth = pane_owner
                deadline = time.monotonic() + 3
                while (
                    locks.process_start_time(pane_pid) == pane_birth
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                assert locks.process_start_time(pane_pid) != pane_birth, (
                    "owned pane process survived kill-session"
                )
            manifest.unlink(missing_ok=True)
            receipt.unlink(missing_ok=True)
