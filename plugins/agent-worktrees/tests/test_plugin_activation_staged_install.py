from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


REPO = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO / "plugins" / "agent-worktrees"
pytestmark = pytest.mark.guard
LIBRARIES = ("plugin-activation", "dropin-registry", "plugin-resolve")


def _run_context_stage(
    payload: Path, stage: Path, tmp_path: Path,
) -> subprocess.CompletedProcess[str]:
    if sys.platform == "win32":
        host = shutil.which("pwsh") or shutil.which("powershell")
        if host is None:
            pytest.skip("PowerShell is required for the Windows context stager")
        installer = (PLUGIN_ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
        start = installer.index("        # Preserve ../../libs references")
        end = installer.index("        $contextStagedEntry =", start)
        script = tmp_path / "stage.ps1"
        script.write_text(
            "param([string]$contextPayload, [string]$contextStage)\n"
            "$ErrorActionPreference = 'Stop'\n"
            + installer[start:end]
            + "\nWrite-Output $contextStagedPayload\n",
            encoding="utf-8",
        )
        command = [host, "-NoProfile", "-File", str(script), str(payload), str(stage)]
    else:
        host = shutil.which("bash")
        if host is None:
            pytest.skip("Bash is required for the POSIX context stager")
        installer = (PLUGIN_ROOT / "scripts/install.sh").read_text(encoding="utf-8")
        start = installer.index("        # Preserve ../../libs references")
        end = installer.index("        for __aw_sibling", start)
        script = (
            'set -euo pipefail\nPLUGIN_DIR="$1"\n__aw_stage="$2"\n'
            + installer[start:end]
            + '\nprintf "%s\\n" "$__aw_staged_payload"\n'
        )
        command = [host, "-c", script, "stage", str(payload), str(stage)]
    return subprocess.run(
        command, capture_output=True, text=True, timeout=30, check=False,
        **no_window_kwargs(),
    )


def _context_payload(tmp_path: Path, *, packaged: bool) -> Path:
    payload = tmp_path / "original source" / "plugins" / "agent-worktrees"
    payload.mkdir(parents=True)
    libraries = payload / "libs" if packaged else payload.parents[1] / "libs"
    prefix = "libs" if packaged else "../../libs"
    sources = "[tool.uv.sources]\n"
    for library in LIBRARIES:
        source = libraries / library / "src" / library.replace("-", "_")
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("CAPTURED = True\n", encoding="utf-8")
        sources += f'agent-{library} = {{ path = "{prefix}/{library}" }}\n'
    (payload / "pyproject.toml").write_text(sources, encoding="utf-8")
    return payload


@pytest.mark.parametrize("packaged", [False, True])
def test_actual_context_stager_captures_declared_dependencies(tmp_path: Path, packaged: bool) -> None:
    payload = _context_payload(tmp_path, packaged=packaged)
    stage = tmp_path / "context stage"
    result = _run_context_stage(payload, stage, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    staged_payload = Path(result.stdout.strip())
    assert staged_payload == stage / "plugins" / "agent-worktrees"
    shutil.rmtree(payload.parents[1])
    project = tomllib.loads((staged_payload / "pyproject.toml").read_text(encoding="utf-8"))
    for library in LIBRARIES:
        declared = project["tool"]["uv"]["sources"][f"agent-{library}"]["path"]
        source = (staged_payload / declared).resolve()
        assert source.is_relative_to(stage.resolve())
        module = source / "src" / library.replace("-", "_") / "__init__.py"
        assert module.read_text(encoding="utf-8") == "CAPTURED = True\n"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symbolic-link refusal")
def test_context_stager_refuses_indirected_canonical_dependencies(tmp_path: Path) -> None:
    payload = _context_payload(tmp_path, packaged=False)
    library = payload.parents[1] / "libs" / "plugin-activation"
    (library / "linked").symlink_to(payload / "pyproject.toml")
    result = _run_context_stage(payload, tmp_path / "stage", tmp_path)
    assert result.returncode != 0
    assert "symbolic links" in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction refusal")
def test_context_stager_refuses_canonical_dependency_junction(tmp_path: Path) -> None:
    payload = _context_payload(tmp_path, packaged=False)
    target = tmp_path / "junction target"
    target.mkdir()
    junction = payload.parents[1] / "libs" / "plugin-activation" / "linked"
    host = shutil.which("pwsh") or shutil.which("powershell")
    if host is None:
        pytest.skip("PowerShell is required for the Windows context stager")
    script = tmp_path / "junction.ps1"
    script.write_text(
        "param([string]$Link, [string]$Target)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "New-Item -ItemType Junction -Path $Link -Target $Target | Out-Null\n",
        encoding="utf-8",
    )
    subprocess.run(
        [host, "-NoProfile", "-File", str(script), str(junction), str(target)],
        capture_output=True, text=True, timeout=30, check=True, **no_window_kwargs(),
    )
    result = _run_context_stage(payload, tmp_path / "stage", tmp_path)
    assert result.returncode != 0
    assert "reparse points" in result.stderr


def _copy_installed_package(stage_root: Path, site_packages: Path, lib: str) -> None:
    src_pkg = stage_root / lib / "src" / lib.replace("-", "_")
    dst_pkg = site_packages / lib.replace("-", "_")
    shutil.copytree(src_pkg, dst_pkg)


def test_plugin_activation_import_survives_stage_cleanup_when_installed_from_real_copy(
    tmp_path: Path,
) -> None:
    """Regression for #4788: a staged non-editable install may record its
    source as a throwaway `.install-stage/...` tree, then delete that tree
    after installation. The installed `agent-worktrees` vendored
    `plugin-activation` copy must remain importable anyway -- the runtime
    package itself must be self-contained, not a passthrough stub that
    still needs the staging source to exist later."""
    stage_root = tmp_path / ".install-stage" / "agent-worktrees"
    site_packages = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True)
    project = tomllib.loads((PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sources = project["tool"]["uv"]["sources"]

    for lib in ("plugin-activation", "dropin-registry", "plugin-resolve"):
        source = (PLUGIN_ROOT / sources[f"agent-{lib}"]["path"]).resolve()
        shutil.copytree(source, stage_root / lib)
        _copy_installed_package(stage_root, site_packages, lib)

    dist_info = site_packages / "agent_plugin_activation-0.1.0.dev0.dist-info"
    dist_info.mkdir()
    (dist_info / "direct_url.json").write_text(
        json.dumps({"url": stage_root.as_uri(), "dir_info": {}}) + "\n",
        encoding="utf-8",
    )

    shutil.rmtree(stage_root)
    assert not stage_root.exists()

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, plugin_activation;"
                "print(json.dumps({"
                "'pkg': plugin_activation.__file__, "
                "'resolver': plugin_activation.resolve_active_plugins.__module__"
                "}))"
            ),
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(site_packages)},
        timeout=30,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert Path(payload["pkg"]).is_relative_to(site_packages)
    assert payload["resolver"] == "plugin_activation.resolver"
