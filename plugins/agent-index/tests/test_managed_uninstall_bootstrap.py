"""Prepared external helpers retain uninstall's interpreter-closure boundary."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

import pytest
from agent_procutil import no_window_kwargs

from test_install_engine_adoption import function, stage_index_installer
from test_managed_bootstrap_reader_ps1 import quote, reader_environment, run, shell
from test_managed_bootstrap_reader_sh import _environment
from test_managed_bootstrap_reader_sh import prepared_helper as prepared_helper


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("layout", ["raw", "packaged"])
def test_finite_uninstall_uses_addressed_engine(
    tmp_path: Path, host: str, layout: str, prepared_helper: tuple[Path, Path, Path],
) -> None:
    executable = shell(host)
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=layout == "packaged",
    )
    canonical = payload.parents[1] / "libs/installer-engine"
    support = payload / "scripts" if layout == "packaged" else canonical
    _, python, _ = prepared_helper
    addressed = support / "installer_engine_uninstall.py"
    unused = (
        canonical if layout == "packaged" else payload / "scripts"
    ) / "installer_engine_uninstall.py"
    marker = tmp_path / "stale-helper-ran"
    addressed.write_text(
        "import json,sys\nprint(json.dumps({'helper':__file__,'args':sys.argv[1:],"
        "'isolated':sys.flags.isolated,'bytecode':sys.dont_write_bytecode}))\n",
        encoding="utf-8",
    )
    unused.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('stale')\n"
        "raise SystemExit(97)\n", encoding="utf-8",
    )
    selected = function("Invoke-IndexFiniteUninstall", "ps1").replace(
        "$PSScriptRoot", quote(payload / "scripts"),
    )
    body = re.search(r"(?ms)^function Invoke-IndexFiniteUninstall\b.*?^\}", selected)
    assert body
    selected = selected[:body.end()]
    root = tmp_path / "runtime"
    harness = tmp_path / "addressed-uninstall.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {quote(support / 'installer-engine.ps1')}\n"
        "function Get-FiniteUninstallPython { param($InstallRoot)\n"
        f"return {quote(python)}\n}}\n"
        + selected + "\n"
        + f"Invoke-IndexFiniteUninstall -Root {quote(root)} -Mode '--check'\n",
        encoding="utf-8-sig",
    )
    environment = reader_environment(tmp_path, prepared_helper, "managed")
    result = run(executable, harness, environment, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout)
    assert Path(observed["helper"]) == addressed
    assert observed["isolated"] == 1 and observed["bytecode"] is True
    assert observed["args"][:6] == [
        "--payload-root", str(payload), "--plugin-id", "agent-index", "--root", str(root),
    ]
    assert observed["args"][-1] == "--check"
    assert not marker.exists() and not root.exists()


@pytest.mark.parametrize("host", ["bash", "pwsh", "powershell.exe"])
@pytest.mark.parametrize("layout", ["raw", "packaged"])
@pytest.mark.parametrize("scenario", ["prepared", "unprepared", "missing-uv", "inside-root"])
def test_uninstall_external_helper_requires_prepared_managed_python(
    tmp_path: Path, host: str, layout: str, scenario: str,
    prepared_helper: tuple[Path, Path, Path],
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    if host == "bash" and os.name == "nt":
        pytest.skip("Whole POSIX paths require native POSIX")
    executable = shell(host)
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=layout == "packaged",
    )
    support = payload / "scripts" if layout == "packaged" else payload.parents[1] / "libs/installer-engine"
    uv, helper, _store = prepared_helper
    root = helper.parent if scenario == "inside-root" else tmp_path / "uninstalled root"
    if host == "bash":
        env = _environment(tmp_path, prepared_helper, scenario)
        harness = tmp_path / "uninstall-reader.sh"
        harness.write_text(
            "set -eu\n"
            f". {shlex.quote(str(support / 'installer-engine.sh'))}\n"
            + function("_bootstrap_python", "sh") + "\n"
            + function("_uninstall_external_python", "sh") + "\n"
            + f"root={shlex.quote(str(root))}\n"
            + "for cycle in 1 2; do\n"
            + ' if selected="$(_uninstall_external_python "$root")"; then\n'
            + '  printf "SELECTED:%s\\n" "$selected"\n'
            + " else printf 'REFUSED\\n'; fi\n"
            + "done\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [executable, str(harness)], env=env, cwd=tmp_path,
            capture_output=True, text=True, timeout=20, **no_window_kwargs(),
        )
        call_log = Path(env["UV_PROBE_LOG"])
        calls = call_log.read_text(encoding="utf-8").splitlines() if call_log.exists() else []
        assert not Path(env["AMBIENT_MARKER"]).exists()
    else:
        env = reader_environment(tmp_path, prepared_helper, scenario)
        commands = tmp_path / "empty-path"
        commands.mkdir()
        env["PATH"] = str(commands)
        functions = "\n".join(function(name, "ps1") for name in (
            "Get-BootstrapPython", "Get-FiniteUninstallPython",
        )).replace("$PSScriptRoot", quote(payload / "scripts"))
        harness = tmp_path / "uninstall-reader.ps1"
        harness.write_text(f"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
. {quote(support / 'installer-engine.ps1')}
. {quote(payload / 'scripts/versioned_runtime_native.ps1')}
{functions}
$Action='uninstall'
$InstallDir=if ({quote(scenario)} -eq 'prepared') {{ '' }} else {{ {quote(root)} }}
Set-Location {quote(helper.parent)}
$WheelProfile='not-used-for-removal';$WheelPython='unprepared-wheel-python'
function Get-Command {{
    param($Name,$CommandType,$ErrorAction)
    if ($Name -ne 'uv') {{ throw "ambient Python discovery: $Name" }}
    if ({quote(scenario)} -ne 'missing-uv') {{ [pscustomobject]@{{Source={quote(uv)}}} }}
}}
$calls=New-Object 'System.Collections.Generic.List[string]'
Rename-Item Function:\\Invoke-Hidden Invoke-RealHidden
function Invoke-Hidden {{
    param($FilePath,$ArgList,[hashtable]$Environment,[ref]$ErrorOutput,[Text.Encoding]$CaptureEncoding)
    if ($FilePath -eq {quote(uv)}) {{ $calls.Add(($ArgList -join ' ')) }}
    return Invoke-RealHidden @PSBoundParameters
}}
for ($cycle=0;$cycle -lt 2;$cycle++) {{
    $selected=Get-FiniteUninstallPython -InstallRoot {quote(root)}
    if ($selected) {{ Write-Output "SELECTED:$selected" }} else {{ Write-Output 'REFUSED' }}
}}
if ({quote(scenario)} -eq 'prepared' -and $InstallDir -cne '') {{
    throw 'External helper selection mutated the caller install root'
}}
@{{calls=@($calls.ToArray())}} | ConvertTo-Json -Compress
""", encoding="utf-8-sig")
        result = run(executable, harness, env, encoding="utf-8")
        lines = result.stdout.splitlines()
        assert lines, result.stderr
        calls = json.loads(lines[-1])["calls"]
    assert result.returncode == 0, result.stdout + result.stderr
    records = result.stdout.splitlines()
    if host != "bash":
        records = records[:-1]
    assert records == (
        [f"SELECTED:{helper}"] * 2 if scenario == "prepared" else ["REFUSED"] * 2
    ), result.stdout + result.stderr
    assert calls == (
        [] if scenario == "missing-uv" else [
            "--version",
            "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18",
        ] * 2
    ), calls
    assert not (tmp_path / "uninstalled root").exists()
    assert not list((tmp_path / "unprepared-python").iterdir())
    record_testsuite_property("index_external_uninstall", json.dumps({
        "host": host, "layout": layout, "scenario": scenario,
        "cycles": 2, "uv_calls": len(calls), "network": "disabled",
        "removal": "not-invoked",
    }))
