"""Native copied-shell transport controls, not real installation survival."""

import base64
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest
from agent_procutil import no_window_kwargs

from _manager_process_fixture import boundary_runtime
from worktree_manager import mux_mapping_registry as mappings

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native PowerShell boundary")
WRAPPER = Path(__file__).resolve().parents[2] / "bin" / "pane-wrapper.ps1"


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def functions():
    source = WRAPPER.read_text(encoding="utf-8")
    body = "function Invoke-AwPaneBoundaryProcess {" + source.split(
        "function Invoke-AwPaneBoundaryProcess {", 1,
    )[1].split("$awSessionIncarnation = ''", 1)[0]
    return body.replace(
        "'-X', 'utf8', $bootstrap", "'-X', 'utf8', '-X', 'importtime', $bootstrap",
    ).replace(
        "$null = $process.WaitForExit(1000)",
        "$null = $process.WaitForExit(1000)\n"
        "if($stderr.Wait(1000)){$script:BoundaryImportDiagnostics="
        "$stderr.Result.Substring([Math]::Max(0,$stderr.Result.Length-16384))}",
    )


@pytest.mark.parametrize("failure", ["prompt", "receipt", "publication", "token", "command", "child-exit"])
def test_wrapper_validation_cancels_and_command_dispatch_preserves_exit(tmp_path, failure):
    pwsh = shutil.which("pwsh")
    assert pwsh, "PowerShell 7 is required"
    probe, boundaries, child = (tmp_path / name for name in ("probe", "boundaries", "child"))
    driver = tmp_path / "copied-wrapper.ps1"
    overrides = (
        "function Invoke-AwPaneBoundaryProcess {"
        f"[IO.File]::WriteAllText({quote(probe)},'probe');return '$1:123|%1'"
        "}\nfunction Invoke-AwPaneBoundary {\n"
        "param([string]$Action,[string]$Installation,[int]$ObservedExit=0)\n"
        f"[IO.File]::AppendAllText({quote(boundaries)},"
        '("{0}:{1}`n" -f $Action,$ObservedExit))\n'
        "return [pscustomobject]@{installation=('a'*64)}\n}\n"
    )
    driver.write_text(WRAPPER.read_text(encoding="utf-8").replace(
        "$awSessionIncarnation = ''", overrides + "$awSessionIncarnation = ''", 1,
    ), encoding="utf-8")
    encode = lambda value: base64.b64encode(str(value).encode()).decode()
    controls = []
    if failure == "prompt":
        controls = ["--aw-prompt-b64", "%%%", "--aw-prompt-receipt-b64", encode(tmp_path / "receipt")]
    elif failure == "receipt":
        blocked = tmp_path / "blocked"
        blocked.touch()
        controls = ["--aw-prompt-b64", encode("prompt"),
                    "--aw-prompt-receipt-b64", encode(blocked / "receipt")]
    elif failure == "publication":
        receipt = tmp_path / "locked-receipt"
        receipt.write_bytes(b"incumbent")
        controls = ["--aw-prompt-b64", encode("prompt"), "--aw-prompt-receipt-b64", encode(receipt)]
    elif failure == "token":
        controls = ["-AwAhpTokenFile", str(tmp_path / "missing-token")]
    backend = [str(tmp_path / "missing-command")] if failure == "command" else [
        pwsh, "-NoProfile", "-Command", f"[IO.File]::WriteAllText({quote(child)},'started');exit 7",
    ]
    with ExitStack() as locks:
        if failure == "publication":
            locks.enter_context(receipt.open("rb"))
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NoLogo", "-File", str(driver),
             "-AwManagerRoot", str(tmp_path / "Manager"), "-AwProject", "example",
             "-AwWt", "worktree", "-AwMuxSession", "wt-worktree", "-AwPsmuxBin", "test-mux",
             *controls, *backend],
            cwd=tmp_path, env=dict(os.environ, WORKTREE_PANE_MIN_RUNTIME="0", WORKTREE_PANE_WAIT_TIMEOUT="0"),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
        )
    assert child.exists() is (failure == "child-exit")
    if failure in {"command", "child-exit"}:
        assert result.returncode == 0, result.stdout + result.stderr
        expected = 127 if failure == "command" else 7
        assert boundaries.read_text(encoding="utf-8").splitlines() == ["start:0", f"end:{expected}"]
    else:
        assert result.returncode == 2, result.stdout + result.stderr
        assert probe.exists()
        assert boundaries.read_text(encoding="utf-8").splitlines() == ["start:0", "cancel:0"]
        if failure == "publication":
            assert receipt.read_bytes() == b"incumbent"


@pytest.mark.parametrize("replacement", ["B", "absent"])
def test_current_boundary_is_resolved_after_old_runtime_removal(tmp_path, replacement):
    pwsh = shutil.which("pwsh")
    assert pwsh, "PowerShell 7 is required"
    root = tmp_path / "Manager with spaces"
    first = boundary_runtime(root, "A", diagnostics=True)
    if replacement == "B":
        boundary_runtime(root, "B", diagnostics=True)
    marker = root / "current-version"
    marker.write_text("A\n", encoding="utf-8")
    ready, proceed = tmp_path / "ready.json", tmp_path / "proceed"
    driver = tmp_path / "copied-boundary.ps1"
    driver.write_text(
        "$ErrorActionPreference='Stop'\n" + functions() +
        f"\n$awManagerRoot={quote(root)}\n"
        "$awProject='example';$awWt='worktree';$awMuxSession='wt-worktree'\n"
        "$awSessionIncarnation='$1:123';$awNativePane='%1';$awPsmuxBin='psmux'\n"
        "$started=Invoke-AwPaneBoundary 'start'\n"
        f"if(-not $started){{[IO.File]::WriteAllText({quote(ready)},"
        "(@{boundary_status=$script:AwPaneBoundaryProcessStatus;"
        "imports=$script:BoundaryImportDiagnostics}|ConvertTo-Json));exit 3}\n"
        f"[IO.File]::WriteAllText({quote(ready)},($started|ConvertTo-Json -Depth 8))\n"
        "$deadline=[DateTime]::UtcNow.AddSeconds(15)\n"
        f"while(-not(Test-Path -LiteralPath {quote(proceed)})){{\n"
        "if([DateTime]::UtcNow -gt $deadline){throw 'Controller did not release boundary'}\n"
        "Start-Sleep -Milliseconds 25\n}\n"
        "$runtime=42\n$ended=Invoke-AwPaneBoundary 'end' $started.installation 7\n"
        "[pscustomobject]@{admitted=($null -ne $ended);"
        "boundary_status=$script:AwPaneBoundaryProcessStatus;"
        "imports=$script:BoundaryImportDiagnostics}|ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [pwsh, "-NoProfile", "-NoLogo", "-File", str(driver)],
        cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.025)
        assert ready.exists(), "Actual current-runtime start did not complete"
        started = json.loads(ready.read_text(encoding="utf-8"))
        assert started.get("result", {}).get("applied") is True, started.get("imports") or started
        assert mappings.get_mapping("example", "worktree", root=root)["live"] is True
        if replacement == "B":
            marker.write_text("B\n", encoding="utf-8")
            shutil.rmtree(first)
        else:
            shutil.rmtree(root)
        proceed.touch()
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stdout + stderr
        assert stderr == ""
        reply = json.loads(stdout)
        assert reply["admitted"] is (replacement == "B"), reply.get("imports") or reply
        if replacement == "B":
            observed = mappings.get_mapping("example", "worktree", root=root)
            assert observed["observed_end"]["exit_code"] == 7
            assert observed["observed_end"]["pane_id"] == "%1"
            assert observed["live"] is False
            assert not first.exists()
        else:
            assert not root.exists(), "Absent handler must not provision or recreate its root"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
