"""Guard: install.ps1's `Get-PayloadHash` must fingerprint the FULL
attributable runtime install input (pyproject.toml, src\\agent_pull_requests,
and any [tool.uv.sources] path-dependency directories) -- the PowerShell
counterpart to test_payload_hash_includes_src.py's bash coverage. The
effort's own validation contract requires BOTH installer entrypoints to be
proven, not just POSIX; a regression here (e.g. reverting to the old
pyproject.toml-only sha256) would otherwise only ever be caught on POSIX.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_VERSIONED_RUNTIME = _PLUGIN_ROOT / "scripts" / "versioned_runtime.py"

pytestmark = pytest.mark.guard


def _powershell() -> str:
    pwsh = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not pwsh:
        pytest.skip("PowerShell is unavailable")
    return pwsh


def _extract_get_payload_hash() -> str:
    """Extract Get-PayloadHash's exact source text via PowerShell's own
    AST parser (pwsh/powershell.exe is already a hard requirement for
    these tests), rather than assuming there is always another top-level
    `function` keyword following it in the file -- a naive text search for
    the next `function` would raise ValueError if Get-PayloadHash ever
    becomes the last function defined."""
    pwsh = _powershell()
    script = (
        "$ast = [System.Management.Automation.Language.Parser]::ParseFile("
        f"'{_INSTALL_PS1}', [ref]$null, [ref]$null)\n"
        "$fn = $ast.Find({ param($node) "
        "$node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and "
        "$node.Name -eq 'Get-PayloadHash' }, $true)\n"
        "if (-not $fn) { throw 'Get-PayloadHash function not found' }\n"
        "Write-Output $fn.Extent.Text\n"
    )
    result = subprocess.run(
        [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    body = result.stdout
    # $PSScriptRoot would reflect the TEMP wrapper script's own directory
    # when this extracted function is embedded and run standalone, not the
    # real scripts/ directory versioned_runtime.py actually lives in --
    # substitute the real, literal path instead of relying on that
    # automatic variable.
    real_scripts_dir = _VERSIONED_RUNTIME.parent
    body = body.replace("$PSScriptRoot", f"'{real_scripts_dir}'")
    return body


def _make_plugin_tree(root: Path) -> tuple[Path, Path]:
    plugin_dir = root / "plugin"
    pkg_src_dir = plugin_dir / "src" / "agent_pull_requests"
    pkg_src_dir.mkdir(parents=True)
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-pull-requests"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (pkg_src_dir / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    return plugin_dir, pkg_src_dir


def _ps_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_get_payload_hash(plugin_dir: Path, install_dir: Path) -> str:
    pwsh = _powershell()
    fn = _extract_get_payload_hash()
    real_python = sys.executable
    script_lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$PluginDir = {_ps_literal(str(plugin_dir))}",
        f"$PkgSrcDir = {_ps_literal(str(plugin_dir / 'src' / 'agent_pull_requests'))}",
        f"$InstallDir = {_ps_literal(str(install_dir))}",
        f"function Get-BootstrapPython {{ return {_ps_literal(real_python)} }}",
        fn,
        "$result = Get-PayloadHash",
        "Write-Output \"HASH:$result\"",
    ]
    script = "\n".join(script_lines)
    result = subprocess.run(
        [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    for line in result.stdout.splitlines():
        if line.startswith("HASH:"):
            out = line[len("HASH:"):].strip()
            assert out, f"expected a non-empty hash; stderr: {result.stderr}"
            return out
    raise AssertionError(f"no HASH: line in output: {result.stdout!r} / {result.stderr!r}")


def test_get_payload_hash_changes_when_src_content_changes(tmp_path: Path) -> None:
    plugin_dir, pkg_src_dir = _make_plugin_tree(tmp_path)
    install_dir = tmp_path / "install"

    before = _run_get_payload_hash(plugin_dir, install_dir)
    (pkg_src_dir / "__init__.py").write_text("x = 2\n", encoding="utf-8")
    after = _run_get_payload_hash(plugin_dir, install_dir)
    assert before != after, (
        "Get-PayloadHash did not change when src\\agent_pull_requests content "
        "changed -- it is still only hashing pyproject.toml"
    )


def test_get_payload_hash_changes_when_a_uv_sources_path_dependency_changes(
    tmp_path: Path,
) -> None:
    plugin_dir, _pkg_src_dir = _make_plugin_tree(tmp_path)
    shared_lib_dir = tmp_path / "libs" / "a-shared-lib"
    (shared_lib_dir / "src" / "a_shared_lib").mkdir(parents=True)
    (shared_lib_dir / "src" / "a_shared_lib" / "__init__.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-pull-requests"\nversion = "1.0.0"\n'
        "dependencies = [\"a-shared-lib\"]\n\n"
        "[tool.uv.sources]\n"
        'a-shared-lib = { path = "../libs/a-shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    install_dir = tmp_path / "install"

    before = _run_get_payload_hash(plugin_dir, install_dir)
    (shared_lib_dir / "src" / "a_shared_lib" / "__init__.py").write_text(
        "x = 2\n", encoding="utf-8"
    )
    after = _run_get_payload_hash(plugin_dir, install_dir)
    assert before != after, (
        "Get-PayloadHash did not change when a [tool.uv.sources] path "
        "dependency's content changed -- shared library source is still "
        "excluded from the fingerprint"
    )


def test_get_payload_hash_is_stable_for_identical_content(tmp_path: Path) -> None:
    plugin_dir, _pkg_src_dir = _make_plugin_tree(tmp_path)
    install_dir = tmp_path / "install"

    first = _run_get_payload_hash(plugin_dir, install_dir)
    second = _run_get_payload_hash(plugin_dir, install_dir)
    assert first == second


def test_get_payload_hash_matches_canonical_fingerprint_source(tmp_path: Path) -> None:
    """Agreement with calling fingerprint_source() directly proves genuine
    delegation to the canonical seam, not a parallel reimplementation."""
    plugin_dir, pkg_src_dir = _make_plugin_tree(tmp_path)
    install_dir = tmp_path / "install"

    ps_hash = _run_get_payload_hash(plugin_dir, install_dir)

    result = subprocess.run(
        [sys.executable, str(_VERSIONED_RUNTIME), "--root", str(install_dir), "fingerprint",
         str(plugin_dir / "pyproject.toml"), str(pkg_src_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert ps_hash == result.stdout.strip()


def test_get_payload_hash_fails_closed_on_a_genuine_fingerprint_error(tmp_path: Path) -> None:
    """A real failure computing the fingerprint (simulated via a
    missing/broken versioned_runtime.py path) must THROW, never be
    silently swallowed into an empty string: an empty hash is
    indistinguishable from "nothing changed" to every caller."""
    plugin_dir, _pkg_src_dir = _make_plugin_tree(tmp_path)
    install_dir = tmp_path / "install"
    pwsh = _powershell()
    fn = _extract_get_payload_hash()
    # Point the (already-substituted) versioned_runtime.py directory at a
    # broken location instead of the real scripts/ dir.
    broken_scripts_dir = tmp_path / "broken-scripts"
    broken_scripts_dir.mkdir()
    fn_broken = fn.replace(str(_VERSIONED_RUNTIME.parent), str(broken_scripts_dir))
    script_lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$PluginDir = {_ps_literal(str(plugin_dir))}",
        f"$PkgSrcDir = {_ps_literal(str(plugin_dir / 'src' / 'agent_pull_requests'))}",
        f"$InstallDir = {_ps_literal(str(install_dir))}",
        f"function Get-BootstrapPython {{ return {_ps_literal(sys.executable)} }}",
        fn_broken,
        "try {",
        "    Get-PayloadHash | Out-Null",
        "    Write-Output 'UNEXPECTED_SUCCESS'",
        "} catch {",
        "    Write-Output \"FAILED_AS_EXPECTED: $($_.Exception.Message)\"",
        "}",
    ]
    script = "\n".join(script_lines)
    result = subprocess.run(
        [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "FAILED_AS_EXPECTED" in result.stdout
    assert "UNEXPECTED_SUCCESS" not in result.stdout
    assert "Failed to compute payload fingerprint" in result.stdout
