"""PowerShell execution regression coverage for install.ps1's build-artifact
scrub (before AND after an install attempt, not just after) -- the Windows
counterpart to test_install_sh_build_artifact_scrub.py.

Installing FROM the pristine payload directory ($PluginDir, under the plugin
install root) leaves setuptools' own build/ + *.egg-info staging behind IN
that tree. Left in place, a stale build/ can silently shadow fresh src/ on a
later install if setuptools' incremental-build mtime check decides nothing
"changed" -- confirmed live on POSIX (copilot-extensions#3444): a truncated
recipes_cli.py shipped this way and crash-looped a production daemon for
~8h. The Windows installer had the identical gap (an after-only scrub in the
`finally` block): this module actually EXECUTES the extracted `$installPkg`
scriptblock under `pwsh`, with native offline UV fixtures, to prove the fix
works, not just that the source text is ordered a certain way.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "_dispatch_owned_fixtures", Path(__file__).with_name("test_installer_engine_adoption.py")
)
assert _FIXTURE_SPEC is not None and _FIXTURE_SPEC.loader is not None
_FIXTURES = importlib.util.module_from_spec(_FIXTURE_SPEC)
_FIXTURE_SPEC.loader.exec_module(_FIXTURES)
LIBRARIES = _FIXTURES.LIBRARIES
_stage_owned_source = _FIXTURES._stage_owned_source
_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(_PWSH is None, reason="pwsh is not available")


def _extract_function_block(name: str) -> str:
    """Extract one `function <name> { ... }` block by brace-counting from
    its own opening `{`."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    func_start = text.index(f"function {name}")
    brace_start = text.index("{", func_start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                source = _PLUGIN_ROOT / "scripts" / "source-runtime.ps1"
                return f". '{source}'\n" + text[func_start : i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting function {name!r}")


def _extract_install_pkg_block() -> str:
    """Extract `Remove-PluginBuildArtifacts` (which `$scrubArtifacts` now
    delegates to) plus the `$StaleCacheRefreshPackages = @(...)`
    declaration through the end of the `$installPkg = { ... }` scriptblock,
    by brace-counting from the scriptblock's own opening `{` (the leading
    `@(...)` array is copied verbatim first since $installPkg's body
    references it)."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    remove_artifacts_fn = _extract_function_block("Remove-PluginBuildArtifacts")
    array_start = text.index("$StaleCacheRefreshPackages = @(")
    block_start = text.index("$installPkg = {")
    brace_start = text.index("{", block_start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return remove_artifacts_fn + "\n\n" + text[array_start : i + 1]
        i += 1
    raise AssertionError("unbalanced braces extracting $installPkg")


def _run_harness(
    plugin_dir: Path, stub_body: str, extra_script: str, *,
    have_uv: bool = True, block: str | None = None, repository: Path | None = None,
) -> subprocess.CompletedProcess:
    libraries = {
        package: plugin_dir / "libs" / lib
        for package, lib in LIBRARIES.items()
        if (plugin_dir / "libs" / lib).is_dir()
    }
    if repository is not None:
        libraries["agent-zdd"] = repository / "libs" / "zdd"
    engine = _stage_owned_source(plugin_dir, libraries, repository=repository)
    fixture_bin = plugin_dir / "fixture-bin"
    fixture_bin.mkdir()
    driver = fixture_bin / "uv-driver.ps1"
    driver.write_text(
        """if ($args -contains '--show-settings') {
    Write-Output 'PipInstallSettings { settings: PipSettings {
    index_locations: IndexLocations {
        flat_index: [],
    },
    exclude_newer: ExcludeNewer {
        global: None,
        package: ExcludeNewerPackage({}),
    },
}, }'
    exit 0
}
"""
        + stub_body
        + f"""
$countPath = '{plugin_dir / "install-count.txt"}'
$count = if (Test-Path $countPath) {{ [int](Get-Content $countPath) }} else {{ 0 }}
[IO.File]::WriteAllText($countPath, [string]($count + 1))
uv @args
$code = $LASTEXITCODE
$roots = @('{plugin_dir}') + @(Get-ChildItem '{plugin_dir / "libs"}' -Directory -ErrorAction SilentlyContinue | ForEach-Object {{ $_.FullName }})
$roots += @($args | Where-Object {{ Test-Path -LiteralPath $_ -PathType Container }})
foreach ($root in $roots) {{
    foreach ($relative in @('build', 'some_pkg.egg-info', 'src/some_pkg.egg-info', 'some_vendored_pkg.egg-info', 'src/some_vendored_pkg.egg-info')) {{
        New-Item -ItemType Directory -Path (Join-Path $root $relative) -Force | Out-Null
    }}
}}
exit $code
""",
        encoding="utf-8",
    )
    uv = fixture_bin / ("uv.cmd" if os.name == "nt" else "uv")
    uv.write_text(
        f'@"{_PWSH}" -NoProfile -NonInteractive -File "{driver}" %*\n@exit /b %ERRORLEVEL%\n'
        if os.name == "nt"
        else f'#!/bin/sh\nexec "{_PWSH}" -NoProfile -NonInteractive -File "{driver}" "$@"\n',
        encoding="utf-8",
    )
    uv.chmod(0o755)
    env = os.environ.copy()
    for key in tuple(env):
        if key.startswith(("AGENT_", "COPILOT_PLUGIN_", "PYTHON", "UV_")):
            env.pop(key)
    env["PATH"] = str(fixture_bin)
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    extracted = block if block is not None else _extract_install_pkg_block()
    if not have_uv:
        # Spy only the unresolved executable boundary; retain the real policy
        # helper and both source-install/scrub calls.
        assert extracted.count("--uv uv --") == 2
        extracted = extracted.replace("--uv uv --", f"--uv '{uv}' --")
    harness = engine / "harness.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f'$PluginDir = "{plugin_dir}"\n'
        # Install-Runtime now builds from $BuildSrcDir (New-PluginBuildSnapshot's
        # durable per-version snapshot), never from the live $PluginDir -- see
        # that function's docstring. These tests only exercise scrub
        # correctness, so point $BuildSrcDir at the same fixture directory
        # $installPkg's extracted block actually reads/writes.
        f'$BuildSrcDir = "{plugin_dir}"\n'
        f"$VenvPython = '{sys.executable}'\n"
        + (f"$UvCommand = '{uv}'\n" if have_uv else "$UvCommand = $null\n")
        + f". '{engine / 'installer-engine.ps1'}'\n"
        + "function Write-Warn { param($Message) Write-Host $Message }\n"
        + "function Start-Sleep { param($Seconds) }\n"
        + "\n\n"
        + extracted
        + "\n\n"
        + extra_script
        + "\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        cwd=plugin_dir,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        check=True,
    )


def _seed_build_residue(plugin_dir: Path) -> None:
    (plugin_dir / "build" / "lib" / "some_pkg").mkdir(parents=True)
    (plugin_dir / "build" / "lib" / "some_pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (plugin_dir / "some_pkg.egg-info").mkdir()
    (plugin_dir / "some_pkg.egg-info" / "PKG-INFO").write_text("stub\n", encoding="utf-8")


def _seed_src_layout_egg_info(plugin_dir: Path) -> None:
    (plugin_dir / "src" / "some_pkg.egg-info").mkdir(parents=True)
    (plugin_dir / "src" / "some_pkg.egg-info" / "PKG-INFO").write_text("stub\n", encoding="utf-8")


def _seed_vendored_lib_build_residue(plugin_dir: Path, lib_name: str) -> Path:
    """Seed stale build/egg-info residue inside libs/<lib_name>/ -- the
    Windows counterpart to the POSIX helper of the same name (see
    test_install_sh_build_artifact_scrub.py)."""
    lib_dir = plugin_dir / "libs" / lib_name
    (lib_dir / "build" / "lib" / "some_vendored_pkg").mkdir(parents=True)
    (lib_dir / "build" / "lib" / "some_vendored_pkg" / "mod.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (lib_dir / "some_vendored_pkg.egg-info").mkdir()
    (lib_dir / "some_vendored_pkg.egg-info" / "PKG-INFO").write_text(
        "stub\n", encoding="utf-8"
    )
    (lib_dir / "src" / "some_vendored_pkg.egg-info").mkdir(parents=True)
    (lib_dir / "src" / "some_vendored_pkg.egg-info" / "PKG-INFO").write_text(
        "stub\n", encoding="utf-8"
    )
    return lib_dir


def test_vendored_lib_build_residue_is_also_scrubbed(tmp_path: Path) -> None:
    """Regression (2026-09-27, confirmed in a live POSIX deployment, same gap
    on Windows): every vendored `[tool.uv.sources]` workspace path dep
    under libs/<name>/ is its OWN independent setuptools build root and
    accumulates the identical build/*.egg-info residue as $PluginDir --
    the scrub must reach every immediate child of libs/, not just
    $PluginDir itself."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    procutil_dir = _seed_vendored_lib_build_residue(plugin_dir, "agent-procutil")
    zdd_dir = _seed_vendored_lib_build_residue(plugin_dir, "zdd")
    stub = f"""
function uv {{
    if ((Test-Path "{procutil_dir}\\build") -or (Test-Path "{procutil_dir}\\some_vendored_pkg.egg-info") `
        -or (Test-Path "{zdd_dir}\\build") -or (Test-Path "{zdd_dir}\\some_vendored_pkg.egg-info")) {{
        [Console]::Error.WriteLine('vendored lib residue still present at install time')
        $global:LASTEXITCODE = 1
        return
    }}
    Write-Output 'Installed 1 package'
    $global:LASTEXITCODE = 0
}}
"""
    extra = f"""
$result = & $installPkg "{plugin_dir}"
if ($result.Code -eq 0) {{ Write-Output "EXIT:0" }} else {{ Write-Output "EXIT:1" }}
Write-Output $result.Output
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXIT:0" in result.stdout
    assert "residue still present" not in result.stderr
    assert not (plugin_dir / "build").exists()
    for lib_dir in (procutil_dir, zdd_dir):
        assert not (lib_dir / "build").exists()
        assert not (lib_dir / "some_vendored_pkg.egg-info").exists()
        assert not (lib_dir / "src" / "some_vendored_pkg.egg-info").exists()


@pytest.mark.parametrize("code", [0, 23])
def test_uv_branch_scrubs_before_and_after(tmp_path: Path, code: int) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    stub = f"""
function uv {{ Write-Output 'fixture install'; $global:LASTEXITCODE = {code} }}
"""
    extra = f"""
$result = & $installPkg "{plugin_dir}"
Write-Output "EXIT:$($result.Code)"
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert f"EXIT:{code}" in result.stdout
    assert (plugin_dir / "install-count.txt").read_text() == "1"
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()


def test_src_layout_egg_info_is_also_scrubbed(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    _seed_src_layout_egg_info(plugin_dir)
    stub = """
function uv { Write-Output 'Installed 1 package'; $global:LASTEXITCODE = 0 }
"""
    extra = f"""
$result = & $installPkg "{plugin_dir}"
if ($result.Code -eq 0) {{ Write-Output "EXIT:0" }} else {{ Write-Output "EXIT:1" }}
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXIT:0" in result.stdout
    assert not (plugin_dir / "src" / "some_pkg.egg-info").exists()


def test_preexisting_residue_is_gone_before_uv_runs(tmp_path: Path) -> None:
    """Regression (2026-09-23, copilot-extensions#3444 review): the Windows
    path only scrubbed in the `finally` block after install -- pre-existing
    residue would still shadow the current install's own build. The stub
    `uv` function asserts the residue is already gone by the time it runs."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    _seed_src_layout_egg_info(plugin_dir)
    stub = f"""
function uv {{
    if ((Test-Path "{plugin_dir}\\build") -or (Test-Path "{plugin_dir}\\some_pkg.egg-info") `
        -or (Test-Path "{plugin_dir}\\src\\some_pkg.egg-info")) {{
        [Console]::Error.WriteLine('residue still present at install time')
        $global:LASTEXITCODE = 1
        return
    }}
    Write-Output 'Installed 1 package'
    $global:LASTEXITCODE = 0
}}
"""
    extra = f"""
$result = & $installPkg "{plugin_dir}"
if ($result.Code -eq 0) {{ Write-Output "EXIT:0" }} else {{ Write-Output "EXIT:1" }}
"""
    result = _run_harness(plugin_dir, stub, extra)
    assert "EXIT:0" in result.stdout
    assert "residue still present" not in result.stderr


def test_unresolved_uv_branch_rescrubs_between_policy_calls(tmp_path: Path) -> None:
    """The two policy-admitted UV calls must scrub between source builds."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    _seed_build_residue(plugin_dir)
    marker = tmp_path / "second-call.json"
    stub = f"""
function uv {{
    param()
    if ([int](Get-Content "{plugin_dir / 'install-count.txt'}") -eq 1) {{
        New-Item -ItemType Directory -Force -Path "{plugin_dir}\\build\\lib\\some_pkg" | Out-Null
        New-Item -ItemType Directory -Force -Path "{plugin_dir}\\some_pkg.egg-info" | Out-Null
        $global:LASTEXITCODE = 0
        return
    }}
    $residue = (Test-Path "{plugin_dir}\\build") -or (Test-Path "{plugin_dir}\\some_pkg.egg-info")
    Set-Content -Path "{marker}" -Value ([string]$residue)
    $global:LASTEXITCODE = 0
}}
"""
    extra = f"""
$result = & $installPkg "{plugin_dir}"
if ($result.Code -eq 0) {{ Write-Output "EXIT:0" }} else {{ Write-Output "EXIT:1" }}
"""
    extra += "\nWrite-Output $result.Output\n"
    result = _run_harness(plugin_dir, stub, extra, have_uv=False)
    assert "EXIT:0" in result.stdout, result.stdout + result.stderr
    assert marker.exists(), result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8").strip() == "False"
    assert (plugin_dir / "install-count.txt").read_text() == "2"
    assert not (plugin_dir / "build").exists()
    assert not (plugin_dir / "some_pkg.egg-info").exists()


def _extract_zdd_preinstall_block() -> str:
    """Extract the standalone zdd pre-install block (runs BEFORE
    $installPkg is even defined, from its own separately-resolved
    libs/zdd/ tree) plus Remove-PluginBuildArtifacts and Test-ZddInstalled,
    which it depends on. Brace-counts the full if/elseif/else chain from
    its own opening `{` so the extracted text is a syntactically complete,
    standalone statement."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    comment_start = text.index("# zdd (zero-downtime graceful-cutover primitives")
    if_start = text.index("if ($ZddDir) {", comment_start)
    brace_start = text.index("{", if_start)
    depth = 0
    i = brace_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                # Keep consuming through any trailing `elseif`/`else` arms
                # chained onto this same statement.
                j = i + 1
                while True:
                    rest = text[j:].lstrip()
                    if rest.startswith("elseif") or rest.startswith("else"):
                        arm_brace = text.index("{", j)
                        arm_depth = 0
                        k = arm_brace
                        while k < len(text):
                            if text[k] == "{":
                                arm_depth += 1
                            elif text[k] == "}":
                                arm_depth -= 1
                                if arm_depth == 0:
                                    break
                            k += 1
                        j = k + 1
                    else:
                        break
                block = text[comment_start:j]
                return "\n\n".join(
                    [
                        _extract_function_block("Remove-PluginBuildArtifacts"),
                        _extract_function_block("Test-ZddInstalled"),
                        block,
                    ]
                )
        i += 1
    raise AssertionError("unbalanced braces extracting the zdd pre-install block")


def test_standalone_zdd_preinstall_scrubs_before_building(tmp_path: Path) -> None:
    """Regression: the standalone zdd pre-install (which runs BEFORE the
    main $installPkg scriptblock is even defined, building from the exact
    same libs/zdd/ tree) must also scrub stale build artifacts first --
    otherwise a stale libs/zdd/build/lib can still be consumed by THIS
    earlier build, and the later main-install scrub only cleans up after
    the broken package has already been installed."""
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    zdd_dir = plugin_dir / "libs" / "zdd"
    (zdd_dir / "build" / "lib" / "zdd").mkdir(parents=True)
    (zdd_dir / "build" / "lib" / "zdd" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (zdd_dir / "some_pkg.egg-info").mkdir()

    stub = f"""
function uv {{
    param()
    if ((Test-Path "{zdd_dir}\\build") -or (Test-Path "{zdd_dir}\\some_pkg.egg-info")) {{
        [Console]::Error.WriteLine('residue still present at zdd install time')
        $global:LASTEXITCODE = 1
        return
    }}
    $global:LASTEXITCODE = 0
}}
"""
    setup = f"""
function Write-Ok {{ param($m) Write-Output "OK: $m" }}
function Write-Fail {{ param($m) throw $m }}
function Write-Skip {{ param($m) Write-Output "SKIP: $m" }}
function Resolve-Zdd {{ param($LibRoot) return "{zdd_dir}" }}
$prevEAP = $ErrorActionPreference
"""
    result = _run_harness(
        plugin_dir, stub, "", block=setup + _extract_zdd_preinstall_block(),
    )
    assert "OK: zdd" in result.stdout, result.stdout + result.stderr
    assert (plugin_dir / "install-count.txt").read_text() == "1"
    assert "residue still present" not in result.stderr, result.stdout + result.stderr
    assert not (zdd_dir / "build").exists()
    assert not (zdd_dir / "some_pkg.egg-info").exists()


def test_standalone_zdd_preinstall_scrubs_external_resolved_path(tmp_path: Path) -> None:
    """Regression: Resolve-Zdd (via Resolve-VendoredLib's registry/checkout
    fallback branches) can resolve to a path OUTSIDE $PluginDir entirely --
    a sibling copilot-extensions checkout, not the marketplace-installed
    payload. $PluginDir/libs/* scrubbing never reaches that tree, so the
    standalone zdd pre-install must pass its own resolved $ZddDir to
    Remove-PluginBuildArtifacts's -ExtraDir explicitly."""
    repository = tmp_path / "authoring"
    plugin_dir = repository / "plugins" / "agent-dispatch"
    plugin_dir.mkdir(parents=True)
    # Deliberately a SIBLING of plugin_dir, not nested under it -- not
    # reachable via $PluginDir/libs/* scrubbing at all.
    external_zdd_dir = repository / "libs" / "zdd"
    (external_zdd_dir / "build" / "lib" / "zdd").mkdir(parents=True)
    (external_zdd_dir / "build" / "lib" / "zdd" / "__init__.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (external_zdd_dir / "some_pkg.egg-info").mkdir()

    stub = f"""
function uv {{
    param()
    if ((Test-Path "{external_zdd_dir}\\build") -or (Test-Path "{external_zdd_dir}\\some_pkg.egg-info")) {{
        [Console]::Error.WriteLine('external residue still present at zdd install time')
        $global:LASTEXITCODE = 1
        return
    }}
    $global:LASTEXITCODE = 0
}}
"""
    setup = f"""
function Write-Ok {{ param($m) Write-Output "OK: $m" }}
function Write-Fail {{ param($m) throw $m }}
function Write-Skip {{ param($m) Write-Output "SKIP: $m" }}
function Resolve-Zdd {{ param($LibRoot) return "{external_zdd_dir}" }}
$prevEAP = $ErrorActionPreference
"""
    result = _run_harness(
        plugin_dir, stub, "", block=setup + _extract_zdd_preinstall_block(),
        repository=repository,
    )
    assert "OK: zdd" in result.stdout, result.stdout + result.stderr
    assert (plugin_dir / "install-count.txt").read_text() == "1"
    assert "external residue still present" not in result.stderr, result.stdout + result.stderr
    assert not (external_zdd_dir / "build").exists()
    assert not (external_zdd_dir / "some_pkg.egg-info").exists()
