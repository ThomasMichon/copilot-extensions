"""Source adopters must preserve both redirects when publication fails."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import shlex
import shutil
import sys

import pytest

from .test_source_runtime_install import REPOSITORY, _function, _ps, _receipt, _run, _small_stage
from .test_source_runtime_reselection import _powershell_functions


def _publish(tmp_path, plugin, *, host=None, locked=False, failed_stage=False):
    payload, fixtures = _small_stage(tmp_path, plugin)
    if failed_stage:
        core = payload / "scripts" / "versioned_runtime_source.py"
        core.write_text(core.read_text(encoding="utf-8") + """

if "publish-redirects" in sys.argv:
    _fixture_real_mkstemp = tempfile.mkstemp
    _fixture_second_marker_calls = 0

    def _fixture_readonly_stage(*args, **kwargs):
        global _fixture_second_marker_calls
        fd, path = _fixture_real_mkstemp(*args, **kwargs)
        if kwargs.get("prefix") == ".stamped-version.redirect-":
            _fixture_second_marker_calls += 1
            if _fixture_second_marker_calls == 2:
                os.close(fd)
                fd = os.open(path, os.O_RDONLY)
                print("STAGE_WRITE_FD_READ_ONLY=stamped-version", file=sys.stderr)
        return fd, path

    tempfile.mkstemp = _fixture_readonly_stage
""", encoding="utf-8")
    identity = _receipt(payload, plugin)
    root = tmp_path / "runtime"
    root.mkdir()
    previous = {"payload-dir": b"previous-payload\r\n", "stamped-version": b"previous-version\r\n"}
    for name, value in previous.items():
        (root / name).write_bytes(value)
    (root / ".payload-dir.redirect-unowned").write_bytes(b"not transaction-owned")
    env = fixtures["_isolated_install_env"](tmp_path / "home")
    if host:
        executable = shutil.which(host)
        if not executable:
            pytest.skip(f"{host} unavailable")
        functions = ""
        path = REPOSITORY / "plugins" / plugin / "scripts" / "install.ps1"
        names = ("Get-GitInfo",)
        if plugin == "agent-logger":
            names += ("Publish-PayloadSnapshot",)
        functions = _powershell_functions(
            plugin, names, path.stat().st_mtime_ns,
        ).replace("$PSScriptRoot", "$ScriptDir")
        body = f"""
$ErrorActionPreference = 'Stop'
$PluginDir = {_ps(payload)}
$ScriptDir = Join-Path $PluginDir 'scripts'
$InstallDir = {_ps(root)}
$SrcVersion = '1.2.3'
. (Join-Path $ScriptDir 'installer-engine.ps1')
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
. (Join-Path $ScriptDir 'source-runtime.ps1')
function Get-BootstrapPython {{ return {_ps(sys.executable)} }}
function Write-Ok($value) {{ Write-Host $value }}
{functions}
Initialize-SourceRuntime
$held = $null
$code = 0
try {{
    if (${str(locked).lower()}) {{
        $second = Join-Path $InstallDir 'stamped-version'
        $held = [IO.File]::Open($second, [IO.FileMode]::Open,
            [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {{
            $probe = [IO.File]::Open($second, [IO.FileMode]::Open,
                [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            $probe.Dispose()
            throw 'Second redirect unexpectedly writable'
        }} catch [IO.IOException] {{
            $nativeCode = $_.Exception.GetBaseException().HResult -band 65535
            if ($nativeCode -ne 32) {{ throw }}
            [Console]::Error.WriteLine('LOCKED_SECOND_MARKER=32')
        }}
    }}
    {'Publish-PayloadSnapshot | Out-Null' if plugin == 'agent-logger' else 'Publish-SourcePayload'}
    Write-Host 'REDIRECT_PUBLICATION_COMPLETE'
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    if ($held) {{
        foreach ($entry in @{{
            'payload-dir' = '{base64.b64encode(previous["payload-dir"]).decode()}'
            'stamped-version' = '{base64.b64encode(previous["stamped-version"]).decode()}'
        }}.GetEnumerator()) {{
            $actual = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $InstallDir $entry.Key)))
            if ($actual -ne $entry.Value) {{ throw "Redirect changed while second marker locked: $($entry.Key)" }}
        }}
        Write-Host 'PAIR_UNCHANGED_WHILE_LOCKED'
    }}
    $code = 19
}} finally {{
    if ($held) {{ $held.Dispose() }}
}}
if (${str(locked).lower()} -and $code -eq 19) {{
    try {{
        {'Publish-PayloadSnapshot | Out-Null' if plugin == 'agent-logger' else 'Publish-SourcePayload'}
        Write-Host 'REDIRECT_RETRY_COMPLETE'
    }} catch {{
        [Console]::Error.WriteLine($_.Exception.Message)
        $code = 21
    }}
}}
exit $code
"""
        script = tmp_path / "publish.ps1"
        argv = [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    else:
        executable = shutil.which("bash")
        if not executable:
            pytest.skip("native Bash unavailable")
        functions = _function(plugin, "_git_info", "sh")
        if plugin == "agent-logger":
            functions += "\n" + _function(plugin, "publish_payload_snapshot", "sh")
        body = f"""
set -euo pipefail
PLUGIN_DIR={shlex.quote(str(payload))}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
INSTALL_DIR={shlex.quote(str(root))}
SRC_VERSION=1.2.3
_bootstrap_python() {{ printf '%s' {shlex.quote(sys.executable)}; }}
_fail() {{ printf '%s\\n' "$*" >&2; }}
ok() {{ printf '%s\\n' "$*"; }}
. "$SCRIPT_DIR/source-runtime.sh"
{functions}
_initialize_source_runtime
if {'publish_payload_snapshot' if plugin == 'agent-logger' else '_stamp_source_payload'}; then
    printf '%s\\n' REDIRECT_PUBLICATION_COMPLETE
else
    exit 19
fi
"""
        script = tmp_path / "publish.sh"
        argv = [executable, str(script)]
    script.write_text(body, encoding="utf-8", newline="\n")
    result = _run(argv, env=env, cwd=tmp_path)
    return result, root, previous, identity


def _assert_owned_temporary_cleanup(root):
    assert {path.name for path in root.glob(".*.redirect-*")} == {".payload-dir.redirect-unowned"}
    assert (root / ".payload-dir.redirect-unowned").read_bytes() == b"not transaction-owned"


@pytest.mark.skipif(os.name != "nt", reason="real Windows marker sharing boundary")
@pytest.mark.parametrize("plugin", ["agent-logger", "agent-vault"])
@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_locked_second_source_redirect_preserves_both_previous_files(tmp_path, plugin, host):
    result, root, previous, identity = _publish(tmp_path, plugin, host=host, locked=True)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "LOCKED_SECOND_MARKER=32" in result.stderr
    assert "Source redirect publication failed" in result.stderr
    assert "[WinError 5]" in result.stderr or "[WinError 32]" in result.stderr
    assert " -> " + repr(str(root / "stamped-version")) in result.stderr
    assert "REDIRECT_PUBLICATION_COMPLETE" not in result.stdout
    assert "PAIR_UNCHANGED_WHILE_LOCKED" in result.stdout
    assert "REDIRECT_RETRY_COMPLETE" in result.stdout
    assert (root / "stamped-version").read_text().strip() == identity.runtime_version
    assert Path((root / "payload-dir").read_text().strip()).is_dir()
    _assert_owned_temporary_cleanup(root)


@pytest.mark.parametrize("plugin", ["agent-logger", "agent-vault"])
@pytest.mark.parametrize("host", ["pwsh", "powershell"] if os.name == "nt" else [None])
def test_failed_second_source_redirect_stage_write_preserves_both_previous_files(tmp_path, plugin, host):
    result, root, previous, _ = _publish(tmp_path, plugin, host=host, failed_stage=True)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "STAGE_WRITE_FD_READ_ONLY=stamped-version" in result.stderr
    assert "[Errno 9]" in result.stderr
    assert "REDIRECT_PUBLICATION_COMPLETE" not in result.stdout
    assert {name: (root / name).read_bytes() for name in previous} == previous
    _assert_owned_temporary_cleanup(root)


@pytest.mark.parametrize("plugin", ["agent-logger", "agent-vault"])
@pytest.mark.parametrize("host", ["pwsh", "powershell"] if os.name == "nt" else [None])
def test_source_redirect_pair_healthy_publication(tmp_path, plugin, host):
    result, root, previous, identity = _publish(tmp_path, plugin, host=host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REDIRECT_PUBLICATION_COMPLETE" in result.stdout
    assert (root / "stamped-version").read_text().strip() == identity.runtime_version
    snapshot = Path((root / "payload-dir").read_text().strip())
    assert snapshot.is_dir()
    assert (snapshot / "runtime-source.json").read_bytes()
    _assert_owned_temporary_cleanup(root)
