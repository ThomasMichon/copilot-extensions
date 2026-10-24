"""Regression guard: the Linux/WSL installer must refuse to downgrade a
running agent-bridge from a stale checkout (#1790).

A stress test caught an agent running the raw ``install.sh update`` from an
old local checkout (0.4.0-dev71) over a live dev87 daemon, silently
*downgrading* it -- reverting the Session-Host survival code and the
``KillMode=process`` fix, and stranding the agent's own Copilot session. The
sanctioned update path is the marketplace flow
(``test-chamber services agent-bridge update``); the raw installer must at
least refuse an unforced downgrade.

Shape checks retain guard placement; extracted functions exercise package
ordering and the actual refusal without executing the installer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

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


def _function(name: str) -> str:
    text = _text()
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 2]


def _run(tmp_path: Path, body: str, *args: str) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    bash = (
        str(Path(git).resolve().parents[1] / "bin" / "bash.exe") if git else None
    ) if os.name == "nt" else shutil.which("bash")
    if not bash or not Path(bash).is_file():
        pytest.skip("A native Bash interpreter is unavailable")
    script = tmp_path / "ordering.sh"
    script.write_text(
        "set -euo pipefail\n" + _function("_version_lt")
        + "\nsort() { printf 'unexpected sort invocation\\n' >&2; return 97; }\n"
        + body, encoding="utf-8", newline="\n",
    )
    return subprocess.run(
        [bash, "--noprofile", "--norc", script.name, *args], cwd=tmp_path,
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.mark.parametrize(("a", "b", "older"), [
    ("0.1.2-dev74", "0.1.2.dev102", True),
    ("0.1.2.dev102", "0.1.2-dev74", False),
    ("0.1.2.dev74", "0.1.2-dev74", False),
    ("0.1.9", "0.1.10", True),
    ("0.9.0", "0.10.0", True),
    ("1.0.0", "0.9.999", False),
    ("0.1.2.dev5", "0.1.2", True),
    ("0.1.2", "0.1.2.dev5", False),
    ("0.1.2", "0.1.2", False),
    ("1.02.3-dev0009", "1.2.3.dev10", True),
    ("0.1.2+source.aaaa", "0.1.2+source.bbbb", False),
    ("0.1.2.source.1234", "0.1.2", False),
])
def test_portable_package_ordering(tmp_path, a, b, older):
    result = _run(
        tmp_path,
        'if _version_lt "$1" "$2"; then printf older; else printf not-older; fi\n',
        a, b,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == ("older" if older else "not-older")


@pytest.mark.parametrize(("source", "installed", "refused"), [
    ("0.1.2-dev150", "0.1.2.dev82", False),
    ("0.1.2-dev82", "0.1.2.dev150", True),
    ("0.1.2", "0.1.2.dev150", False),
    ("0.1.2-dev150", "0.1.2", True),
    ("0.1.2", "0.1.3", True),
])
def test_actual_downgrade_guard(tmp_path, source, installed, refused):
    body = _function("_downgrade_guard") + """
CASE_SOURCE="$1"; CASE_INSTALLED="$2"; FORCE=0; ACTION=update
_source_version() { printf '%s\\n' "$CASE_SOURCE"; }
_installed_version() { printf '%s\\n' "$CASE_INSTALLED"; }
_warn() { :; }
_fail() { printf '%s\\n' "$*" >&2; }
_downgrade_guard
"""
    result = _run(tmp_path, body, source, installed)
    assert result.returncode == (1 if refused else 0), result.stderr
    assert ("Refusing to downgrade" in result.stderr) == refused
    assert "unexpected sort invocation" not in result.stderr


def test_first_install_has_no_downgrade(tmp_path):
    body = _function("_downgrade_guard") + """
_installed_version() { return 1; }
_source_version() { return 97; }
_downgrade_guard
"""
    result = _run(tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
