"""Execute the installer's native boundary, without provisioning a runtime."""

from __future__ import annotations

import base64
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "init.ps1"
HOSTS = [
    pytest.param(shutil.which(name), id=name)
    for name in ("pwsh", "powershell.exe")
    if name == "pwsh" or os.name == "nt"
]


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _helper() -> str:
    return ". " + _quote(INSTALLER.parent / "versioned_runtime_native.ps1") + "\n"


def _producer_setup(directory: Path) -> str:
    for name in ("installer-human-output.ps1", "versioned_runtime_native.ps1"):
        shutil.copyfile(INSTALLER.parent / name, directory / name)
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index("Set-StrictMode -Version 2.0")
    end = text.index("# === install-contract:test-persistent-environment", start)
    return "$Action = 'stamp'\n$InstallDir = Join-Path $PSScriptRoot 'fixture-runtime'\n" + text[start:end]


def _cleanup_diagnostic_readback(directory: Path) -> str:
    records = ["Fixture-only construction timeout diagnostics (original 20s bound):"]
    for name in ("stages.log", "python-stacks.log"):
        path = directory / name
        try:
            with path.open("rb") as stream:
                data = stream.read(16385)
        except OSError as error:
            records.append(f"{name}: unavailable ({type(error).__name__})")
            continue
        records.append(f"--- {name} ---\n{data[:16384].decode('utf-8', 'replace')}")
        if len(data) > 16384:
            records.append("[truncated at 16384 bytes]")
    return "\n".join(records)


def _construction_diagnostics(
    directory: Path,
    helper: Path,
    shell: str,
    *,
    interval: float = 8,
) -> tuple[Path, str]:
    directory.mkdir()
    wrapper = directory / "construction_probe.py"
    wrapper.write_text(
        "import faulthandler\nimport runpy\n"
        f"with open({str(directory / 'python-stacks.log')!r}, 'w') as stacks:\n"
        f"    faulthandler.dump_traceback_later({interval!r}, repeat=True, file=stacks)\n"
        "    try:\n"
        f"        runpy.run_path({str(helper)!r}, run_name='__main__')\n"
        "    finally:\n"
        "        faulthandler.cancel_dump_traceback_later()\n",
        encoding="utf-8",
    )
    prefix, boundary, cleanup = shell.rpartition("function script:Stop-RuntimeConstruction")
    assert boundary, "construction cleanup adapter changed; update fixture diagnostics"
    probes = (
        ("Send-RuntimeConstructionCommand $session (", "abort-write", False),
        ("(@{command=$command} | ConvertTo-Json -Compress)\n        )", "abort-sent", True),
        ("$line = $session.Process.StandardOutput.ReadLine()", "reply-wait", False),
        ("$line = $session.Process.StandardOutput.ReadLine()", "reply-received", True),
        ("$session.Process.WaitForExit()", "exit-wait", False),
        ("$session.Process.WaitForExit()", "child-exited", True),
    )
    for needle, label, after in probes:
        assert cleanup.count(needle) == 1, f"cleanup probe changed: {needle}"
        mark = f"Write-ConstructionDiagnosticStage '{label}'"
        cleanup = cleanup.replace(
            needle, f"{needle}\n        {mark}" if after else f"{mark}\n        {needle}"
        )
    writer = f"""
function Write-ConstructionDiagnosticStage([string]$Stage) {{
    try {{
        [IO.File]::AppendAllText({_quote(directory / "stages.log")},
            [DateTime]::UtcNow.ToString('O') + ' ' + $Stage + [Environment]::NewLine)
    }} catch {{
        [Console]::Error.WriteLine(
            "Construction diagnostic warning: could not record stage '$Stage': " +
            $_.Exception.Message)
    }}
}}
"""
    return wrapper, writer + prefix + boundary + cleanup


def _run(
    host: str | None,
    script: str,
    *,
    diagnostics: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if host is None:
        pytest.skip("PowerShell host unavailable")
    script = "$ProgressPreference = 'SilentlyContinue'\n" + script
    if diagnostics is not None:
        path = diagnostics / "native-probe.ps1"
        path.write_text(script, encoding="utf-8-sig")
        arguments = ["-File", str(path)]
    else:
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        arguments = ["-EncodedCommand", encoded]
    try:
        return subprocess.run(
            [host, "-NoProfile", "-NonInteractive", *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        if diagnostics is not None:
            print(_cleanup_diagnostic_readback(diagnostics), file=sys.stderr, flush=True)
        raise


@pytest.mark.skipif(os.name != "nt", reason="literal generated Windows CMD launch")
@pytest.mark.parametrize("complete", [True, False], ids=["complete", "unqualified"])
def test_generated_cmd_uses_marker_owner_and_preserves_stdin(tmp_path, complete):
    from plugin_test_environment import isolated_environment

    host = shutil.which("pwsh")
    assert host
    environment = isolated_environment(os.environ, tmp_path / "profile")
    home = tmp_path / "p'q"
    root = home / ".agent-mcp"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    slot = root / "versions" / "1.0.0"
    python_home = slot / ".venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(python_home)],
        env=environment, check=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    (root / "current-version").write_text("1.0.0", encoding="utf-8")
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.0.0" if complete else "different",
        "completed_at": "fixture", "pid": 1,
    }), encoding="utf-8")
    function = re.search(
        r"(?ms)^function Deploy-SelfProvisioningBinstub \{.*?^\}",
        INSTALLER.read_text(encoding="utf-8"),
    ).group().replace("$PSScriptRoot", _quote(INSTALLER.parent))
    generated = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command",
         f"$InstallDir={_quote(root)}; $LocalBin={_quote(local_bin)}; "
         "$utf8NoBom=[Text.UTF8Encoding]::new($false); function Write-Ok($message) {}\n"
         + function + "\nDeploy-SelfProvisioningBinstub"],
        env=environment, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr
    modules = tmp_path / "modules"
    package = modules / "agent_mcp"
    package.mkdir(parents=True)
    (package / "__main__.py").write_text(
        "import json,sys\nprint(json.dumps({'prefix':sys.prefix,'argv':sys.argv[1:],"
        "'stdin':sys.stdin.readline().rstrip('\\n')}))\n", encoding="utf-8",
    )
    environment.update(
        USERPROFILE=str(home), HOME=str(home), PYTHONPATH=str(modules),
        PATH=str(local_bin) + os.pathsep + environment["PATH"],
        AGENT_MCP_NO_SELFPROVISION="1",
    )
    result = subprocess.run(
        f'"{os.environ["ComSpec"]}" /d /s /c agent-mcp --fixture "two words"',
        cwd=tmp_path, env=environment, input="untouched stdin\n",
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if complete:
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert Path(value["prefix"]) == python_home
        assert value["argv"] == ["--fixture", "two words"]
        assert value["stdin"] == "untouched stdin"
    else:
        assert result.returncode != 0 and not result.stdout
        assert "runtime not provisioned" in result.stderr


@pytest.mark.parametrize("host", HOSTS)
def test_native_helper_preserves_python_flags_and_literal_argv(host: str | None) -> None:
    arguments = ["--", "two words", 'embedded"quote', r"a\"b", "trailing\\", "", "-I"]
    code = "import json,sys; print(json.dumps([sys.flags.isolated, sys.argv[1:]]))"
    argv = ", ".join(_quote(arg) for arg in ["-I", "-c", code, *arguments])
    result = _run(
        host,
        _helper()
        + f"""
$ErrorActionPreference = 'Stop'
$output = Invoke-Hidden -FilePath {_quote(sys.executable)} -ArgList @({argv})
if ($LASTEXITCODE -ne 0) {{ throw 'native child failed' }}
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    {_quote(INSTALLER)}, [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ throw 'installer syntax errors' }}
$calls = $ast.FindAll({{
    param($node)
    $node -is [Management.Automation.Language.CommandAst] -and
        $node.GetCommandName() -eq 'Invoke-Hidden'
}}, $true)
foreach ($call in $calls) {{
    $parameters = @($call.CommandElements | Where-Object {{
        $_ -is [Management.Automation.Language.CommandParameterAst]
    }} | ForEach-Object {{ $_.ParameterName }})
    if ($parameters -notcontains 'FilePath' -or $parameters -notcontains 'ArgList') {{
        throw 'native call must bind an explicit argv array'
    }}
}}
Write-Output $output
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [1, arguments]
    assert not result.stderr


@pytest.mark.parametrize("host", HOSTS)
def test_native_helper_drains_stderr_and_retains_failure(host: str | None) -> None:
    code = (
        "import sys; "
        "sys.stderr.write('policy-denied\\n' + 'E' * 131072 + '\\n'); "
        "sys.stderr.flush(); print('stdout-retained'); sys.exit(17)"
    )
    result = _run(
        host,
        _helper()
        + f"""
$ErrorActionPreference = 'Stop'
$output = Invoke-Hidden -FilePath {_quote(sys.executable)} -ArgList @('-I', '-c', {_quote(code)})
Write-Output "native-exit=$LASTEXITCODE"
Write-Output $output
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr[:300]
    assert result.stdout.strip().splitlines() == ["native-exit=17", "stdout-retained"]
    assert "policy-denied" in result.stderr
    assert "E" * 131072 in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows redirected producer encoding")
@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("native_exit", [0, 23])
def test_construction_shell_stderr_uses_requested_producer_encoding(
    host: str | None, native_exit: int, tmp_path: Path,
) -> None:
    if host is None:
        pytest.skip("PowerShell host unavailable")
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index("function Invoke-VersionedSlotClean {")
    end = text.index("\nfunction Invoke-VersionedMarkComplete", start)
    native = tmp_path / "versioned_runtime.py"
    native.write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "Path(os.environ['MCP_NATIVE_RECEIPT']).write_text("
        "json.dumps([sys.flags.isolated,sys.argv[1:]]), encoding='utf-8')\n"
        "sys.stderr.buffer.write(b'native detail caf\\xe9\\n')\n"
        "print(\"function Start-RuntimeConstruction { "
        "param($Python,$Helper,$Root,$Version,$PayloadHash,$LinkName,$Footprint,$Plugin) "
        "$script:RuntimeConstructionSession = [pscustomobject]@{Version=$Version}; "
        "[IO.File]::WriteAllText($env:MCP_NATIVE_CALLED,'called'); return $true }\")\n"
        f"sys.exit({native_exit})\n",
        encoding="ascii",
    )
    script = tmp_path / "construction-output.ps1"
    root = tmp_path / "runtime with spaces"
    script.write_text(
        "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(1252)\n"
        "$OutputEncoding = [Text.ASCIIEncoding]::new()\n"
        + _producer_setup(tmp_path)
        + f"""
function Assert-SourceRuntime {{}}
function Get-BootstrapPython {{ {_quote(sys.executable)} }}
function Get-PayloadHash {{ 'fixture-hash' }}
$VersionedRuntime = $true
$InstallDir = {_quote(root)}
$LinkDir = Join-Path $InstallDir '.venv'
$SrcVersion = '2.0.0'
{text[start:end]}
try {{
    Invoke-VersionedSlotClean
    [Console]::Out.WriteLine('completed')
}} catch {{
    [Console]::Out.WriteLine('caught=' + $_.Exception.Message)
}}
if ([Console]::OutputEncoding.CodePage -ne 1252 -or $OutputEncoding.CodePage -ne 20127) {{
    throw 'native decoding or stdin changed'
}}
exit 0
""",
        encoding="ascii",
    )
    receipt = tmp_path / "native-argv.json"
    called = tmp_path / "called"
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env={
            **os.environ, "COPILOT_EXTENSIONS_HUMAN_UTF8": "1",
            "MCP_NATIVE_RECEIPT": str(receipt), "MCP_NATIVE_CALLED": str(called),
        },
        capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(receipt.read_text(encoding="utf-8")) == [
        1, ["--root", str(root), "construction-shell", "--shell", "ps1"],
    ]
    assert "native detail caf\u00e9" in result.stderr.decode("utf-8")
    if native_exit == 0:
        assert called.read_text(encoding="utf-8-sig") == "called"
        assert result.stdout.decode("utf-8").strip() == "completed"
    else:
        assert not called.exists()
        assert result.stdout.decode("utf-8").startswith("caught=")


@pytest.mark.skipif(os.name != "nt", reason="Windows redirected producer encoding")
@pytest.mark.parametrize("host", HOSTS)
def test_incoming_failure_preserves_native_warning_and_retains_launch_assets(
    host: str | None, tmp_path: Path,
) -> None:
    if host is None:
        pytest.skip("PowerShell host unavailable")
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index("function Publish-IncomingMcp {")
    publication = text[start:text.index("\n}\n", start) + 2]
    (tmp_path / "publish_incoming.py").write_text(
        "import sys\nprint('fixture-version:123')\n"
        "sys.stderr.write('fixture warning: caf'+chr(233)+'\\n')\nsys.exit(7)\n", encoding="ascii",
    )
    retained = tmp_path / "predecessor" / "lazy.py"
    retained.parent.mkdir()
    retained.write_text("# retained launch-owned code\n", encoding="ascii")
    collected = tmp_path / "collection-invoked"
    gc = tmp_path / "gc.py"
    gc.write_text(
        "from pathlib import Path\n"
        f"Path({ascii(str(collected))}).write_text('unexpected')\n"
        "print('{\"removed\": []}')\n",
        encoding="ascii",
    )
    script = tmp_path / "gc-output.ps1"
    script.write_text(
        "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(1252)\n"
        "$OutputEncoding = [Text.ASCIIEncoding]::new()\n"
        + _producer_setup(tmp_path)
        + "\n".join(
            line for line in text.splitlines()
            if line.startswith(("function Write-Ok ", "function Write-Skip "))
        )
        + f"""
$VenvPython = {_quote(sys.executable)}
$VrScript = {_quote(gc)}
$InstallDir = {_quote(tmp_path / "runtime with spaces")}
$VersionedRuntime = $true
$env:AGENT_MCP_NO_VERSION_REAP = ''
{publication}
try {{ Publish-IncomingMcp; throw 'expected incoming failure' }} catch {{
if ([Console]::OutputEncoding.CodePage -ne 1252 -or $OutputEncoding.CodePage -ne 20127) {{
    throw 'native decoding or stdin changed'
}}
exit 23
}}
""",
        encoding="ascii",
    )
    environment = {
        **os.environ, "COPILOT_EXTENSIONS_HUMAN_UTF8": "1", "PYTHONIOENCODING": "cp1252",
    }
    environment.pop("PYTHONWARNINGS", None)
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=environment,
        capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 23, result.stdout + result.stderr
    error = result.stderr.decode("utf-8")
    assert error.count("fixture warning:") == 1
    assert "caf\u00e9" in error
    assert retained.read_text(encoding="ascii") == "# retained launch-owned code\n"
    assert not collected.exists()


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("have_uv", [True, False])
def test_preinstall_executes_real_native_helper(
    host: str | None,
    have_uv: bool,
    tmp_path: Path,
) -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index("foreach ($lib in @(\n    @{ Dir = 'credential-relay';")
    loop = text[start : text.index("\n\nif (Get-Command uv ", start)]
    plugin = tmp_path / "payload with spaces"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    policy = scripts / "installer_dependency_policy.py"
    policy.write_text(
        "import json,sys\nprint(json.dumps([sys.flags.isolated, sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    libraries = ("credential-relay", "agent-procutil", "single-instance-lease", "zdd")
    for name in libraries:
        lib = plugin / "libs" / name
        lib.mkdir(parents=True)
        (lib / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    # Retain the policy call's stdout only for assertions; the production loop
    # discards it after the real native helper has captured both child streams.
    loop = loop.replace(" | Out-Null", "")
    result = _run(
        host,
        _helper()
        + f"""
$ErrorActionPreference = 'Stop'
$PluginDir = {_quote(plugin)}
$PSScriptRoot = {_quote(scripts)}
$VenvPython = {_quote(sys.executable)}
function Get-Command {{
    param($Name)
    if ($Name -ne 'uv') {{ throw 'unexpected command discovery' }}
    if (${str(have_uv).lower()}) {{ return 'fixture-uv' }}
}}
function Write-Fail {{ param($Message) throw $Message }}
{loop}
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert len(records) == len(libraries)
    for record, name in zip(records, libraries, strict=True):
        assert record[0] == 1
        expected = ["--uv", "uv", "--", "--python", sys.executable]
        if have_uv:
            package = name if name == "agent-procutil" else f"agent-{name}"
            expected += ["--reinstall-package", package, str(plugin / "libs" / name), "--quiet"]
        else:
            expected += ["--quiet", str(plugin / "libs" / name)]
        assert record[1] == expected


@pytest.mark.parametrize("host", HOSTS)
def test_owned_construction_cleanup_preserves_primary_failure(
    host: str | None,
    tmp_path: Path,
) -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    lock_start = text.index("# === install-transaction lock (fail-closed) ===")
    lock_end = text.index("# === end install-transaction lock ===", lock_start)
    lock = text[lock_start:lock_end]
    finalization = text[text.rindex("\n} catch {\n    $script:McpInstallFailure = $_") :]
    helper = INSTALLER.parent / "versioned_runtime.py"
    adapter = runpy.run_path(str(INSTALLER.parent / "versioned_runtime_construction.py"))
    shell = adapter["construction_shell_helpers"]("ps1")
    diagnostics = tmp_path / "cleanup diagnostics"
    helper, shell = _construction_diagnostics(diagnostics, helper, shell)
    native = _helper()
    denial = "import sys; sys.stderr.write('dependency-fixture-denied\\n'); sys.exit(17)"
    root = tmp_path / "owned runtime"
    old = root / "versions" / "1.0"
    old.mkdir(parents=True)
    (old / "previous").write_text("unchanged", encoding="utf-8")
    (root / "current-version").write_text("1.0", encoding="utf-8")
    result = _run(
        host,
        f"""
$ErrorActionPreference = 'Stop'
$WarningPreference = 'Stop'
Set-StrictMode -Version 2.0
$InstallDir = {_quote(root)}
{lock}
{shell}
{native}
$script:McpInstallFailure = $null
$code = 0
if (-not (Enter-InstallLock -TimeoutSec 0)) {{ throw 'initial lock failed' }}
try {{
    try {{
        $reuse = Start-RuntimeConstruction -Python {_quote(sys.executable)} `
            -Helper {_quote(helper)} -Root $InstallDir -Version '2.0' `
            -PayloadHash ('a' * 64) -LinkName '.venv'
        [IO.File]::WriteAllText({_quote(root / "versions" / "2.0" / "partial")}, 'partial')
        Invoke-Hidden -FilePath {_quote(sys.executable)} `
            -ArgList @('-I', '-c', {_quote(denial)}) | Out-Null
        if ($LASTEXITCODE -ne 0) {{ throw 'original-policy-failure' }}
        throw 'native denial was not exercised'
{finalization}
}} catch {{
    [Console]::Error.WriteLine($_.Exception.Message)
    $code = 1
}}
if ($script:InstallLockHandle) {{ throw 'install lock survived cleanup' }}
if (-not (Enter-InstallLock -TimeoutSec 0)) {{ throw 'lock could not be reacquired' }}
Exit-InstallLock
Write-Output 'lock-reacquired'
exit $code
""",
        diagnostics=diagnostics,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "original-policy-failure" in result.stderr
    assert "dependency-fixture-denied" in result.stderr
    assert "owned incomplete candidate was reclaimed" in " ".join(
        (result.stdout + result.stderr).split()
    )
    assert "lock-reacquired" in result.stdout
    assert not (root / "versions" / "2.0").exists()
    assert (root / "current-version").read_text(encoding="utf-8") == "1.0"
    assert (old / "previous").read_text(encoding="utf-8") == "unchanged"
    stages = (diagnostics / "stages.log").read_text(encoding="utf-8")
    assert [line.split()[-1] for line in stages.splitlines()] == [
        "abort-write",
        "abort-sent",
        "reply-wait",
        "reply-received",
        "exit-wait",
        "child-exited",
    ]


def test_cleanup_diagnostic_readback_is_bounded_and_tolerates_missing_files(
    tmp_path: Path,
) -> None:
    missing = _cleanup_diagnostic_readback(tmp_path)
    assert "stages.log: unavailable (FileNotFoundError)" in missing
    assert "python-stacks.log: unavailable (FileNotFoundError)" in missing
    (tmp_path / "stages.log").write_bytes(b"abort-sent\n" + b"x" * 17000)
    (tmp_path / "python-stacks.log").write_bytes(b"stack-frame\n\xff")
    report = _cleanup_diagnostic_readback(tmp_path)
    assert "abort-sent\n" in report
    assert "stack-frame\n\ufffd" in report
    assert "[truncated at 16384 bytes]" in report
    assert len(report) < 17000


def test_cleanup_timeout_retains_real_python_stacks_and_original_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    helper = tmp_path / "blocked_fixture.py"
    helper.write_text("import time\ntime.sleep(0.35)\n", encoding="utf-8")
    adapter = runpy.run_path(str(INSTALLER.parent / "versioned_runtime_construction.py"))
    diagnostics = tmp_path / "cleanup diagnostics"
    wrapper, _ = _construction_diagnostics(
        diagnostics,
        helper,
        adapter["construction_shell_helpers"]("ps1"),
    )
    assert "dump_traceback_later(8, repeat=True" in wrapper.read_text(encoding="utf-8")
    diagnostics = tmp_path / "quick diagnostics"
    wrapper, _ = _construction_diagnostics(
        diagnostics,
        helper,
        adapter["construction_shell_helpers"]("ps1"),
        interval=0.1,
    )
    result = subprocess.run(
        [sys.executable, "-I", str(wrapper)],
        capture_output=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    original = subprocess.TimeoutExpired(
        ["fixture-pwsh"],
        20,
        output=b"stdout-retained",
        stderr=b"primary-failure-retained",
    )

    def expire(*args: object, **kwargs: object) -> None:
        assert kwargs["timeout"] == 20
        raise original

    monkeypatch.setattr(subprocess, "run", expire)
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        _run("fixture-pwsh", "", diagnostics=diagnostics)
    error = failure.value
    assert error is original
    assert error.timeout == 20
    assert error.output == b"stdout-retained"
    assert error.stderr == b"primary-failure-retained"
    report = capsys.readouterr().err
    assert report.count("Timeout (") >= 2
    assert str(helper) in report
    assert "line 2 in <module>" in report
    assert "stages.log: unavailable (FileNotFoundError)" in report


@pytest.mark.parametrize("host", HOSTS)
def test_cleanup_stage_write_failure_warns_without_replacing_primary_failure(
    host: str | None,
    tmp_path: Path,
) -> None:
    adapter = runpy.run_path(str(INSTALLER.parent / "versioned_runtime_construction.py"))
    diagnostics = tmp_path / "cleanup diagnostics"
    _, shell = _construction_diagnostics(
        diagnostics,
        INSTALLER.parent / "versioned_runtime.py",
        adapter["construction_shell_helpers"]("ps1"),
    )
    (diagnostics / "stages.log").mkdir()
    result = _run(
        host,
        shell
        + """
Write-ConstructionDiagnosticStage 'write-failure'
[Console]::Error.WriteLine('primary-failure-retained')
exit 17
""",
    )
    assert result.returncode == 17
    assert (
        "Construction diagnostic warning: could not record stage 'write-failure':" in result.stderr
    )
    assert "primary-failure-retained" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows visibility contract")
@pytest.mark.parametrize("host", HOSTS)
def test_native_helper_stays_windowless_for_two_cycles(host: str | None) -> None:
    visibility = runpy.run_path(
        str(Path(__file__).with_name("test_windows_launch_integration.py"))
    )
    user32, kernel32, callback_type = visibility["_configure_win32"]()
    visible_windows = visibility["_visible_windows"]
    process_name = visibility["_process_name"]
    names = visibility["_SURFACED_PROCESS_NAMES"] | {"pwsh.exe"}
    baseline = visible_windows(user32, callback_type)
    foreground = user32.GetForegroundWindow()
    surfaced: set[int] = set()
    focus_changes: set[int] = set()
    stopped = threading.Event()

    def observe() -> None:
        while not stopped.wait(0.01):
            surfaced.update(
                hwnd
                for hwnd in visible_windows(user32, callback_type) - baseline
                if process_name(user32, kernel32, hwnd) in names
            )
            current = user32.GetForegroundWindow()
            if current != foreground and process_name(user32, kernel32, current) in names:
                focus_changes.add(current)

    child = "import ctypes,time; print(ctypes.windll.kernel32.GetConsoleWindow()); time.sleep(0.1)"
    with ThreadPoolExecutor(max_workers=1) as executor:
        observation = executor.submit(observe)
        try:
            result = _run(
                host,
                _helper()
                + f"""
$ErrorActionPreference = 'Stop'
for ($cycle = 0; $cycle -lt 2; $cycle++) {{
    Invoke-Hidden -FilePath {_quote(sys.executable)} -ArgList @('-I', '-c', {_quote(child)})
    if ($LASTEXITCODE -ne 0) {{ throw 'console descendant failed' }}
    Start-Sleep -Milliseconds 100
}}
""",
            )
        finally:
            stopped.set()
            observation.result(timeout=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.split() == ["0", "0"]
    assert not surfaced, f"visible console windows: {surfaced}"
    assert not focus_changes, f"console foreground transitions: {focus_changes}"
