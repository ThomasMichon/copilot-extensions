"""PATH Python discovery without interpreter attestation or implicit acquisition."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "libs/installer-engine/bootstrap-python.ps1"


@pytest.fixture(params=("pwsh", "powershell"))
def host(request):
    executable = shutil.which(request.param)
    if not executable:
        pytest.skip(f"{request.param} is unavailable")
    return executable


def _run(host, tmp_path, body, *, environment=None, success=True):
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
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    if success:
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        return json.loads(result.stdout)
    return result


def _application(directory):
    directory.mkdir()
    application = directory / ("python.exe" if os.name == "nt" else "python")
    # Discovery must not execute or inspect these intentionally non-Python bytes.
    application.write_bytes(b"not an interpreter\n")
    application.chmod(0o755)
    return application


def test_discovery_uses_path_order_without_executing_candidates(host, tmp_path):
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
def test_setup_owns_path_hygiene_not_a_runtime_allowlist(host, tmp_path, kind):
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


def test_alias_and_function_do_not_shadow_path_application(host, tmp_path):
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


def test_missing_python_is_an_explicit_prerequisite_outcome(host, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = _run(
        host, tmp_path, "Resolve-EvaluatorBootstrapPython | ConvertTo-Json -Compress",
        environment={"PATH": str(empty)},
    )
    assert result == {
        "Status": "unavailable", "Reason": "python-not-on-path", "Python": None, "Guard": None,
    }


def test_discovery_errors_are_not_reported_as_missing_python(host, tmp_path):
    result = _run(host, tmp_path, """
function Get-Command { throw [IO.IOException]::new('discovery fixture failure') }
Resolve-EvaluatorBootstrapPython
""", success=False)
    assert result.returncode != 0
    assert "discovery fixture failure" in result.stderr


def test_discovery_does_not_compile_attest_or_launch(host, tmp_path):
    result = _run(host, tmp_path, r"""
$errors=$null
$tokens=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($env:SELECTION_HELPER, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Bootstrap helper must parse cleanly.' }
$commands=@($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.CommandAst]
}, $true))
$forbidden=@($commands | Where-Object {
    $_.InvocationOperator -ne [Management.Automation.Language.TokenKind]::Unknown -or
    $_.GetCommandName() -notin @('Get-Command')
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


@pytest.mark.parametrize("plugin", ("agent-codespaces", "agent-containers"))
@pytest.mark.parametrize("condition", ("missing", "startup-fails"))
def test_runtime_gate_does_not_fall_back_after_prerequisite_failure(
    host, tmp_path, plugin, condition,
):
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
