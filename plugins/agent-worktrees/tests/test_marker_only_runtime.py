"""Tests for the junction-free, marker-only runtime resolution (#1106).

Every runtime-resolution flow resolves the active slot python via the
`current-version` marker -> versions/<ver>, never through a `.venv` link. These
tests cover the shared resolver helpers' presence + deployment, and that no
runtime-resolution flow still reads a `.venv` link.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPTS = _PLUGIN / "scripts"
_BIN = _PLUGIN / "bin"


def test_resolver_helpers_exist():
    assert (_SCRIPTS / "resolve-runtime.sh").is_file()
    assert (_SCRIPTS / "resolve-runtime.ps1").is_file()


def test_resolver_helpers_are_marker_only():
    for name in ("resolve-runtime.sh", "resolve-runtime.ps1"):
        text = (_SCRIPTS / name).read_text(encoding="utf-8")
        assert "current-version" in text, f"{name} must resolve via the marker"
        assert "versions" in text
        # The resolver never resolves a python interpreter THROUGH a `.venv` path.
        assert not re.search(r"\.venv[/\\]", text), f"{name} must not read a .venv link"


@pytest.mark.parametrize("installer", ["install.ps1", "install.sh"])
def test_installers_deploy_the_resolver(installer: str):
    text = (_SCRIPTS / installer).read_text(encoding="utf-8")
    assert "resolve-runtime.ps1" in text
    assert "resolve-runtime.sh" in text


def test_installer_py_deploys_the_resolver():
    text = (_PLUGIN / "src" / "agent_worktrees" / "installer.py").read_text("utf-8")
    assert "resolve-runtime.ps1" in text
    assert "resolve-runtime.sh" in text


def _hook_scripts() -> list[Path]:
    names = [
        "session-machine", "session-conduct", "register-session",
        "deregister-session", "project-hooks", "provision-check",
        "anchor-hygiene-check", "bootstrap-check",
    ]
    out: list[Path] = []
    for n in names:
        for ext in (".sh", ".ps1"):
            p = _SCRIPTS / f"{n}{ext}"
            if p.is_file():
                out.append(p)
    return out


def test_hooks_resolve_via_marker_not_venv():
    """No hook resolves a python interpreter through a `.venv` link -- each
    sources the canonical marker resolver instead (#1106)."""
    for hook in _hook_scripts():
        text = hook.read_text(encoding="utf-8")
        assert not re.search(r"\.venv[/\\](bin|Scripts)", text), (
            f"{hook.name} still resolves python through a .venv link"
        )
        assert "resolve-runtime" in text, f"{hook.name} must source the resolver"


def test_binstubs_are_marker_only():
    """The launcher binstubs resolve via the marker with no `.venv` fallback."""
    for name in ("agent-worktrees", "agent-worktrees.ps1", "agent-worktrees.cmd",
                 "launch-session.sh", "launch-session.ps1", "launch-session.cmd"):
        p = _BIN / name
        if not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        assert "current-version" in text, f"{name} must resolve via the marker"
        assert not re.search(r"\.venv[/\\](bin|Scripts)", text), (
            f"{name} still resolves python through a .venv link"
        )
