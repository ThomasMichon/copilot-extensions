"""PATH Python discovery without interpreter attestation or implicit acquisition."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Literal, overload

import pytest

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "libs/installer-engine/bootstrap-python.ps1"


@pytest.fixture(params=("pwsh", "powershell"))
def host(request: pytest.FixtureRequest) -> str:
    executable = shutil.which(str(request.param))
    if not executable:
        pytest.skip(f"{request.param} is unavailable")
    return executable


@overload
def _run(
    host: str, tmp_path: Path, body: str, *,
    environment: dict[str, str] | None = None, success: Literal[True] = True,
) -> dict[str, Any]: ...


@overload
def _run(
    host: str, tmp_path: Path, body: str, *,
    environment: dict[str, str] | None = None, success: Literal[False],
) -> subprocess.CompletedProcess[str]: ...


def _run(
    host: str, tmp_path: Path, body: str, *,
    environment: dict[str, str] | None = None, success: bool = True,
) -> dict[str, Any] | subprocess.CompletedProcess[str]:
    child_env = dict(environment or {})
    test_path = child_env.pop("PATH", "")
    script = tmp_path / "probe.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        "if ($env:BOOTSTRAP_TEST_PATH) { $env:PATH = $env:BOOTSTRAP_TEST_PATH }\n"
        ". $env:SELECTION_HELPER\n" + body,
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, encoding="utf-8",
        env={
            **os.environ, "SELECTION_HELPER": str(HELPER), "PYTHONUTF8": "1",
            **child_env, "BOOTSTRAP_TEST_PATH": test_path,
        },
        timeout=20,
        **({"creationflags": getattr(subprocess, "CREATE_NO_WINDOW")} if os.name == "nt" else {}),
    )
    if success:
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        value: dict[str, Any] = json.loads(result.stdout)
        return value
    return result


def _application(directory: Path) -> Path:
    directory.mkdir()
    application = directory / ("python.exe" if os.name == "nt" else "python")
    # Discovery must not execute or inspect these intentionally non-Python bytes.
    application.write_bytes(b"not an interpreter\n")
    application.chmod(0o755)
    return application


def test_discovery_uses_path_order_without_executing_candidates(host: str, tmp_path: Path) -> None:
    first = _application(tmp_path / "first's \u00e9")
    second = _application(tmp_path / "second")
    result = _run(
        host, tmp_path, "Resolve-EvaluatorBootstrapPython | ConvertTo-Json -Compress",
        environment={"PATH": os.pathsep.join((str(first.parent), str(second.parent)))},
    )
    assert result == {
        "Status": "selected", "Reason": "path-python", "Python": str(first), "Guard": None,
    }


@pytest.mark.parametrize("kind", ("venv", "embedded", "excluded"))
def test_setup_owns_path_hygiene_not_a_runtime_allowlist(host: str, tmp_path: Path, kind: str) -> None:
    python = _application(tmp_path / kind)
    if kind == "venv":
        (python.parent / "pyvenv.cfg").write_text("home = elsewhere\n", encoding="utf-8")
    if kind == "embedded":
        (python.parent / "python._pth").write_text("custom\n", encoding="utf-8")
    result = _run(
        host, tmp_path,
        "Resolve-EvaluatorBootstrapPython -ExcludedRoots @($env:PATH) | ConvertTo-Json -Compress",
        environment={"PATH": str(python.parent)},
    )
    assert result["Status"] == "selected"
    assert result["Python"] == str(python)
    assert result["Guard"] is None


def test_alias_and_function_do_not_shadow_path_application(host: str, tmp_path: Path) -> None:
    python = _application(tmp_path / "bin")
    result = _run(
        host, tmp_path, """
function python { throw 'Function must not be used.' }
Set-Alias python Write-Error
Resolve-EvaluatorBootstrapPython | ConvertTo-Json -Compress
""",
        environment={"PATH": str(python.parent)},
    )
    assert result["Python"] == str(python)


def test_missing_python_is_an_explicit_prerequisite_outcome(host: str, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run(
        host, tmp_path, "Resolve-EvaluatorBootstrapPython | ConvertTo-Json -Compress",
        environment={"PATH": str(empty)},
    )
    assert result == {
        "Status": "unavailable", "Reason": "python-not-on-path", "Python": None, "Guard": None,
    }


@pytest.mark.parametrize("source", ["argument", "environment"])
def test_prepared_python_bypasses_ambient_discovery(host: str, tmp_path: Path, source: str) -> None:
    python = _application(tmp_path / "prepared")
    argument = "-Python $env:PREPARED_PYTHON" if source == "argument" else ""
    result = _run(
        host, tmp_path,
        "function Get-Command { throw 'Ambient discovery must not run' }\n"
        f"Resolve-EvaluatorBootstrapPython {argument} | ConvertTo-Json -Compress",
        environment={
            "PREPARED_PYTHON": str(python),
            "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON": (
                str(python) if source == "environment" else "ignored-relative-path"
            ),
        },
    )
    assert result == {
        "Status": "selected", "Reason": "explicit-python", "Python": str(python), "Guard": None,
    }


@pytest.mark.parametrize("python", ["python", "", "relative/python"])
def test_invalid_prepared_python_is_not_an_ambient_fallback(
    host: str, tmp_path: Path, python: str,
) -> None:
    result = _run(
        host, tmp_path,
        "function Get-Command { throw 'Ambient discovery must not run' }\n"
        "Resolve-EvaluatorBootstrapPython -Python $env:PREPARED_PYTHON",
        environment={"PREPARED_PYTHON": python}, success=False,
    )
    assert result.returncode != 0
    assert "absolute executable path" in result.stderr
    assert "Ambient discovery" not in result.stderr


def test_discovery_errors_are_not_reported_as_missing_python(host: str, tmp_path: Path) -> None:
    result = _run(host, tmp_path, """
function Get-Command { throw [IO.IOException]::new('discovery fixture failure') }
Resolve-EvaluatorBootstrapPython
""", success=False)
    assert result.returncode != 0
    assert "discovery fixture failure" in result.stderr


def test_discovery_does_not_compile_attest_or_launch(host: str, tmp_path: Path) -> None:
    result = _run(host, tmp_path, r"""
$errors=$null
$tokens=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:SELECTION_HELPER, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Bootstrap helper must parse cleanly.' }
$commands=@($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.CommandAst]
}, $true))
$adoption=@($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.IfStatementAst] -and
    @($node.Clauses[0].Item1.FindAll({
        param($condition)
        $condition -is [Management.Automation.Language.StringConstantExpressionAst] -and
        $condition.Value -eq 'bootstrap-python-launcher.txt'
    }, $true)).Count -eq 1
}, $true))
if ($adoption.Count -ne 1) { throw 'Expected one explicit adoption branch.' }
$adoptedCommands=@($adoption[0].Clauses[0].Item2.FindAll({
    param($node)
    $node -is [Management.Automation.Language.CommandAst]
}, $true))
$forbidden=@($commands | Where-Object {
    if ($_.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Ampersand) {
        $_ -notin $adoptedCommands -or $_.CommandElements[0].Extent.Text -ne '$selector'
    } else {
        $_.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown -or
        $_.GetCommandName() -notin @('Get-Command', 'Test-Path', 'Join-Path')
    }
})
[pscustomobject]@{forbidden=$forbidden.Count} | ConvertTo-Json -Compress
""")
    assert result == {"forbidden": 0}
    text = HELPER.read_text(encoding="utf-8")
    for obsolete in (
        "Add-Type", "DllImport", "Registry", "WinVerifyTrust", "Process.Start",
        "CreateProcess", "FileVersionInfo", "Get-AuthenticodeSignature", "Reflection",
    ):
        assert obsolete not in text


@pytest.mark.parametrize("selection", ("managed", "explicit", "missing", "failed"))
def test_declared_bootstrap_never_falls_back_to_discovery(
    host: str, tmp_path: Path, selection: str,
) -> None:
    scripts = tmp_path / "scripts"
    bootstrap = scripts / "_bootstrap_python"
    bootstrap.mkdir(parents=True)
    helper = scripts / HELPER.name
    helper.write_bytes(HELPER.read_bytes())
    (scripts / "bootstrap-python-launcher.txt").write_text("uv\n", encoding="ascii")
    if selection != "missing":
        (bootstrap / "select.ps1").write_text(
            "throw 'uv is unavailable'\n" if selection in ("explicit", "failed") else
            "$env:PREPARED_PYTHON\n",
            encoding="utf-8",
        )
    argument = "-Python $env:PREPARED_PYTHON" if selection == "explicit" else ""
    result = _run(
        host, tmp_path,
        "function Get-Command { throw 'Ambient discovery must not run' }\n"
        f"Resolve-EvaluatorBootstrapPython {argument} | ConvertTo-Json -Compress",
        environment={
            "SELECTION_HELPER": str(helper), "PREPARED_PYTHON": sys.executable,
            "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON": "",
        },
        success=False,
    )
    if selection in ("missing", "failed"):
        assert result.returncode != 0
        assert (
            "selector is unavailable" if selection == "missing" else "uv is unavailable"
        ) in result.stderr
        assert "Ambient discovery" not in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {
            "Status": "selected",
            "Reason": "explicit-python" if selection == "explicit" else "uv-managed-python",
            "Python": sys.executable, "Guard": None,
        }


@pytest.mark.parametrize("plugin", ("agent-codespaces", "agent-containers"))
@pytest.mark.parametrize("condition", ("missing", "startup-fails"))
def test_runtime_gate_does_not_fall_back_after_prerequisite_failure(
    host: str, tmp_path: Path, plugin: str, condition: str,
) -> None:
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    sdk = scripts / "installation-context"
    sdk.mkdir(parents=True)
    (scripts / "bootstrap-python.ps1").write_bytes(HELPER.read_bytes())
    gate = scripts / "runtime-gate.ps1"
    gate.write_bytes((ROOT / "plugins" / plugin / "scripts" / gate.name).read_bytes())
    (scripts / "resolve-runtime.ps1").write_text(
        "# Trace contract: normal/read-only/ready-v1\nthrow 'Runtime must not be resolved.'\n",
        encoding="utf-8",
    )
    installer = "install.ps1" if plugin == "agent-codespaces" else "init.ps1"
    (scripts / installer).write_text("throw 'Installer must not run.'\n", encoding="utf-8")
    (sdk / "installation-context.ps1").write_text(
        "Set-Content -LiteralPath $env:FALLBACK_SENTINEL -Value invoked\nexit 98\n",
        encoding="utf-8",
    )
    (sdk / "installation_context.py").write_text(
        "import sys\nsys.stderr.write('evaluator startup fixture failure\\n')\nsys.exit(97)\n",
        encoding="utf-8",
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    fallback = tmp_path / "compiled-fallback"
    prefix = ""
    if condition == "startup-fails":
        prefix = "function Get-Command { [pscustomobject]@{Path=$env:PROBE_PYTHON} }\n"
    result = _run(
        host, tmp_path, prefix + "& $env:RUNTIME_GATE\nexit $LASTEXITCODE\n", success=False,
        environment={
            "PATH": str(empty), "USERPROFILE": str(home), "HOME": str(home),
            "COPILOT_EXTENSIONS_CONTEXT": "contexts/marketplace/plugin/cells/user/install.json",
            f"{plugin.upper().replace('-', '_')}_HOME": "runtime",
            f"{plugin.upper().replace('-', '_')}_PAYLOAD_ROOT": str(payload),
            "PROBE_PYTHON": sys.executable, "RUNTIME_GATE": str(gate),
            "FALLBACK_SENTINEL": str(fallback),
        },
    )
    assert result.returncode == 126, result.stderr
    assert not fallback.exists()
    assert result.stdout == ""
    if condition == "missing":
        assert "Python 3.10+ must be available as python on PATH" in result.stderr
        assert "prerequisite setup" in result.stderr
    else:
        assert "evaluator startup fixture failure" in result.stderr
        assert "installation context could not be resolved" in result.stderr
