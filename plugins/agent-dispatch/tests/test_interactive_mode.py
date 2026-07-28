"""Interactive service mode guards for the Windows installer (install.ps1).

On an interactive-required host (dev6/cloud1/augloop1: an RDP/console logon is
required before SSH works, and Task Scheduler registration is admin-gated), the
non-elevated HKCU logon auto-start is the FIRST-CLASS coordinator service -- no
elevated S4U boot task. These read install.ps1 as text and assert that shape so
the mode can't silently regress to requiring elevation.
"""

from __future__ import annotations

import re
from pathlib import Path

INSTALL_PS1 = Path(__file__).resolve().parent.parent / "scripts" / "install.ps1"


def _text() -> str:
    return INSTALL_PS1.read_text(encoding="utf-8")


def test_interactive_switch_is_a_param():
    assert re.search(r"\[switch\]\$Interactive", _text()), (
        "install.ps1 must expose a first-class -Interactive switch"
    )


def test_service_mode_resolver_exists():
    text = _text()
    assert "function Get-ServiceMode" in text
    # Precedence: the -Interactive flag wins, else the persisted marker.
    assert "'service-mode'" in text, "mode must persist via a service-mode marker"


def test_interactive_mode_uses_logon_autostart_not_a_task():
    """In interactive mode the coordinator install must take the non-elevated
    logon auto-start path (Primary) and return BEFORE the S4U task registration."""
    text = _text()
    m = re.search(r"function\s+Install-CoordinatorTask\s*\{", text)
    assert m, "could not locate Install-CoordinatorTask"
    rest = text[m.end():]
    nxt = re.search(r"\n function |\nfunction ", rest)
    body = rest[: nxt.start()] if nxt else rest

    branch = body.index("(Get-ServiceMode) -eq 'interactive'")
    reg = body.index("Register-ScheduledTask -TaskName")
    assert branch < reg, "interactive-mode branch must precede task registration"

    interactive_block = body[branch:reg]
    assert "-Primary" in interactive_block, (
        "interactive mode must install the logon auto-start as the primary service"
    )
    assert "return" in interactive_block, (
        "interactive mode must return before attempting an elevated Scheduled Task"
    )


def test_autostart_function_supports_primary_flag():
    text = _text()
    m = re.search(r"function\s+Start-CoordinatorNonElevatedFallback\s*\{", text)
    assert m, "autostart function missing"
    rest = text[m.end():]
    nxt = re.search(r"\nfunction ", rest)
    body = rest[: nxt.start()] if nxt else rest
    assert "[switch]$Primary" in body, (
        "the non-elevated autostart must accept -Primary so interactive mode is a "
        "first-class service, not a degraded fallback"
    )


def test_uninstall_removes_logon_autostart():
    text = _text()
    assert "Remove-CoordinatorAutostart" in text
    assert "Remove-SupervisorAutostart" in text


def test_interactive_mode_pins_loopback():
    """Interactive mode ignores WSL and pins the coordinator to 127.0.0.1 so it is
    reachable non-elevated on a NAT box (no elevation-gated firewall rule)."""
    text = _text()
    assert "function Set-ServiceEnvLoopback" in text
    assert "AGENT_DISPATCH_HOST=127.0.0.1" in text
    m = re.search(r"function\s+Install-CoordinatorTask\s*\{", text)
    rest = text[m.end():]
    nxt = re.search(r"\n function |\nfunction ", rest)
    body = rest[: nxt.start()] if nxt else rest
    interactive_block = body[body.index("(Get-ServiceMode) -eq 'interactive'"):body.index("Register-ScheduledTask -TaskName")]
    assert "Set-ServiceEnvLoopback" in interactive_block, (
        "interactive mode must pin loopback before starting the coordinator"
    )
