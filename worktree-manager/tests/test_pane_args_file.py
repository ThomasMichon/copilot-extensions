"""Exercise the shipped file-based pane argv consumer with PowerShell."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / "bin" / "pane-wrapper.ps1"
LAUNCHER = WRAPPER.with_name("pane-launch.ps1")
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell 7 is required")


def _test_env(tmp_path: Path) -> dict[str, str]:
    return dict(
        os.environ, USERPROFILE=str(tmp_path / "profile"),
        HOME=str(tmp_path / "profile"), WORKTREE_PANE_MIN_RUNTIME="0",
        TEMP=str(tmp_path), TMP=str(tmp_path),
        PSMUX_NO_WARM="1", PSMUX_DATA_DIR=str(tmp_path / "psmux-data"),
    )


def _powershell_producer(tmp_path: Path, wrapper: Path, argv: list[str]) -> list[str]:
    source = (WRAPPER.parent / "launch-session.ps1").read_text("utf-8")
    start = source.index("                    $paneArgsFile = Join-Path")
    end = source.index("\n                }\n                $savedAuth", start)
    payload = tmp_path / "producer-input.json"
    payload.write_text(json.dumps(argv), encoding="utf-8")
    script = tmp_path / "produce.ps1"
    script.write_text(
        "$paneWrapper=$args[0]\n"
        "$paneLaunch=Join-Path (Split-Path -Parent $paneWrapper) 'pane-launch.ps1'\n"
        "$inputJson=[System.Text.Json.JsonDocument]::Parse([IO.File]::ReadAllText($args[1]))\n"
        "$wrapperArgs=@($inputJson.RootElement.EnumerateArray() | ForEach-Object { $_.GetString() })\n"
        "$inputJson.Dispose()\n"
        + source[start:end]
        + "\nConvertTo-Json -InputObject @($paneCmd) -Compress -EscapeHandling EscapeNonAscii\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(wrapper), str(payload)],
        capture_output=True, text=True, encoding="utf-8", timeout=15, env=_test_env(tmp_path),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _run(manifest: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [PWSH, "-NoProfile", "-NoLogo", "-File", str(LAUNCHER),
         "-Manifest", str(manifest)],
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
        try:
            result = _run(manifest, env)
            assert result.returncode == 0, result.stderr
            assert json.loads(output.read_text("utf-8")) == expected
            assert not manifest.exists()
        finally:
            manifest.unlink(missing_ok=True)


@pytest.mark.parametrize("nested", [False, True])
def test_launch_plan_preserves_string_argv_before_manifest_creation(tmp_path, nested):
    source = (WRAPPER.parent / "launch-session.ps1").read_text("utf-8")
    start = source.index('$plan = ($jsonOutput -join "`n")')
    end = source.index("# Feed the crash-detector trap", start)
    expected = ["program", "", "2026-10-08T12:00:00+05:30", "--allow-all"]
    plan = {"action": "exec", "cmd": expected}
    payload = tmp_path / "plan.json"
    payload.write_text(json.dumps({"launch": plan} if nested else plan), encoding="utf-8")
    script = tmp_path / "plan-argv.ps1"
    script.write_text(
        "$jsonOutput=@([IO.File]::ReadAllText($args[0]))\n"
        + source[start:end]
        + "\nConvertTo-Json -InputObject @($plan.cmd) -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(payload)],
        capture_output=True, text=True, timeout=15, env=_test_env(tmp_path),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


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
        "$paneLaunch=Join-Path (Split-Path -Parent $paneWrapper) 'pane-launch.ps1'\n"
        "$plan=[pscustomobject]@{work_dir=$args[2]}\n"
        "$wrapperArgs=@('payload','two words')\n$ahpArgs=@()\n$envFlags=@()\n"
        "function Write-SetupLog {}\nfunction Write-SetupStatus {}\n"
        "$script:stops=0\nfunction Stop-AwOwnedPsmuxSession { $script:stops++ }\n"
        "function Read-AwMuxRetryChoice { $false }\n"
        + source[start:end]
        + "\n[pscustomobject]@{attempts=$totalCreateAttempts; stops=$script:stops;"
        " code=$newSessionExit} | ConvertTo-Json -Compress\n",
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
    assert json.loads(result.stdout) == {"attempts": 2, "stops": 1, "code": 0}
    paths = log.read_text("utf-8").splitlines()
    assert len(set(paths)) == 2
    assert all(not Path(path).exists() for path in paths)


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("PSMUX_FILE_LAUNCH_E2E") != "1",
    reason="opt-in real Windows PSMux consumer check",
)
@pytest.mark.parametrize("producer", ["powershell", "python"])
def test_real_psmux_file_launch_two_cycles(tmp_path, producer, monkeypatch):
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
    observer = runpy.run_path(str(
        WRAPPER.parents[2] / "plugins" / "agent-worktrees" / "tests"
        / "test_status_monitor_windows.py"
    ))
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
                tmp_path, producer, psmux, folder, wrapper, child, expected, roots,
            )
        finally:
            stop.set()
            monitor.result(timeout=3)
    assert not visible, "PSMux launch surfaced an owned visible window"
    assert not foreground, "PSMux launch took foreground focus"
    deadline = time.monotonic() + 3
    while descendants & observer["_process_snapshot"]().keys() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not (descendants & observer["_process_snapshot"]().keys()), "owned descendants leaked"


def _exercise_psmux_cycles(tmp_path, producer, psmux, folder, wrapper, child, expected, roots):
    for cycle in range(2):
        name = "aw-file-test-" + uuid.uuid4().hex
        output = tmp_path / f"capture-{cycle}.json"
        child_argv = [PWSH, "-NoProfile", "-File", str(child), str(output), *expected]
        if producer == "powershell":
            command = _powershell_producer(tmp_path, wrapper, child_argv)
        else:
            from agent_worktrees import sessions

            command = sessions._mux_pane_cmd(
                "test-worktree", child_argv, is_tmux=False, pane_wrapper=str(wrapper),
            )
        manifest = Path(command[-1][1:-1].replace("''", "'"))
        if producer == "python":
            from agent_worktrees.sessions_pane_args import sweep_mux_pane_args

            delayed = time.time() - 60
            os.utime(manifest, (delayed, delayed))
            sweep_mux_pane_args()
        handoff = json.loads(manifest.read_text("utf-8"))
        assert handoff["wrapper"] == str(wrapper)
        expected_argv = (["-AwWt", "test-worktree"] if producer == "python" else []) + child_argv
        assert handoff["argv"] == expected_argv
        assert command[-3] == "'" + str(folder / LAUNCHER.name).replace("'", "''") + "'"
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
            assert process.returncode == 0, stderr
            deadline = time.monotonic() + 8
            while not output.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not output.exists():
                diagnostic = subprocess.run(
                    [psmux, "capture-pane", "-p", "-t", name],
                    capture_output=True, text=True, encoding="utf-8", timeout=5,
                    env=_test_env(tmp_path), creationflags=subprocess.CREATE_NO_WINDOW,
                )
                pytest.fail(
                    f"manifest_pending={manifest.exists()}; {diagnostic.stdout}; "
                    f"{diagnostic.stderr}; {stderr}"
                )
            assert json.loads(output.read_text("utf-8")) == expected
            assert not manifest.exists()
        finally:
            stopped = subprocess.run(
                [psmux, "kill-session", "-t", name],
                capture_output=True, text=True, timeout=10, env=_test_env(tmp_path),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            manifest.unlink(missing_ok=True)
            assert stopped.returncode == 0, stopped.stderr
