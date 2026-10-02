"""Windows installer guards for forwarded venue bridge routes."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")


def _extract_function(name: str) -> str:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    start = text.index(f"function {name}")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting {name!r}")


def _write_forward(tmp_path: Path, active: dict) -> None:
    install_dir = tmp_path / "agent-bridge"
    install_dir.mkdir()
    (install_dir / "active.json").write_text(
        json.dumps({"active": active}), encoding="utf-8"
    )


def _run_harness(tmp_path: Path, functions: list[str], extra: str) -> subprocess.CompletedProcess:
    install_dir = tmp_path / "agent-bridge"
    link_python = tmp_path / "python.exe"
    link_python.write_text("stub", encoding="utf-8")
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        f"$InstallDir = '{install_dir}'\n"
        f"$PidFile = '{install_dir / 'agent-bridge.pid'}'\n"
        f"$LinkPython = '{link_python}'\n"
        "$Port = 9280\n"
        "$TaskName = 'agent-bridge'\n"
        "$ScheduledTaskHasNotRunResult = 267009\n"
        "function Write-Skip { param([string]$m) Write-Host \"SKIP: $m\" }\n"
        "function Write-Fail { param([string]$m) throw $m }\n"
        "function Write-Step { param([string]$m) Write-Host \"STEP: $m\" }\n"
        "function Write-Warn { param([string]$m) Write-Host \"WARN: $m\" }\n"
        "function Write-Ok { param([string]$m) Write-Host \"OK: $m\" }\n"
        + "\n\n".join(_extract_function(name) for name in functions)
        + "\n\n"
        + extra
        + "\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


@pytest.mark.parametrize(
    ("active", "expected"),
    [
        ({"bind": "127.0.0.1", "port": 62254, "forwarded": True}, True),
        ({"port": 62254}, True),
        ({"bind": "127.0.0.1", "port": 62254}, False),
    ],
)
def test_install_ps1_recognizes_forwarded_active_routes(
    tmp_path: Path, active: dict, expected: bool,
) -> None:
    _write_forward(tmp_path, active)
    result = _run_harness(
        tmp_path,
        ["Test-ActiveIsForward"],
        "Write-Host \"FORWARD=$(Test-ActiveIsForward)\"",
    )
    assert f"FORWARD={expected}" in result.stdout


def test_get_running_process_never_returns_forward_listener(tmp_path: Path) -> None:
    _write_forward(tmp_path, {"bind": "127.0.0.1", "port": 62254, "forwarded": True})
    result = _run_harness(
        tmp_path,
        ["Test-ActiveIsForward", "Get-ActiveEndpoint", "Get-RunningProcess"],
        """
function Get-Process { return $null }
function Get-NetTCPConnection { throw 'must not inspect the forwarded listener' }
$rp = Get-RunningProcess
if ($null -eq $rp) { Write-Host 'RUNNING=NULL' } else { Write-Host "RUNNING=$($rp.Id)" }
""",
    )
    assert "RUNNING=NULL" in result.stdout


def test_invoke_start_skips_over_forwarded_route(tmp_path: Path) -> None:
    _write_forward(tmp_path, {"bind": "127.0.0.1", "port": 62254, "forwarded": True})
    result = _run_harness(
        tmp_path,
        ["Test-ActiveIsForward", "Invoke-Start"],
        """
function Get-RunningProcess { throw 'must not inspect or stop forwarded route' }
function Test-HealthOnce { throw 'must not probe forwarded route' }
Invoke-Start
""",
    )
    assert "SKIP:" in result.stdout
    assert "not starting a local daemon" in result.stdout


def test_install_ps1_update_checks_forward_before_lifecycle_actions() -> None:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    helper = text.split("function Invoke-UpdateDrainStop", 1)[1].split(
        "\n}\n\nfunction Invoke-UpdateStart", 1
    )[0]
    body = text.split("function Invoke-Update {", 1)[1].split("\n}\n\n# -- Dispatch", 1)[0]
    forward_at = body.index("$activeForward = Test-ActiveIsForward")
    revalidate_at = body.index("Invoke-UpdateDrainStop")
    start_at = body.index("Invoke-UpdateStart")
    assert forward_at < revalidate_at < start_at
    assert (
        helper.index("Test-UpdateLifecycleStillTargetsPredecessor")
        < helper.index("Invoke-Drain")
        < helper.rindex("Test-UpdateLifecycleStillTargetsPredecessor")
        < helper.index("Invoke-Stop")
    )
    assert "$wasRunning = (-not $activeForward) -and" in body
    assert "if ($activeForward) {" in body
    assert "Forwarded host bridge route appeared during update -- skipping drain/stop/start" in text
    assert "Active route changed during update -- skipping drain/stop/start" in text
    assert "Forwarded host bridge route still active -- not starting a local daemon" in body
    assert "Invoke-UpdateDrainStop -Signature $predecessorSignature -TimeoutSec 30" in body
    assert "Invoke-UpdateStart -Signature $predecessorSignature" in body
    assert "Invoke-UpdateStart -Signature $predecessorSignature -Message 'Restarting the previous version...'" in body
    assert "Invoke-UpdateStart -Signature $predecessorSignature -Message 'Restarting the previous service...'" in body


@pytest.mark.parametrize(
    ("current", "forward", "starts"),
    [
        ("", False, True),          # our own drain-stop cleared the route
        ("sig", False, True),       # still the pinned predecessor
        ("other", False, False),    # a different successor took over
        ("", True, False),          # a forwarded route appeared
    ],
)
def test_update_start_accepts_the_route_its_own_stop_cleared(
    tmp_path: Path, current: str, forward: bool, starts: bool,
) -> None:
    (tmp_path / "agent-bridge").mkdir()
    result = _run_harness(
        tmp_path,
        ["Test-UpdateLifecycleStillTargetsPredecessor", "Invoke-UpdateStart",
         "Get-SignatureBaseUrl", "Invoke-UpdateDrainStop"],
        f"""
function Test-ActiveIsForward {{ return ${str(forward).lower()} }}
function Get-ActiveSignature {{ return '{current}' }}
function Invoke-Start {{ Write-Host 'STARTED' }}
function Invoke-Drain {{ param($TimeoutSec, $BaseUrl) }}
function Invoke-Stop {{ Write-Host 'STOPPED' }}
$null = Invoke-UpdateStart -Signature 'sig'
$null = Invoke-UpdateDrainStop -Signature 'sig'
""",
    )
    assert ("STARTED" in result.stdout) is starts
    # Drain/stop stays strict: it never stops anything but the pinned route.
    assert ("STOPPED" in result.stdout) is (current == "sig" and not forward)


@pytest.mark.parametrize(
    ("after", "forward", "stops"),
    [
        ("sig", False, True),     # still the pinned predecessor after the drain
        ("", False, True),        # the drained predecessor exited and cleared its route
        ("other", False, False),  # another daemon took the route during the drain
        ("sig", True, False),     # a venue forward published during the drain
    ],
)
def test_update_drain_stop_revalidates_the_route_after_draining(
    tmp_path: Path, after: str, forward: bool, stops: bool,
) -> None:
    """The drain can run for minutes; a forward published meanwhile must not be
    stopped by the update's port cleanup."""
    (tmp_path / "agent-bridge").mkdir()
    result = _run_harness(
        tmp_path,
        ["Test-UpdateLifecycleStillTargetsPredecessor", "Get-SignatureBaseUrl",
         "Invoke-UpdateDrainStop"],
        f"""
$script:drained = $false
function Test-ActiveIsForward {{ return ($script:drained -and ${str(forward).lower()}) }}
function Get-ActiveSignature {{ if ($script:drained) {{ return '{after}' }} return 'sig' }}
function Invoke-Drain {{ param($TimeoutSec, $BaseUrl) $script:drained = $true; Write-Host 'DRAINED' }}
function Invoke-Stop {{ Write-Host 'STOPPED' }}
$r = Invoke-UpdateDrainStop -Signature 'sig'
Write-Host "RESULT=$r"
""",
    )
    assert "DRAINED" in result.stdout
    assert ("STOPPED" in result.stdout) is stops
    assert f"RESULT={stops}" in result.stdout


@pytest.mark.parametrize(
    ("signature", "url"),
    [
        ("127.0.0.1|41000|123|7", "http://127.0.0.1:41000"),
        ("0.0.0.0|41000||", "http://127.0.0.1:41000"),
        ("::|41000||", "http://[::1]:41000"),
        ("", "http://127.0.0.1:9280"),  # no route: the fixed-port daemon
    ],
)
def test_update_drain_is_pinned_to_the_validated_predecessor(
    tmp_path: Path, signature: str, url: str,
) -> None:
    """A route rewritten (e.g. to a forward) after validation can't redirect the
    drain: it targets the predecessor's own endpoint via AGENT_BRIDGE_BASE_URL."""
    (tmp_path / "agent-bridge").mkdir()
    result = _run_harness(
        tmp_path,
        ["Get-SignatureBaseUrl", "Invoke-UpdateDrainStop"],
        f"""
function Test-UpdateLifecycleStillTargetsPredecessor {{ param($Signature) return $true }}
function Invoke-Drain {{ param($TimeoutSec, $BaseUrl) Write-Host "DRAIN=$BaseUrl" }}
function Invoke-Stop {{ }}
$null = Invoke-UpdateDrainStop -Signature '{signature}'
""",
    )
    assert f"DRAIN={url}" in result.stdout
