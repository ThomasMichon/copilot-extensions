"""dtssh installers keep devtunnel off the WinGet Links reparse point.

On hardened Windows hosts, RedirectionGuard refuses to start a user-created
symlink such as ``%LOCALAPPDATA%\\Microsoft\\WinGet\\Links\\devtunnel.exe`` from
a non-interactive SSH or service logon ("untrusted mount point"), so every
``ssh <alias>`` ProxyCommand fails. Both installers copy the real binary beside
dtssh.exe. The function is extracted verbatim from each script and run under
PowerShell against a temporary PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "transports" / "dtssh" / "scripts"
PWSH = shutil.which("pwsh")

pytestmark = [
    pytest.mark.skipif(PWSH is None, reason="PowerShell 7 (pwsh) is not available"),
    pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point behavior"),
]


def _function(script: str) -> str:
    text = (SCRIPTS / script).read_text(encoding="utf-8-sig")
    start = text.index("function Install-ShimFreeDevTunnel {")
    end = text.index("\nfunction ", start + 1)
    return text[start:end]


def _layout(tmp_path: Path, *, link: bool) -> tuple[Path, Path, Path]:
    package = tmp_path / "Packages" / "Microsoft.devtunnel"
    package.mkdir(parents=True)
    real = package / "devtunnel.exe"
    real.write_bytes(b"real-devtunnel-v2")
    links = tmp_path / "Links"
    links.mkdir()
    if link:
        try:
            os.symlink(real, links / "devtunnel.exe")
        except OSError as exc:  # symlink creation needs Developer Mode or elevation
            pytest.skip(f"cannot create a file symlink here: {exc}")
    else:
        shutil.copyfile(real, links / "devtunnel.exe")
    return real, links, tmp_path / "dtssh" / "bin"


def _run(script: str, bin_dir: Path, links: Path) -> str:
    body = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$env:PATH = '{bin_dir};{links}'\n"
        + _function(script)
        + f"\n$r = Install-ShimFreeDevTunnel -BinDir '{bin_dir}' 6>$null\n"
        "Write-Output \"result:$r\"\n"
    )
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", body],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return next(line for line in proc.stdout.splitlines() if line.startswith("result:")).removeprefix("result:")


@pytest.mark.parametrize("script", ["install-client.ps1", "install-host.ps1"])
def test_link_only_devtunnel_is_copied_beside_dtssh(tmp_path, script):
    real, links, bin_dir = _layout(tmp_path, link=True)
    result = _run(script, bin_dir, links)
    sibling = bin_dir / "devtunnel.exe"
    assert result == str(sibling)
    assert sibling.read_bytes() == real.read_bytes()
    assert not os.path.islink(sibling)


@pytest.mark.parametrize("script", ["install-client.ps1", "install-host.ps1"])
def test_stale_sibling_is_refreshed_from_the_link_target(tmp_path, script):
    real, links, bin_dir = _layout(tmp_path, link=True)
    bin_dir.mkdir(parents=True)
    (bin_dir / "devtunnel.exe").write_bytes(b"real-devtunnel-v1")
    assert _run(script, bin_dir, links) == str(bin_dir / "devtunnel.exe")
    assert (bin_dir / "devtunnel.exe").read_bytes() == real.read_bytes()


@pytest.mark.parametrize("script", ["install-client.ps1", "install-host.ps1"])
def test_real_devtunnel_on_path_is_left_alone(tmp_path, script):
    _, links, bin_dir = _layout(tmp_path, link=False)
    assert _run(script, bin_dir, links) == ""
    assert not (bin_dir / "devtunnel.exe").exists()
