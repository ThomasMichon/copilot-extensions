"""Retained native compatibility helpers; the current installer owner is Python."""
from __future__ import annotations

import json
import os
import sys
import venv
from pathlib import Path

import pytest

from test_installer_completion_publication import (
    SCRIPTS,
    SHELLS,
    _bash_path,
    _copy_helper,
    _function_body,
    _ps_function,
    _ps_quote,
    _run,
)

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("shell,executable", SHELLS)
@pytest.mark.parametrize("case", [
    "healthy", "failed-health", "missing-resolver", "resolver-copy-failure",
    "wrapper-copy-failure", "competing-activation", "monitor-failed", "monitor-exception",
])
def test_native_publication_follows_health_and_precedes_monitor(
    tmp_path: Path, shell: str, executable: str | None, case: str,
) -> None:
    if not executable:
        pytest.skip(f"{shell} unavailable")
    _copy_helper(tmp_path, shell)
    root = tmp_path / "runtime"
    slot = root / "versions" / "1.2.3"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(slot)
    site = slot / "Lib" / "site-packages" if os.name == "nt" else (
        slot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    )
    package = site / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    events = tmp_path / "events"
    redirect = tmp_path / "redirect"
    redirect.write_text("previous", encoding="utf-8")
    manifest = root / "deploy-manifest.json"
    manifest.write_text('{"selected":"previous"}', encoding="utf-8")
    manifest_before = manifest.read_bytes()
    (package / "__main__.py").write_text(
        "from pathlib import Path\n"
        "def _load_full_command_surface():\n"
        f"    with Path({str(events)!r}).open('a') as out: out.write('health\\n')\n"
        + ("    raise RuntimeError('fixture import failure')\n" if case == "failed-health" else ""),
        encoding="utf-8",
    )
    (package / "status_monitor_cutover.py").write_text(
        "from pathlib import Path\n"
        "def monitor_live_now(): return False\n"
        "def installer_after_update():\n"
        f"    assert Path({str(root / 'current-version')!r}).read_text().strip() == '1.2.3'\n"
        f"    published = Path({str(redirect)!r}).read_text()\n"
        "    assert published in ('wrappers', 'resolvers')\n"
        f"    with Path({str(events)!r}).open('a') as out: out.write('monitor:' + published + '\\n')\n"
        + ("    raise RuntimeError('fixture monitor failure')\n" if case == "monitor-exception"
           else "    return 7\n" if case == "monitor-failed" else "    return 0\n"),
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.2.3", "completed_at": "2026-01-01T00:00:00Z", "pid": 0,
    }), encoding="utf-8")
    completed_before = (slot / ".install-complete.json").read_bytes()
    for name in ("current-version", "last-known-good"):
        (root / name).write_text("1.2.2\n", encoding="utf-8")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    failure = case not in {"healthy", "failed-health", "monitor-failed", "monitor-exception"}
    mode = "wrappers" if case in {"missing-resolver", "wrapper-copy-failure"} else "resolvers"
    if failure:
        (root / "versions" / "1.2.4").mkdir()
        (tmp_path / "resolve-runtime.ps1").write_text("# fixture resolver\n", encoding="utf-8")
        if case != "missing-resolver":
            (tmp_path / "resolve-runtime.sh").write_text("# fixture resolver\n", encoding="utf-8")
        (tmp_path / "session-conduct.ps1").write_text("# fixture hook\n", encoding="utf-8")
    if shell == "bash":
        text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
        functions = "\n".join(
            _function_body(text, name) + "\n}\n"
            for name in ("_versioned_activate_inner", "_versioned_activate")
        )
        script = f"""
set -euo pipefail
SCRIPT_DIR='{_bash_path(tmp_path)}'
INSTALL_DIR='{_bash_path(root)}'
VENV_DIR='{_bash_path(slot)}'
VENV_PYTHON='{_bash_path(python)}'
SRC_VERSION=1.2.3
VERSIONED_RUNTIME=1
CONTEXTUAL_INSTALL=false
record() {{ printf '%s\\n' "$1" >> '{_bash_path(events)}'; }}
err() {{ printf '%s\\n' "$*" >&2; }}
ok() {{ :; }}
_versioned_mark_complete() {{ record complete; }}
_release_versioned_slot_lease() {{ record released; }}
publish() {{
    [[ "$(cat "$INSTALL_DIR/current-version")" == "1.2.2" ]] || return 1
    record "$1"
    printf '%s' "$1" > '{_bash_path(redirect)}'
}}
deploy_wrappers() {{ publish wrappers; }}
deploy_runtime_resolvers() {{ publish resolvers; }}
{functions}
"""
        if failure:
            script += "\n".join(
                _function_body(text, name) + "\n}\n"
                for name in (
                    "_runtime_file_id", "_runtime_file_state", "_publish_runtime_file",
                    "deploy_runtime_resolvers", "deploy_wrappers",
                )
            )
            script += f"\nBIN_DIR='{_bash_path(root / 'bin')}'\n"
            if case != "missing-resolver":
                fail_source = (
                    "session-conduct.ps1" if case == "wrapper-copy-failure"
                    else "resolve-runtime.sh"
                )
                peer = (
                    f'"{_bash_path(python)}" "{_bash_path(tmp_path / "versioned_runtime.py")}" '
                    '--root "$INSTALL_DIR" activate 1.2.4 --no-link || return 1\n'
                    if case == "competing-activation" else ""
                )
                script += f"""
cp() {{
    if [[ "$1" == "$SCRIPT_DIR/{fail_source}" ]]; then
        {peer}
        printf 'Injected helper copy failure\\n' >&2
        return 42
    fi
    command cp "$@"
}}
"""
            script += f"\n_versioned_activate {mode} || exit 1\n"
        else:
            script += """
_versioned_activate wrappers || exit 1
printf '1.2.2\\n' > "$INSTALL_DIR/current-version"
_versioned_activate resolvers || exit 1
"""
    else:
        text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
        functions = "\n".join(
            _ps_function(text, name)
            for name in ("Invoke-VersionedActivateInner", "Invoke-VersionedActivate")
        )
        script = f"""
$ErrorActionPreference = 'Stop'
$ScriptDir = {_ps_quote(tmp_path)}
$InstallDir = {_ps_quote(root)}
$VenvDir = {_ps_quote(slot)}
$VenvPython = {_ps_quote(python)}
$LinkPython = $VenvPython
$SrcVersion = '1.2.3'
$VersionedRuntime = $true
$ContextualInstall = $false
function Record($Value) {{ [IO.File]::AppendAllText({_ps_quote(events)}, "$Value`n") }}
function Write-ServiceErr($Value) {{ [Console]::Error.WriteLine($Value) }}
function Write-ServiceChanged {{ }}
function Write-ServiceOk {{ }}
function Invoke-VersionedMarkComplete {{ Record complete; return $true }}
function Exit-VersionedSlotLease {{ Record released }}
function Publish($Value) {{
    if ((Get-Content (Join-Path $InstallDir 'current-version') -Raw).Trim() -cne '1.2.2') {{
        throw 'Runtime selection preceded publication'
    }}
    Record $Value
    [IO.File]::WriteAllText({_ps_quote(redirect)}, $Value)
    return $true
}}
function Deploy-Wrappers {{ Publish wrappers }}
function Deploy-RuntimeResolvers {{ Publish resolvers }}
{functions}
"""
        if failure:
            publisher = _ps_function(text, "Publish-RuntimeFile")
            if case != "missing-resolver":
                fail_source = (
                    "session-conduct.ps1" if case == "wrapper-copy-failure"
                    else "resolve-runtime.sh"
                )
                peer = (
                    f"& {_ps_quote(python)} {_ps_quote(tmp_path / 'versioned_runtime.py')} "
                    "--root $InstallDir activate 1.2.4 --no-link\n"
                    "        if ($LASTEXITCODE -ne 0) { throw 'Competing activation failed' }\n"
                    if case == "competing-activation" else ""
                )
                copy = "        [IO.File]::Copy($Source, $tmp, $false)"
                assert publisher.count(copy) == 1
                publisher = publisher.replace(copy, f"""
        if ((Split-Path -Leaf $Source) -eq '{fail_source}') {{
            {peer}
            throw 'Injected helper copy failure'
        }}
""" + copy)
            script += publisher + "\n" + "\n".join(
                _ps_function(text, name)
                for name in ("Deploy-RuntimeResolvers", "Deploy-Wrappers")
            )
            script += f"""
$BinDir = {_ps_quote(root / 'bin')}
function Ensure-InstallDir($Dir) {{ [void][IO.Directory]::CreateDirectory($Dir) }}
"""
            script += f"\nif (-not (Invoke-VersionedActivate -Publish {mode})) {{ exit 1 }}\n"
        else:
            script += """
if (-not (Invoke-VersionedActivate -Publish wrappers)) { exit 1 }
[IO.File]::WriteAllText((Join-Path $InstallDir 'current-version'), "1.2.2`n")
if (-not (Invoke-VersionedActivate -Publish resolvers)) { exit 1 }
"""
    result = _run(shell, executable, script, tmp_path)
    assert result.returncode == (0 if case == "healthy" else 1), result.stdout + result.stderr
    observed = events.read_text(encoding="utf-8").splitlines()
    if case == "healthy":
        assert observed == [
            "health", "complete", "wrappers", "monitor:wrappers", "released",
            "health", "complete", "resolvers", "monitor:resolvers", "released",
        ]
        assert redirect.read_text(encoding="utf-8") == "resolvers"
        assert all((root / name).read_text().strip() == "1.2.3" for name in (
            "current-version", "last-known-good",
        ))
    elif case.startswith("monitor-"):
        assert observed == ["health", "complete", "wrappers", "monitor:wrappers", "released"]
        assert redirect.read_text(encoding="utf-8") == "wrappers"
        assert (root / "current-version").read_text().strip() == "1.2.3"
        assert (root / "last-known-good").read_text().strip() == "1.2.3"
        assert (slot / ".install-complete.json").read_bytes() == completed_before
    else:
        assert observed == (
            ["health", "complete", "released"] if failure else ["health", "released"]
        )
        assert redirect.read_text(encoding="utf-8") == "previous"
        expected = "1.2.4" if case == "competing-activation" else "1.2.2"
        assert all((root / name).read_text().strip() == expected for name in (
            "current-version", "last-known-good",
        ))
        if failure:
            assert "Runtime resolver source not found" in result.stderr or (
                "Injected helper copy failure" in result.stderr
            ), result.stdout + result.stderr
    assert manifest.read_bytes() == manifest_before
