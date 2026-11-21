"""Execute the launcher's PSMux client selection without starting a real mux."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"
PWSH: str | None = shutil.which("pwsh")


def _launcher_functions(*names: str) -> str:
    source = SOURCE.read_text("utf-8")
    helpers = []
    for name in names:
        start = source.index("function " + name + " {")
        helpers.append(source[start:source.index("\n}\n", start) + 3])
    return "\n".join(helpers)


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows PowerShell")
@pytest.mark.parametrize("path_compatible", [True, False])
def test_launcher_keeps_compatible_path_client_and_puts_selection_first(
    tmp_path: Path, path_compatible: bool,
) -> None:
    assert PWSH is not None
    old_dir = tmp_path / "old"
    side_root = tmp_path / "side"
    side_dir = side_root / "3.4.0"
    other_dir = tmp_path / "other"
    for directory in (old_dir, side_dir, other_dir):
        directory.mkdir(parents=True)
        (directory / "psmux.exe").write_bytes(b"fixture")
    # The real helper reads PE metadata; this stub isolates the launcher's
    # selection order from the binaries installed on the host.
    (tmp_path / "psmux-path.ps1").write_text(
        "function Test-AwPsmuxVersionCompatible { param($Version,$MinimumVersion) "
        "$global:PathCompatible }\n"
        "function Get-AwPsmuxSideBySideRoot { $global:SideRoot }\n"
        "function Find-AwCompatiblePsmuxPackageBinary { param($PackageRoot,$SideBySideRoot) "
        "[pscustomobject]@{ Path = (Join-Path $SideBySideRoot '3.4.0\\psmux.exe') } }\n",
        encoding="utf-8",
    )
    script = tmp_path / "resolve.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        + _launcher_functions(
            "Resolve-AwPsmuxBin", "Join-AwPsmuxPathFirst", "Set-AwPsmuxProcessPathFirst",
        ) + "\n"
        f"$global:PathCompatible = ${str(path_compatible).lower()}\n"
        f"$global:SideRoot = '{side_root}'\n"
        f"$env:PATH = '{other_dir};{old_dir};{side_dir}\\'\n"
        f"$cmd = [pscustomobject]@{{ Source = '{old_dir / 'psmux.exe'}' }}\n"
        "$bin = Resolve-AwPsmuxBin $cmd\n"
        "if ($bin -ne $cmd.Source) { Set-AwPsmuxProcessPathFirst $bin }\n"
        "@{ bin = $bin; path = $env:PATH } | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout.strip().splitlines()[-1])
    if path_compatible:
        assert observed == {
            "bin": str(old_dir / "psmux.exe"),
            "path": f"{other_dir};{old_dir};{side_dir}\\",
        }
    else:
        assert observed == {
            "bin": str(side_dir / "psmux.exe"),
            "path": f"{side_dir};{other_dir};{old_dir}",
        }


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows PowerShell")
def test_launcher_replaces_compatible_link_stub_with_its_target(tmp_path: Path) -> None:
    assert PWSH is not None
    package_dir = tmp_path / "package"
    links_dir = tmp_path / "links"
    for directory in (package_dir, links_dir):
        directory.mkdir()
    real = package_dir / "psmux.exe"
    real.write_bytes(b"fixture")
    stub = links_dir / "psmux.exe"
    try:
        stub.symlink_to(real)
    except OSError:
        pytest.skip("symbolic links need Developer Mode or elevation")
    (tmp_path / "psmux-path.ps1").write_text(
        "function Test-AwPsmuxVersionCompatible { param($Version,$MinimumVersion) $true }\n"
        "function Get-AwPsmuxSideBySideRoot { 'unused' }\n"
        "function Find-AwCompatiblePsmuxPackageBinary { throw 'not expected' }\n",
        encoding="utf-8",
    )
    script = tmp_path / "resolve.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        + _launcher_functions("Resolve-AwPsmuxBin") + "\n"
        f"$cmd = [pscustomobject]@{{ Source = '{stub}' }}\n"
        "Resolve-AwPsmuxBin $cmd\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == str(real)


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows PowerShell")
@pytest.mark.parametrize("selected", [True, False])
def test_launcher_puts_selected_client_first_in_a_plan_path(
    tmp_path: Path, selected: bool,
) -> None:
    assert PWSH is not None
    side_dir = tmp_path / "side" / "3.4.0"
    old_dir = tmp_path / "old"
    binary = str(side_dir / "psmux.exe") if selected else ""
    script = tmp_path / "plan.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        + _launcher_functions("Join-AwPsmuxPathFirst", "Set-AwPsmuxPlanPathFirst") + "\n"
        "$plan = [ordered]@{}\n"
        f"$plan['Path'] = '{old_dir};{side_dir}'\n"
        "$plan['OTHER'] = 'kept'\n"
        f"Set-AwPsmuxPlanPathFirst $plan '{binary}'\n"
        "$plan | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout.strip().splitlines()[-1])
    expected_path = f"{side_dir};{old_dir}" if selected else f"{old_dir};{side_dir}"
    assert observed == {"Path": expected_path, "OTHER": "kept"}
