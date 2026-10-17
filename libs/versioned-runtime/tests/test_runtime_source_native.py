"""Native source probes preserve argv, output and launcher-only bootstrap."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
REPOSITORY = LIBRARY.parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime_source as identity


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _run(host, script, tmp_path, *, env=None):
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")
    harness = tmp_path / "native.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". {_quote(LIBRARY / 'versioned_runtime_native.ps1')}\n" + script,
        encoding="utf-8",
    )
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        env=env, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_native_capture_preserves_arguments_and_separates_both_streams(tmp_path, host):
    values = ["", "plain", "-3", "two words", 'a"b', "tail slash \\", "line\nbreak"]
    code = "import json,sys; print(json.dumps(sys.argv[1:])); sys.stderr.write('diagnostic')"
    script = f"""
$values = ConvertFrom-Json {_quote(json.dumps(values))}
$nativeError = ''
$output = Invoke-Hidden -FilePath {_quote(sys.executable)} `
    -ArgList (@('-I', '-c', {_quote(code)}) + $values) -ErrorOutput ([ref]$nativeError)
if ($LASTEXITCODE -ne 0 -or $nativeError -cne 'diagnostic') {{ throw 'native capture failed' }}
[Console]::Out.Write($output)
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == values


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_native_capture_drains_both_streams_and_preserves_failure(tmp_path, host):
    code = "import sys; sys.stdout.write('o'*131072); sys.stderr.write('e'*131072); sys.exit(7)"
    script = f"""
$nativeError = ''
$output = Invoke-Hidden -FilePath {_quote(sys.executable)} `
    -ArgList @('-I', '-c', {_quote(code)}) -ErrorOutput ([ref]$nativeError)
if ($LASTEXITCODE -ne 7 -or $output -cne ('o' * 131072) -or
    $nativeError -cne ('e' * 131072)) {{ throw 'native failure evidence changed' }}
[Console]::Out.Write('captured')
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "captured"
    assert result.stderr == ""


@pytest.mark.skipif(os.name != "nt", reason="native Windows launcher discovery")
@pytest.mark.parametrize("host", ["pwsh", "powershell"])
@pytest.mark.parametrize("plugin", ["agent-containers", "agent-machines"])
def test_fresh_source_probe_accepts_only_py_on_path(tmp_path, host, plugin):
    launcher = shutil.which("py")
    if not launcher:
        pytest.skip("Python launcher unavailable")
    binary = tmp_path / "bin"
    binary.mkdir()
    shutil.copy2(launcher, binary / "py.exe")
    payload = tmp_path / "payload"
    payload.mkdir()
    version = "1.2.3"
    (payload / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": version}), encoding="utf-8",
    )
    digest = identity.assembly_hash(payload)
    source = identity.SourceIdentity(
        plugin, version, "a" * 40, "b" * 40, digest,
        identity.runtime_version(version, "a" * 40, digest),
    )
    (payload / "runtime-source.json").write_text(json.dumps(source.document()), encoding="utf-8")
    name = "Get-SourceRuntimeVersion" if plugin == "agent-containers" else "Get-SourceRuntimeIdentity"
    text = (REPOSITORY / "plugins" / plugin / "scripts" / "init.ps1").read_text(encoding="utf-8")
    function = re.search(rf"^function {name} \{{.*?^\}}", text, re.M | re.S)
    assert function is not None
    bootstrap = ""
    if plugin == "agent-containers":
        match = re.search(r"^function Get-SourceRuntimePython \{.*?^\}", text, re.M | re.S)
        assert match is not None
        bootstrap = match.group()
    script = f"""
Set-StrictMode -Version 2.0
$PluginDir = {_quote(payload)}
$PackageVersion = '{version}'
$ScriptDir = {_quote(LIBRARY)}
if (Get-Command python, python3 -ErrorAction SilentlyContinue) {{ throw 'fixture has direct Python' }}
{bootstrap}
{function.group().replace('$PSScriptRoot', '$ScriptDir')}
$result = {name}
"""
    script += (
        "[Console]::Out.Write($result)\n" if plugin == "agent-containers" else
        "[Console]::Out.Write(($result | ConvertFrom-Json).runtime_version)\n"
    )
    result = _run(host, script, tmp_path, env={**os.environ, "PATH": str(binary)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == source.runtime_version


@pytest.mark.skipif(os.name != "nt", reason="native Windows source stamp")
@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_fresh_containers_stamp_publishes_snapshot_with_only_py(tmp_path, host):
    executable, launcher = shutil.which(host), shutil.which("py")
    if not executable or not launcher:
        pytest.skip("PowerShell or Python launcher unavailable")
    plugin = REPOSITORY / "plugins" / "agent-containers"
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in ("init.ps1", "runtime_profile.py"):
        shutil.copy2(plugin / "scripts" / name, scripts / name)
    for path in LIBRARY.glob("versioned_runtime*.*"):
        shutil.copy2(path, scripts / path.name)
    version = "1.2.3"
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-containers", "version": version}), encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "agent-containers"\nversion = "{version}"\n', encoding="utf-8",
    )
    digest = identity.assembly_hash(payload)
    source = identity.SourceIdentity(
        "agent-containers", version, "a" * 40, "b" * 40, digest,
        identity.runtime_version(version, "a" * 40, digest),
    )
    (payload / "runtime-source.json").write_text(json.dumps(source.document()), encoding="utf-8")
    binary, home = tmp_path / "bin", tmp_path / "home"
    binary.mkdir()
    home.mkdir()
    shutil.copy2(launcher, binary / "py.exe")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("COPILOT_PLUGIN_INSTALL_SMOKE")
    }
    env.update({
        "PATH": str(binary), "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1", "COPILOT_PLUGIN_INSTALL_STAGED": "1",
    })
    root = home / ".agent-containers"
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(scripts / "init.ps1"),
         "-Action", "stamp", "-InstallDir", str(root)],
        env=env, cwd=home, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "stamped-version").read_text().strip() == source.runtime_version
    snapshot = Path((root / "payload-dir").read_text().strip())
    assert snapshot == root / "snapshots" / source.runtime_version
    assert identity.read_source_identity(snapshot, version, plugin="agent-containers") == source
    for suffix in ("ps1", "cmd"):
        assert (home / ".local" / "bin" / f"agent-containers.{suffix}").is_file()
    assert not (root / "versions").exists()
