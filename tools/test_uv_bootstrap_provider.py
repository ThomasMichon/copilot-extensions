"""Shell-native managed selection, independently of ambient Python discovery."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import pytest

import installer_engine_ref as ier
import materialize_installer_engine as materializer

ROOT = Path(__file__).resolve().parents[1]
PROVIDER = ROOT / "libs/installer-engine/bootstrap-uv.ps1"


@pytest.mark.parametrize("operation", ["Inspect", "Prepare"])
def test_posix_bootstrap_is_managed_only_and_acquires_only_for_setup(
    tmp_path: Path, operation: str,
) -> None:
    bash = shutil.which("bash")
    if not bash or os.name == "nt":
        pytest.skip("This control exercises the POSIX shell provider")
    request = tmp_path / "request.txt"
    request.write_bytes(b"cpython@3.13.12\r\n")
    python = tmp_path / "managed python"
    python.touch(mode=0o700)
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "calls"
    uv = tools / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        '[ -z "${UV_PYTHON-}${UV_NO_MANAGED_PYTHON-}${VIRTUAL_ENV-}${UV_WORKING_DIR-}${UV_WORKING_DIRECTORY-}" ] || exit 95\n'
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$*" in\n'
        '  --version) printf "uv 0.12.6\\n";;\n'
        '  "python install --no-bin --no-registry cpython@3.13.12") exit 0;;\n'
        '  "python find --no-project --system --managed-python --no-python-downloads cpython@3.13.12")\n'
        '    printf "%s\\n" "$MANAGED";;\n'
        '  *) exit 96;;\nesac\n',
    )
    uv.chmod(0o700)
    result = subprocess.run(
        [bash, "-c", '. "$1"; resolve_uv_bootstrap_python "$2" "$3"', "bootstrap",
         str(PROVIDER.with_suffix(".sh")), str(request), operation],
        capture_output=True, text=True, timeout=15,
        env={
            **os.environ, "PATH": str(tools) + os.pathsep + os.defpath,
            "CALLS": str(log), "MANAGED": str(python), "UV_PYTHON": "foreign",
            "UV_NO_MANAGED_PYTHON": "1", "VIRTUAL_ENV": "foreign",
            "UV_WORKING_DIR": "foreign", "UV_WORKING_DIRECTORY": "foreign",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == str(python) + "\n"
    assert log.read_text().splitlines() == [
        "--version",
        *(["python install --no-bin --no-registry cpython@3.13.12"] if operation == "Prepare" else []),
        "python find --no-project --system --managed-python --no-python-downloads cpython@3.13.12",
    ]


@pytest.mark.parametrize("content", [
    b"3.13.12\n", b"cpython@3.13.12", b"cpython@3.13.12\n\n", b"cpython@3.13.12\0\n",
])
def test_posix_bootstrap_rejects_malformed_requests_before_uv(
    tmp_path: Path, content: bytes,
) -> None:
    bash = shutil.which("bash")
    if not bash or os.name == "nt":
        pytest.skip("This control exercises the POSIX shell provider")
    request = tmp_path / "request.txt"
    request.write_bytes(content)
    tools = tmp_path / "tools"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text('#!/bin/sh\nprintf "UV_MUST_NOT_RUN\\n" >&2\nexit 97\n')
    uv.chmod(0o700)
    result = subprocess.run(
        [bash, "-c", '. "$1"; resolve_uv_bootstrap_python "$2" Prepare', "bootstrap",
         str(PROVIDER.with_suffix(".sh")), str(request)],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "PATH": str(tools) + os.pathsep + os.defpath},
    )
    assert result.returncode == 1
    assert "must declare one exact stable CPython" in result.stderr
    assert "UV_MUST_NOT_RUN" not in result.stderr


@pytest.fixture(params=("pwsh", "powershell"))
def host(request: pytest.FixtureRequest) -> str:
    executable = shutil.which(str(request.param))
    if not executable:
        pytest.skip(f"{request.param} is unavailable")
    return executable


def _run(
    host: str, tmp_path: Path, responses: list[dict[str, Any]], *,
    request: bytes = b"cpython@3.11.11\n", operation: str = "Inspect",
    uv: bool = True, isolate: bool = True, callback_environment: bool = True,
) -> dict[str, Any]:
    request_file = tmp_path / "bootstrap-python-version.txt"
    request_file.write_bytes(request)
    uv_file = tmp_path / "uv application"
    uv_file.touch()
    python = tmp_path / "managed python"
    python.touch()
    for response in responses:
        if response.get("Stdout") == "PYTHON":
            response["Stdout"] = str(python) + "\n"
    script = tmp_path / "provider-test.ps1"
    source = r"""
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. $env:PROVIDER
$responses = ConvertFrom-Json $env:RESPONSES
$script:calls = [Collections.Generic.List[object]]::new()
function Get-Command {
    param($Name, $CommandType, $ErrorAction)
    if ($Name -cne 'uv' -or $CommandType -cne 'Application') {
        throw 'Ambient Python, aliases and functions must not be discovered.'
    }
    if ($env:HAS_UV -eq '1') { [pscustomobject]@{Source = $env:FIXTURE_UV} }
}
$invoke = {
    param($FilePath, $ArgList, $Environment)
    if ($FilePath -cne $env:FIXTURE_UV) { throw 'Wrong uv application.' }
    if ($env:ISOLATE -ceq '1') {
        $keys = @(
            'CONDA_PREFIX', 'PYTHONHOME', 'PYTHONPATH', 'UV_ENV_FILE', 'UV_FROZEN',
            'UV_ISOLATED', 'UV_LOCKED', 'UV_MANAGED_PYTHON', 'UV_NO_MANAGED_PYTHON',
            'UV_NO_SYNC', 'UV_PROJECT', 'UV_PYTHON', 'UV_PYTHON_PREFERENCE',
            'UV_WORKING_DIR', 'UV_WORKING_DIRECTORY', 'VIRTUAL_ENV'
        )
        if ((@($Environment.Keys | Sort-Object) -join ',') -cne ($keys -join ',') -or
            @($keys | Where-Object { $null -ne $Environment[$_] }).Count -ne 0) {
            throw 'Every uv stage requires the complete null-valued selector override map.'
        }
    } elseif ($PSBoundParameters.ContainsKey('Environment') -or $args.Count -ne 0) {
        throw 'A legacy capture callback received new override arguments.'
    }
    $script:calls.Add(@($ArgList))
    $response = $responses[$script:calls.Count - 1]
    if ($response.PSObject.Properties['Throw']) { throw $response.Throw }
    return $response
}
try {
    $selected = Resolve-UvBootstrapPython -RequestFile $env:REQUEST_FILE `
        -InvokeCaptured $invoke -Operation $env:OPERATION -IsolateSelection
    @{Selected = $selected; Calls = @($script:calls.ToArray())} | ConvertTo-Json -Depth 10 -Compress
} catch {
    @{Error = $_.Exception.Message; Calls = @($script:calls.ToArray())} | ConvertTo-Json -Depth 10 -Compress
}
"""
    if not callback_environment:
        source = source.replace("param($FilePath, $ArgList, $Environment)", "param($FilePath, $ArgList)")
    if not isolate:
        source = source.replace(" -IsolateSelection", "")
    script.write_text(source, encoding="utf-8-sig")
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={
            **os.environ, "PROVIDER": str(PROVIDER), "REQUEST_FILE": str(request_file),
            "FIXTURE_UV": str(uv_file), "HAS_UV": "1" if uv else "0",
            "ISOLATE": "1" if isolate else "0",
            "RESPONSES": json.dumps(responses), "OPERATION": operation,
        },
        **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW")} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr
    value: dict[str, Any] = json.loads(result.stdout)
    return value


def _result(stdout: str = "", *, code: int = 0, stderr: str = "") -> dict[str, Any]:
    return {"ExitCode": code, "Stdout": stdout, "Stderr": stderr}


FIND = [
    "python", "find", "--no-project", "--system", "--managed-python",
    "--no-python-downloads", "cpython@3.11.11",
]


@pytest.mark.parametrize("operation,line_ending", [
    ("Inspect", b"\n"), ("Inspect", b"\r\n"), ("Prepare", b"\n"), ("prepare", b"\r\n"),
])
def test_managed_selection_preserves_exact_request_and_operation(
    host: str, tmp_path: Path, operation: str, line_ending: bytes,
) -> None:
    prepare = operation.lower() == "prepare"
    responses = [_result("uv 0.8.0\n")]
    if prepare:
        responses.append(_result(stderr="installation progress is not a Python path\n"))
    responses.append(_result("PYTHON", stderr="diagnostic is not a Python path\n"))
    result = _run(
        host, tmp_path, responses,
        operation=operation, request=b"cpython@3.11.11" + line_ending,
    )
    assert result["Selected"] == {
        "Status": "selected", "Reason": "uv-managed-python",
        "Uv": str(tmp_path / "uv application"), "Python": str(tmp_path / "managed python"),
        "Requirement": "cpython@3.11.11",
    }
    assert result["Calls"] == [
        ["--version"],
        *([["python", "install", "--no-bin", "--no-registry", "cpython@3.11.11"]] if prepare else []),
        FIND,
    ]


@pytest.mark.parametrize("exit_code", [1, 2])
def test_unprepared_lookup_never_acquires(host: str, tmp_path: Path, exit_code: int) -> None:
    result = _run(host, tmp_path, [
        _result("uv 0.9.4 (release)\n"),
        _result(code=exit_code, stderr="error: No interpreter found for CPython 3.11.11 in managed installations\n"),
    ])
    assert result["Selected"]["Reason"] == "managed-python-not-prepared"
    assert result["Selected"]["Python"] is None
    assert result["Calls"] == [["--version"], FIND]


def test_missing_uv_does_not_discover_python(host: str, tmp_path: Path) -> None:
    result = _run(host, tmp_path, [], uv=False)
    assert result["Selected"]["Reason"] == "uv-not-on-path"
    assert result["Calls"] == []


def test_legacy_capture_contract_remains_usable_without_selection_isolation(host: str, tmp_path: Path) -> None:
    result = _run(
        host, tmp_path, [_result("uv 0.8.1\n"), _result("PYTHON")],
        isolate=False, callback_environment=False,
    )
    assert result["Selected"]["Reason"] == "uv-managed-python"
    assert result["Calls"] == [["--version"], FIND]


def test_selection_isolation_refuses_an_older_callback_before_execution(host: str, tmp_path: Path) -> None:
    result = _run(host, tmp_path, [], callback_environment=False)
    assert "must accept child Environment overrides" in result["Error"]
    assert result["Calls"] == []


@pytest.mark.parametrize("declaration", [
    b"cpython@3.11\n", b"3.11.11\n", b"cpython@03.11.11\n",
    b"cpython@3.11.11\nextra", b"cpython@3.11.11", b"cpython@3.11.11\r\r\n", b"x" * 65,
])
def test_invalid_declaration_refuses_before_uv(
    host: str, tmp_path: Path, declaration: bytes,
) -> None:
    result = _run(host, tmp_path, [], request=declaration)
    assert "Bootstrap Python request" in result["Error"]
    assert result["Calls"] == []


@pytest.mark.parametrize("response", [
    _result("uv 0.7.99\n"), _result("uv 0.8.0rc1\n"), _result("other 0.9.0\n"),
    _result(code=1, stderr="version failure"), {"ExitCode": None, "Stdout": "", "Stderr": ""},
    {"Throw": "caller deadline/cleanup failure"},
])
def test_unqualified_uv_or_unconfirmed_custody_stops(
    host: str, tmp_path: Path, response: dict[str, Any],
) -> None:
    result = _run(host, tmp_path, [response], operation="Prepare")
    assert "Error" in result
    assert result["Calls"] == [["--version"]]


@pytest.mark.parametrize("stage", ["version", "install", "find"])
def test_policy_refusal_does_not_retry_or_fall_back(
    host: str, tmp_path: Path, stage: str,
) -> None:
    steps = ["version", "install", "find"]
    responses = [_result("uv 0.8.1\n"), _result(), _result("PYTHON")]
    responses[steps.index(stage)] = _result(code=1, stderr="Access denied (os error 448)")
    result = _run(host, tmp_path, responses, operation="Prepare")
    assert "operating-system policy" in result["Error"]
    assert len(result["Calls"]) == steps.index(stage) + 1


@pytest.mark.parametrize("response", [
    _result("relative-python\n"), _result("one\nsecond\n"),
    _result(code=1, stderr="permission denied"),
    _result(code=2, stderr="invalid configuration"),
])
def test_failed_or_malformed_lookup_is_not_a_missing_interpreter(
    host: str, tmp_path: Path, response: dict[str, Any],
) -> None:
    result = _run(host, tmp_path, [_result("uv 0.8.1\n"), response])
    assert "Error" in result
    assert result["Calls"] == [["--version"], FIND]


def test_real_uv_lookup_uses_production_capture_without_path_python(
    host: str, tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This read-only native witness requires existing uv; it never installs it.")
    request_file = tmp_path / "bootstrap-python-version.txt"
    request_file.write_bytes(b"cpython@3.11.11\r\n")
    script = tmp_path / "native-lookup.ps1"
    script.write_text(
        r"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
. $env:CAPTURE_HELPER
. $env:PROVIDER
$env:PATH = $env:UV_ONLY_PATH
$env:UV_PYTHON = $null
$script:calls = [Collections.Generic.List[object]]::new()
$invoke = {
    param($FilePath, $ArgList, $Environment)
    $script:calls.Add(@($ArgList))
    $errors = ''
    $output = Invoke-Hidden -FilePath $FilePath -ArgList $ArgList `
        -ErrorOutput ([ref]$errors) -Environment $Environment
    [pscustomobject]@{ExitCode = $LASTEXITCODE; Stdout = [string]$output; Stderr = $errors}
}
$selected = Resolve-UvBootstrapPython -RequestFile $env:REQUEST_FILE -InvokeCaptured $invoke -IsolateSelection
@{Selected = $selected; Calls = @($script:calls.ToArray())} | ConvertTo-Json -Depth 10 -Compress
""",
        encoding="utf-8-sig",
    )
    uv_only = tmp_path / "uv-only"
    uv_only.mkdir()
    # Copy the already available standalone uv, never Python or another host tool.
    shutil.copy2(uv, uv_only / Path(uv).name)
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        env={
            **os.environ,
            "CAPTURE_HELPER": str(ROOT / "libs/versioned-runtime/versioned_runtime_native.ps1"),
            "PROVIDER": str(PROVIDER), "REQUEST_FILE": str(request_file),
            "UV_ONLY_PATH": str(uv_only), "UV_PYTHON_INSTALL_DIR": str(tmp_path / "empty-python"),
            "UV_CACHE_DIR": str(tmp_path / "cache"), "UV_PYTHON_DOWNLOADS": "never",
        },
        **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW")} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr
    value = json.loads(result.stdout)
    assert value["Selected"]["Reason"] == "managed-python-not-prepared"
    assert value["Calls"] == [["--version"], FIND]
    assert not (tmp_path / "empty-python").exists()


def test_provider_is_materialized_only_to_declared_native_consumer(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    scripts = candidate / "plugins/agent-worktrees/scripts"
    scripts.mkdir(parents=True)
    source_scripts = ROOT / "plugins/agent-worktrees/scripts"
    for name in ier.native_files(ROOT / "plugins/agent-worktrees", ROOT):
        source = source_scripts / name
        if source.is_file():
            shutil.copyfile(source, scripts / name)
    before = PROVIDER.read_bytes()
    log = materializer.materialize_derived_files(
        candidate, canonical_root=ROOT,
        copied_consumer=(ROOT / "plugins/agent-worktrees", candidate / "plugins/agent-worktrees"),
        file_names=frozenset({"bootstrap-uv.ps1"}),
    )
    assert log and not any(line.startswith("SKIP") for line in log), log
    assert (scripts / "bootstrap-uv.ps1").read_bytes() == before
    assert PROVIDER.read_bytes() == before
    assert not (candidate / "plugins/agent-codespaces/scripts/bootstrap-uv.ps1").exists()
    (scripts / "bootstrap-uv.ps1").write_text("foreign candidate\n")
    refused = materializer.materialize_derived_files(
        candidate, canonical_root=ROOT,
        copied_consumer=(ROOT / "plugins/agent-worktrees", candidate / "plugins/agent-worktrees"),
        file_names=frozenset({"bootstrap-uv.ps1"}),
    )
    assert refused and all(line.startswith("SKIP") for line in refused), refused
    assert (scripts / "bootstrap-uv.ps1").read_text() == "foreign candidate\n"
