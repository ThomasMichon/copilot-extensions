"""Tests for the junction-free, marker-only runtime resolution (#1106).

Every runtime-resolution flow resolves the active slot python via the
`current-version` marker -> versions/<ver>, never through a `.venv` link. These
tests cover the shared resolver helpers' presence + deployment, and that no
runtime-resolution flow still reads a `.venv` link.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
def test_powershell_resolver_exports_payload_invocation_contract(tmp_path):
    runtime = tmp_path / ".agent-worktrees"
    slot_python = runtime / "versions" / "1.2.3" / "Scripts" / "python.exe"
    slot_python.parent.mkdir(parents=True)
    slot_python.touch()
    (slot_python.parents[1] / ".install-complete.json").write_text(
        json.dumps({"version": "1.2.3"}), encoding="utf-8"
    )
    (runtime / "current-version").write_text("1.2.3\n", encoding="utf-8")
    resolver = _SCRIPTS / "resolve-runtime.ps1"
    home_literal = str(tmp_path).replace("'", "''")
    resolver_literal = str(resolver).replace("'", "''")
    script = (
        f"$env:USERPROFILE = '{home_literal}'; "
        f". '{resolver_literal}'; "
        "[pscustomobject]@{ AwPy = $AwPy; AgentRtPy = $AgentRtPy } "
        "| ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    resolved = json.loads(result.stdout)
    assert Path(resolved["AwPy"]) == slot_python
    assert resolved["AgentRtPy"] == resolved["AwPy"]


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
        "anchor-hygiene-check", "marketplace-overrides", "bootstrap-check",
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


# -- last-known-good fallback (#742) ------------------------------------------

def test_resolvers_reference_last_known_good():
    """Both resolvers implement the tier-2 last-known-good fallback."""
    for name in ("resolve-runtime.sh", "resolve-runtime.ps1"):
        text = (_SCRIPTS / name).read_text(encoding="utf-8")
        assert "last-known-good" in text, (
            f"{name} must prefer last-known-good over a newest-slot guess (#742)"
        )


@pytest.mark.parametrize("installer", ["install.sh", "install.ps1"])
def test_installer_records_last_known_good(installer: str):
    """The installer stamps last-known-good when it activates a slot (#742)."""
    text = (_SCRIPTS / installer).read_text(encoding="utf-8")
    assert "last-known-good" in text, (
        f"{installer} must record last-known-good on activate (#742)"
    )


def _make_slot(root: Path, version: str) -> None:
    slot = root / ".agent-worktrees" / "versions" / version
    (slot / "bin").mkdir(parents=True, exist_ok=True)
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": version}), encoding="utf-8"
    )
    py = slot / "bin" / "python"
    py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    py.chmod(0o755)


def _resolve(home: Path) -> str:
    """Source resolve-runtime.sh under a fabricated HOME; return AW_PY."""
    import subprocess

    resolver = _SCRIPTS / "resolve-runtime.sh"
    script = f'. "{resolver}"; printf "%s" "$AW_PY"'
    out = subprocess.run(
        ["sh", "-c", script],
        capture_output=True, text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
    )
    assert out.returncode == 0, (
        f"resolver exited {out.returncode}: {out.stderr.strip()}"
    )
    return out.stdout.strip()


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_rejects_incomplete_marker_slot(tmp_path):
    _make_slot(tmp_path, "1.2.3")
    runtime = tmp_path / ".agent-worktrees"
    (runtime / "versions" / "1.2.3" / ".install-complete.json").unlink()
    (runtime / "current-version").write_text("1.2.3\n", encoding="utf-8")

    assert _resolve(tmp_path) == ""


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_posix_resolver_exports_payload_invocation_contract(tmp_path):
    _make_slot(tmp_path, "1.2.3")
    runtime = tmp_path / ".agent-worktrees"
    (runtime / "current-version").write_text("1.2.3\n", encoding="utf-8")
    resolver = _SCRIPTS / "resolve-runtime.sh"
    script = (
        f'. "{resolver}"; '
        'printf "%s\\n%s\\n" "$AW_PY" "$AGENT_RT_PY"'
    )
    result = subprocess.run(
        ["sh", "-c", script],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    aw_py, agent_rt_py = result.stdout.splitlines()
    assert Path(aw_py).name == "python"
    assert agent_rt_py == aw_py


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_tier1_prefers_current_version_marker(tmp_path):
    aw = tmp_path / ".agent-worktrees"
    _make_slot(tmp_path, "0.1.0")
    _make_slot(tmp_path, "0.2.0")
    (aw).mkdir(parents=True, exist_ok=True)
    (aw / "current-version").write_text("0.1.0\n", encoding="utf-8")
    assert _resolve(tmp_path).endswith("versions/0.1.0/bin/python")


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_tier2_prefers_last_known_good_over_newest(tmp_path):
    # No marker; last-known-good names the older slot. It must win over the
    # newest-slot guess -- the core #742 behavior.
    aw = tmp_path / ".agent-worktrees"
    _make_slot(tmp_path, "0.1.0")
    _make_slot(tmp_path, "0.2.0")
    aw.mkdir(parents=True, exist_ok=True)
    (aw / "last-known-good").write_text("0.1.0\n", encoding="utf-8")
    assert _resolve(tmp_path).endswith("versions/0.1.0/bin/python")


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_stale_marker_falls_to_last_known_good(tmp_path):
    # Marker names a slot that is gone; last-known-good (an installed slot) is
    # preferred over guessing the newest slot.
    aw = tmp_path / ".agent-worktrees"
    _make_slot(tmp_path, "0.1.0")
    _make_slot(tmp_path, "0.2.0")
    aw.mkdir(parents=True, exist_ok=True)
    (aw / "current-version").write_text("9.9.9\n", encoding="utf-8")
    (aw / "last-known-good").write_text("0.1.0\n", encoding="utf-8")
    assert _resolve(tmp_path).endswith("versions/0.1.0/bin/python")


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_tier3_newest_on_true_first_run(tmp_path):
    # No marker and no last-known-good -> newest installed slot.
    _make_slot(tmp_path, "0.1.0-dev9")
    _make_slot(tmp_path, "0.1.0-dev10")
    assert _resolve(tmp_path).endswith("versions/0.1.0-dev10/bin/python")
    _make_slot(tmp_path, "0.1.0")
    assert _resolve(tmp_path).endswith("versions/0.1.0/bin/python")


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh binstub")
def test_global_binstub_tier3_prefers_dev10_over_dev9(tmp_path):
    import os
    import subprocess

    runtime = tmp_path / ".agent-worktrees"
    for version in ("1.5.3-dev9", "1.5.3-dev10"):
        slot = runtime / "versions" / version
        command = slot / "bin" / "agent-worktrees"
        command.parent.mkdir(parents=True)
        command.write_text(
            f"#!/bin/sh\nprintf '%s' '{version}'\n", encoding="utf-8"
        )
        command.chmod(0o755)
        (slot / ".install-complete.json").write_text(
            json.dumps({"version": version}), encoding="utf-8"
        )
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)

    result = subprocess.run(
        [str(_BIN / "agent-worktrees"), "status"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "1.5.3-dev10"

    slot = runtime / "versions" / "1.5.3"
    command = slot / "bin" / "agent-worktrees"
    command.parent.mkdir(parents=True)
    command.write_text("#!/bin/sh\nprintf '%s' '1.5.3'\n", encoding="utf-8")
    command.chmod(0o755)
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": "1.5.3"}), encoding="utf-8"
    )
    result = subprocess.run(
        [str(_BIN / "agent-worktrees"), "status"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1.5.3"


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX sh resolver")
def test_resolver_empty_when_nothing_installed(tmp_path):
    assert _resolve(tmp_path) == ""
