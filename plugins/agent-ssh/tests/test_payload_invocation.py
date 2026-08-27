"""Payload-local invocation tests for agent-ssh compatibility wrappers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_compatibility_wrappers_do_not_reference_global_binstub() -> None:
    for name in ("emit-profile.sh", "verify.sh", "emit-profile.ps1", "verify.ps1"):
        text = (PLUGIN / "scripts" / name).read_text(encoding="utf-8")
        assert ".local/bin" not in text
        assert r".local\bin" not in text
        assert "bin/agent-ssh" in text or r"bin\agent-ssh.ps1" in text


@pytest.mark.skipif(os.name == "nt", reason="POSIX wrapper test")
@pytest.mark.parametrize(
    ("script_name", "subcommand"),
    (("emit-profile.sh", "emit-profile"), ("verify.sh", "verify")),
)
def test_posix_compatibility_wrapper_uses_own_payload_command(
    tmp_path: Path,
    script_name: str,
    subcommand: str,
) -> None:
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    bin_dir = payload / "bin"
    scripts.mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(PLUGIN / "scripts" / script_name, scripts / script_name)

    marker = tmp_path / "payload-called"
    payload_command = bin_dir / "agent-ssh"
    payload_command.write_text(
        f'#!/bin/sh\nprintf "%s" "$*" > "{marker}"\n',
        encoding="utf-8",
    )
    payload_command.chmod(0o755)

    home = tmp_path / "home"
    global_bin = home / ".local" / "bin"
    global_bin.mkdir(parents=True)
    shadow_marker = tmp_path / "global-called"
    shadow = global_bin / "agent-ssh"
    shadow.write_text(
        f'#!/bin/sh\nprintf called > "{shadow_marker}"\nexit 99\n',
        encoding="utf-8",
    )
    shadow.chmod(0o755)

    result = subprocess.run(
        ["bash", str(scripts / script_name), "two words"],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == f"{subcommand} two words"
    assert not shadow_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows wrapper test")
@pytest.mark.parametrize(
    ("script_name", "subcommand"),
    (("emit-profile.ps1", "emit-profile"), ("verify.ps1", "verify")),
)
def test_windows_compatibility_wrapper_uses_own_payload_command(
    tmp_path: Path,
    script_name: str,
    subcommand: str,
) -> None:
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    bin_dir = payload / "bin"
    scripts.mkdir(parents=True)
    bin_dir.mkdir()
    shutil.copy2(PLUGIN / "scripts" / script_name, scripts / script_name)

    marker = tmp_path / "payload-called"
    (bin_dir / "agent-ssh.ps1").write_text(
        f"[IO.File]::WriteAllText('{marker}', ($args -join ' '))\n",
        encoding="utf-8",
    )

    home = tmp_path / "home"
    global_bin = home / ".local" / "bin"
    global_bin.mkdir(parents=True)
    shadow_marker = tmp_path / "global-called"
    (global_bin / "agent-ssh.ps1").write_text(
        f"[IO.File]::WriteAllText('{shadow_marker}', 'called')\nexit 99\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "pwsh",
            "-NoProfile",
            "-File",
            str(scripts / script_name),
            "two words",
        ],
        env={**os.environ, "USERPROFILE": str(home)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == f"{subcommand} two words"
    assert not shadow_marker.exists()
