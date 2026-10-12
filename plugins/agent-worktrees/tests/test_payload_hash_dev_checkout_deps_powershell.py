"""Guard: install.ps1's `Get-PayloadHash` must fingerprint a dev-checkout
[tool.uv.sources] `path = "../../libs/<lib>"` dependency's actual source
content -- the PowerShell counterpart to
test_payload_hash_dev_checkout_deps.py's bash coverage. The effort's own
validation contract requires BOTH installer entrypoints to be proven, not
just POSIX; a regression here (e.g. reverting to only the local libs/ scan)
would otherwise only ever be caught on POSIX.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"

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
    return result.stdout


def _ps_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _make_plugin_tree(root: Path) -> tuple[Path, Path]:
    """Return (plugin_dir, external_lib_src_dir) for a minimal fake plugin
    layout with ONE dev-checkout external path dependency declared via
    [tool.uv.sources], matching the real repo's
    `plugins/<name>` + `../../libs/<lib>` sibling layout."""
    plugin_dir = root / "repo" / "plugins" / "agent-worktrees"
    pkg_src_dir = plugin_dir / "src" / "agent_worktrees"
    pkg_src_dir.mkdir(parents=True)
    (pkg_src_dir / "__init__.py").write_text("x = 1\n", encoding="utf-8")

    ext_lib_dir = root / "repo" / "libs" / "some-shared-lib"
    ext_lib_src_dir = ext_lib_dir / "src" / "some_shared_lib"
    ext_lib_src_dir.mkdir(parents=True)
    (ext_lib_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-some-shared-lib"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (ext_lib_src_dir / "__init__.py").write_text("y = 1\n", encoding="utf-8")

    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-worktrees"\nversion = "1.0.0"\n'
        '[tool.uv.sources]\n'
        'agent-some-shared-lib = { path = "../../libs/some-shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    return plugin_dir, ext_lib_src_dir


def _run_get_payload_hash(plugin_dir: Path) -> str:
    pwsh = _powershell()
    fn = _extract_get_payload_hash()
    script_lines = [
        "$ErrorActionPreference = 'Stop'",
        f"$PluginDir = {_ps_literal(str(plugin_dir))}",
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


def test_get_payload_hash_changes_when_dev_checkout_dependency_src_changes(
    tmp_path: Path,
) -> None:
    plugin_dir, ext_lib_src_dir = _make_plugin_tree(tmp_path)

    before = _run_get_payload_hash(plugin_dir)
    (ext_lib_src_dir / "__init__.py").write_text("y = 2\n", encoding="utf-8")
    after = _run_get_payload_hash(plugin_dir)

    assert before != after, (
        "Get-PayloadHash did not change when the dev-checkout external "
        "[tool.uv.sources] path dependency's src content changed -- it is "
        "still invisible to the libs/ scan, which only sees the LOCAL "
        "(promoted/materialized) form"
    )


def test_get_payload_hash_still_changes_when_src_content_changes(tmp_path: Path) -> None:
    plugin_dir, _ext_lib_src_dir = _make_plugin_tree(tmp_path)
    pkg_src_dir = plugin_dir / "src" / "agent_worktrees"

    before = _run_get_payload_hash(plugin_dir)
    (pkg_src_dir / "__init__.py").write_text("x = 2\n", encoding="utf-8")
    after = _run_get_payload_hash(plugin_dir)

    assert before != after


def test_get_payload_hash_is_stable_for_identical_content(tmp_path: Path) -> None:
    plugin_dir, _ext_lib_src_dir = _make_plugin_tree(tmp_path)

    first = _run_get_payload_hash(plugin_dir)
    second = _run_get_payload_hash(plugin_dir)
    assert first == second


def test_get_payload_hash_ignores_generated_packaging_metadata(tmp_path: Path) -> None:
    plugin_dir, external_src = _make_plugin_tree(tmp_path / "ancestor.egg-info")
    local_src = plugin_dir / "libs" / "local-lib" / "src" / "local_lib"
    local_src.mkdir(parents=True)
    (local_src / "__init__.py").write_text("z = 1\n", encoding="utf-8")
    roots = (plugin_dir / "src", local_src.parent, external_src.parent)
    before = _run_get_payload_hash(plugin_dir)
    for root in roots:
        for suffix in ("egg-info", "dist-info"):
            metadata = root / f"generated.{suffix}"
            metadata.mkdir()
            (metadata / "PKG-INFO").write_text("generated build metadata\n", encoding="utf-8")
    assert _run_get_payload_hash(plugin_dir) == before
    (external_src / "runtime.json").write_text('{"enabled": true}\n', encoding="utf-8")
    assert _run_get_payload_hash(plugin_dir) != before


def test_get_payload_hash_does_not_double_hash_a_local_materialized_dependency(
    tmp_path: Path,
) -> None:
    """A LOCAL (already-promoted/materialized) `libs/<lib>` dependency is
    already fully covered by the pre-existing libs/ scan -- the new
    [tool.uv.sources] resolution must detect it resolves back under
    `$PluginDir/libs` and skip it, not hash it a second time."""
    plugin_dir = tmp_path / "repo" / "plugins" / "agent-worktrees"
    pkg_src_dir = plugin_dir / "src" / "agent_worktrees"
    pkg_src_dir.mkdir(parents=True)
    (pkg_src_dir / "__init__.py").write_text("x = 1\n", encoding="utf-8")

    local_lib_dir = plugin_dir / "libs" / "some-shared-lib"
    local_lib_src_dir = local_lib_dir / "src" / "some_shared_lib"
    local_lib_src_dir.mkdir(parents=True)
    (local_lib_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-some-shared-lib"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    (local_lib_src_dir / "__init__.py").write_text("y = 1\n", encoding="utf-8")

    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-worktrees"\nversion = "1.0.0"\n'
        '[tool.uv.sources]\n'
        'agent-some-shared-lib = { path = "libs/some-shared-lib", editable = true }\n',
        encoding="utf-8",
    )

    before = _run_get_payload_hash(plugin_dir)
    (local_lib_src_dir / "__init__.py").write_text("y = 2\n", encoding="utf-8")
    after = _run_get_payload_hash(plugin_dir)

    assert before != after
