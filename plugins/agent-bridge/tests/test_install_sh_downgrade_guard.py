"""Regression guard: the Linux/WSL installer must refuse to downgrade a
running agent-bridge from a stale checkout (#1790).

A stress test caught an agent running the raw ``install.sh update`` from an
old local checkout (0.4.0-dev71) over a live dev87 daemon, silently
*downgrading* it -- reverting the Session-Host survival code and the
``KillMode=process`` fix, and stranding the agent's own Copilot session. The
sanctioned update path is the marketplace flow
(``aperture-labs services agent-bridge update``); the raw installer must at
least refuse an unforced downgrade.

These are file-shape assertions over ``scripts/install.sh`` (bash is not
guaranteed on every CI host) so a future edit that drops the guard trips a
test. Behavioural ordering of ``sort -V`` for the ``0.4.0-devN`` stream is
exercised separately where bash is available.
"""

from __future__ import annotations

from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"


def _text() -> str:
    return _INSTALL_SH.read_text(encoding="utf-8")


def test_install_sh_exists():
    assert _INSTALL_SH.is_file(), "Linux/WSL installer must ship"


def test_guard_helpers_defined():
    text = _text()
    for fn in (
        "_installed_version()",
        "_source_version()",
        "_version_lt()",
        "_downgrade_guard()",
    ):
        assert fn in text, f"install.sh must define {fn} for the downgrade guard (#1790)"


def test_force_flag_parsed():
    text = _text()
    assert "--force)" in text, "install.sh must accept --force to override the guard (#1790)"
    assert "AGENT_BRIDGE_ALLOW_DOWNGRADE" in text, (
        "install.sh must honor AGENT_BRIDGE_ALLOW_DOWNGRADE=1 as the env override (#1790)"
    )


def test_guard_runs_in_update_before_stopping():
    """The guard must fire before the update drains/stops the live daemon, so a
    rejected downgrade never disturbs the running service."""
    text = _text()
    guard_at = text.index("_downgrade_guard\n", text.index("do_update()"))
    stop_at = text.index("do_stop", text.index("do_update()"))
    assert guard_at < stop_at, "downgrade guard must run before do_stop in do_update (#1790)"


def test_guard_runs_in_install():
    text = _text()
    install_block = text[text.index("do_install()"):text.index("do_uninstall()")]
    assert "_downgrade_guard" in install_block, (
        "downgrade guard must also run in do_install (#1790)"
    )


def test_version_compare_uses_sort_v():
    """The ordering must go through `sort -V`, which correctly orders the
    `0.4.0-devN` build stream (dev71 < dev92 < dev100) on the Linux/WSL hosts
    the installer actually runs on. (Real ordering is validated live on WSL;
    Windows git-bash `sort` lacks -V so it is not exercised here.)"""
    text = _text()
    start = text.index("_version_lt()")
    body = text[start:text.index("\n}", start)]
    assert "sort -V" in body, "_version_lt must order versions with `sort -V` (#1790)"
