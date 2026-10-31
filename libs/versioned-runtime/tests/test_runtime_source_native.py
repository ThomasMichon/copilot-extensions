"""Native source probes preserve argv, output and launcher-only bootstrap."""

import base64
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


def _run_progress(host, script, tmp_path, *, output_fds=None):
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")
    sys.path.insert(0, str(REPOSITORY / "libs" / "agent-procutil" / "src"))
    sys.path.insert(0, str(REPOSITORY / "libs" / "installer-engine"))
    import agent_procutil
    from installer_engine_process import run_owned

    harness = tmp_path / "native.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". {_quote(LIBRARY / 'versioned_runtime_native.ps1')}\n" + script,
        encoding="utf-8",
    )
    return run_owned(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        cwd=tmp_path, env=dict(os.environ), timeout=3,
        procutil=agent_procutil, construction_fd=None,
        on_output=(lambda _name, _data: None) if not output_fds else None,
        output_fds=output_fds,
    )


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
@pytest.mark.parametrize("stream_errors", [False, True])
def test_native_capture_supplies_stdin_and_drains_both_streams(tmp_path, host, stream_errors):
    code = (
        "import json,sys; sys.stdout.write('o'*131072); sys.stdout.flush(); "
        "sys.stderr.write('e'*131072); sys.stderr.flush(); "
        "print(json.dumps(sys.stdin.buffer.read().decode('utf-8')))"
    )
    script = f"""
$nativeError = ''
$inputText = '{{"source":"two words","version":"1.2.3"}}' + [char]0x03bb
$output = Invoke-Hidden -FilePath {_quote(sys.executable)} `
    -ArgList @('-I', '-c', {_quote(code)}) -InputText $inputText -ErrorOutput ([ref]$nativeError) `
    {'-StreamErrorOutput' if stream_errors else ''}
if ($LASTEXITCODE -ne 0 -or $nativeError -cne ('e' * 131072)) {{ throw 'stdin capture failed' }}
[Console]::Out.Write($output)
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ("e" * 131072 if stream_errors else "")
    assert result.stdout[:131072] == "o" * 131072
    assert json.loads(result.stdout[131072:]) == '{"source":"two words","version":"1.2.3"}' + chr(0x03bb)


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


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
@pytest.mark.parametrize("productive", [True, False])
def test_native_stderr_progress_renews_quiet_window_without_changing_capture(
    tmp_path, host, productive,
):
    marker = "diagnostic"
    code = (
        "import sys,time; "
        f"data={marker.encode('utf-8')!r}; "
        "[ (sys.stderr.buffer.write(data),sys.stderr.buffer.flush(),time.sleep(2)) "
        "for _ in range(4) ]; "
        "sys.stdout.write('payload-result'); sys.exit(7)"
        if productive else
        "import sys,time; sys.stderr.write('entered'); sys.stderr.flush(); time.sleep(30)"
    )
    script = f"""
$nativeError = ''
$output = Invoke-Hidden -FilePath {_quote(sys.executable)} `
    -ArgList @('-I', '-c', {_quote(code)}) -ErrorOutput ([ref]$nativeError) -StreamErrorOutput
if ($LASTEXITCODE -ne 7 -or $output -cne 'payload-result') {{ throw 'native result changed' }}
[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($nativeError)))
"""
    if productive:
        result = _run_progress(host, script, tmp_path)
        assert base64.b64decode(result.stdout).decode("utf-8") == marker * 4
        assert result.stderr == marker * 4
    else:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            _run_progress(host, script, tmp_path)
        assert caught.value.stderr == "entered"


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_native_stderr_relay_preserves_raw_bytes_and_original_text_capture(tmp_path, host):
    data = b"\xef\xbb\xbfdiagnostic\xff\r\n" + chr(0x03bb).encode("utf-8")
    code = f"import sys; sys.stderr.buffer.write({data!r}); sys.stderr.buffer.flush()"
    script = f"""
$originalError = ''
[void](Invoke-Hidden -FilePath {_quote(sys.executable)} `
    -ArgList @('-I', '-c', {_quote(code)}) -ErrorOutput ([ref]$originalError))
$nativeError = ''
$output = Invoke-Hidden -FilePath {_quote(sys.executable)} `
    -ArgList @('-I', '-c', {_quote(code)}) -ErrorOutput ([ref]$nativeError) -StreamErrorOutput
if ($LASTEXITCODE -ne 0 -or $output -or $nativeError -cne $originalError) {{
    throw 'native result changed'
}}
[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($nativeError)))
"""
    sink = tmp_path / "diagnostics"
    with sink.open("wb") as stream:
        result = _run_progress(host, script, tmp_path, output_fds={"stderr": stream.fileno()})
    assert sink.read_bytes() == data
    assert base64.b64decode(result.stdout).decode("utf-8") == data.decode("utf-8-sig", "replace")


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job settlement")
def test_native_stderr_sink_failure_settles_original_writer_before_error(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPOSITORY / "libs" / "agent-procutil" / "src"))
    import agent_procutil

    marker = tmp_path / "late-writer"
    writer = tmp_path / "writer-started"
    settle = agent_procutil.JobHandle.terminate_and_wait
    settled = []

    def observe(job, timeout):
        result = settle(job, timeout)
        settled.append(job.handle)
        return result

    monkeypatch.setattr(agent_procutil.JobHandle, "terminate_and_wait", observe)
    code = (
        "import sys,time; from pathlib import Path; "
        f"Path({str(writer)!r}).write_text('started'); "
        "sys.stderr.write('entered'); sys.stderr.flush(); time.sleep(6); "
        f"Path({str(marker)!r}).write_text('unsettled')"
    )
    script = f"""
$nativeError = ''
Invoke-Hidden -FilePath {_quote(sys.executable)} `
    -ArgList @('-I', '-c', {_quote(code)}) -ErrorOutput ([ref]$nativeError) -StreamErrorOutput
"""
    sink = tmp_path / "diagnostics"
    sink.write_bytes(b"")
    with sink.open("rb") as stream, pytest.raises(OSError):
        _run_progress("pwsh", script, tmp_path, output_fds={"stderr": stream.fileno()})
    assert not marker.exists()
    assert writer.read_text() == "started"
    assert settled and all(handle is None for handle in settled)


@pytest.mark.parametrize("phase", ["hash", "copy", "silent-copy"])
def test_native_source_wrapper_reports_completed_hash_and_copy_work(tmp_path, phase):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-worktrees", "version": "1.2.3"}), encoding="utf-8",
    )
    for index in range(4):
        (payload / f"input-{index}.txt").write_text(str(index), encoding="utf-8")
    digest = identity.assembly_hash(payload)
    selected = identity.SourceIdentity(
        "agent-worktrees", "1.2.3", "a" * 40, "b" * 40, digest,
        identity.runtime_version("1.2.3", "a" * 40, digest),
    )
    (payload / identity.SOURCE_FILE).write_text(json.dumps(selected.document()), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in payload.iterdir()}
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    actor = f"""
import sys,time
from pathlib import Path
sys.path.insert(0,{str(LIBRARY)!r})
import versioned_runtime_source as source
payload=Path({str(payload)!r})
if {phase!r} == 'hash':
    read=Path.read_bytes
    def slow_read(path):
        if path.parent == payload:
            time.sleep(2)
        return read(path)
    Path.read_bytes=slow_read
else:
    copy=source.shutil.copy2
    def slow_copy(origin,destination):
        time.sleep(30 if {phase!r} == 'silent-copy' else 1.5)
        return copy(origin,destination)
    source.shutil.copy2=slow_copy
raise SystemExit(source.main())
"""
    (helpers / "versioned_runtime_source.py").write_text(actor, encoding="utf-8")
    expected = json.dumps(identity.manifest_fields(selected), sort_keys=True)
    root = tmp_path / "runtime"
    script = f"""
. {_quote(REPOSITORY / 'plugins' / 'agent-worktrees' / 'scripts' / 'source-runtime.ps1')}
function Get-BootstrapPython {{ param([switch]$ExcludeRuntimeRoot); return {_quote(sys.executable)} }}
$PluginDir = {_quote(payload)}
$ScriptDir = {_quote(helpers)}
$PackageVersion = '1.2.3'
$SrcVersion = {_quote(selected.runtime_version)}
$SourceRuntimeIdentity = {_quote(expected)}
$InstallDir = {_quote(root)}
[Console]::Out.Write({'(Get-SourceRuntimeIdentity)' if phase == 'hash' else '(Publish-SourceSnapshot)'})
"""
    if phase == "silent-copy":
        with pytest.raises(subprocess.TimeoutExpired):
            _run_progress("pwsh", script, tmp_path)
        assert not (root / "snapshots" / selected.runtime_version).exists()
    else:
        result = _run_progress("pwsh", script, tmp_path)
        assert "source-runtime: completed source work" in result.stderr
        if phase == "hash":
            assert json.loads(result.stdout) == identity.manifest_fields(selected)
        else:
            snapshot = Path(result.stdout)
            assert snapshot == root / "snapshots" / selected.runtime_version
            assert identity.read_source_identity(snapshot, "1.2.3") == selected
    assert {p.name: p.read_bytes() for p in payload.iterdir()} == before


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
def test_fresh_containers_stamp_publishes_snapshot_with_declared_bootstrap(tmp_path, host):
    executable, launcher = shutil.which(host), shutil.which("py")
    if not executable or not launcher:
        pytest.skip("PowerShell or Python launcher unavailable")
    plugin = REPOSITORY / "plugins" / "agent-containers"
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in ("init.ps1", "runtime_profile.py", "legacy-transaction.ps1"):
        shutil.copy2(plugin / "scripts" / name, scripts / name)
    for path in LIBRARY.glob("versioned_runtime*.*"):
        shutil.copy2(path, scripts / path.name)
    shutil.copytree(
        REPOSITORY / "libs/installation-context", scripts / "installation-context",
        ignore=shutil.ignore_patterns("tests", "__pycache__"),
    )
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
        "PATH": os.pathsep.join((str(binary), str(Path(sys.executable).parent))),
        "HOME": str(home), "USERPROFILE": str(home),
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
