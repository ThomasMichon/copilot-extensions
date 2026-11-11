"""Ordinary Dispatch admission uses the canonical complete local input graph."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
import time

import pytest

from .test_installer_engine_adoption import PLUGIN, _env, _function, _prelude, _run

pytestmark = [
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]

SCRIPTS = PLUGIN / "scripts"
VERSION = "1.2.3"


@pytest.fixture
def inputs(tmp_path):
    packages = {}
    for name, dependency in (("plugin", "direct"), ("direct", "transitive"), ("transitive", None)):
        package = tmp_path / name
        (package / "src").mkdir(parents=True)
        project = f'[project]\nname="{name}"\nversion="{VERSION}"\n'
        if dependency:
            project += (
                f'dependencies=["{dependency}"]\n[tool.uv.sources]\n'
                f'{dependency}={{path="../{dependency}"}}\n'
            )
        (package / "pyproject.toml").write_text(project, encoding="utf-8")
        (package / "src" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        packages[name] = package
    spec = importlib.util.spec_from_file_location(
        "dispatch_ordinary_input_source", SCRIPTS / "versioned_runtime_source.py",
    )
    assert spec and spec.loader
    source = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = source
    spec.loader.exec_module(source)
    return packages, source._runtime()


def _adapter(ext, plugin, runtime, *, force=False):
    if ext == "sh":
        return (
            f"SCRIPT_DIR='{SCRIPTS}'\nPLUGIN_DIR='{plugin}'\n"
            f"INSTALL_DIR='{runtime}'\nLINK_DIR='{runtime / '.venv'}'\n"
            f"SRC_VERSION='{VERSION}'\nVERSIONED_RUNTIME=1\nFORCE={int(force)}\n"
            "SOURCE_RUNTIME_IDENTITY=''\n"
            f"_bootstrap_python() {{ printf '%s\\n' '{sys.executable}'; }}\n"
            + _function(ext, "_payload_hash")
        )
    return (
        f"$ScriptDir='{SCRIPTS}'\n$PluginDir='{plugin}'\n$InstallDir='{runtime}'\n"
        f"$LinkDir='{runtime / '.venv'}'\n$SrcVersion='{VERSION}'\n"
        f"$VersionedRuntime=$true\n$Force=${str(force).lower()}\n"
        "$script:SourceRuntimeIdentity=$null\n"
        f". '{SCRIPTS / 'versioned_runtime_native.ps1'}'\n"
        f"function Get-BootstrapPython {{ param([switch]$ExternalOnly) return '{sys.executable}' }}\n"
        + _function(ext, "Get-PayloadHash")
    )


def _fingerprint(ext, tmp_path, env, plugin, runtime, *, force=False):
    script = _prelude(ext) + _adapter(ext, plugin, runtime, force=force)
    script += "_payload_hash\n" if ext == "sh" else "Get-PayloadHash\n"
    started = time.monotonic()
    result = _run(ext, script, tmp_path, env)
    print(f"{ext} fingerprint subprocess: {time.monotonic() - started:.3f}s", flush=True)
    assert result.returncode == 0, result.stdout + result.stderr
    digest = result.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", digest), result.stdout
    return digest


def _evidence(root):
    return {
        path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("mutation", ["plugin", "transitive"])
def test_ordinary_input_changes_refuse_completed_content_without_mutation(
    tmp_path, inputs, ext, mutation,
):
    packages, runtime_api = inputs
    env = _env(tmp_path)
    root = tmp_path / "runtime"
    digest = _fingerprint(ext, tmp_path, env, packages["plugin"], root)
    runtime_api.mark_complete(root, VERSION, payload_hash=digest)
    (root / "current-version").write_text(VERSION, encoding="utf-8")
    (root / "deploy-manifest.json").write_text('{"existing":true}', encoding="utf-8")
    before = _evidence(root)

    unchanged = _fingerprint(ext, tmp_path, env, packages["plugin"], root)
    assert unchanged == digest
    assert runtime_api.check_admission(root, VERSION, payload_hash=unchanged) == runtime_api.ADMIT_REUSE
    runtime_api.mark_complete(root, VERSION, payload_hash=unchanged)
    assert _evidence(root) == before

    (packages[mutation] / "src" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    changed = _fingerprint(ext, tmp_path, env, packages["plugin"], root, force=True)
    assert changed != digest
    assert runtime_api.check_admission(
        root, VERSION, payload_hash=changed,
    ) == runtime_api.ADMIT_CONTENT_CONFLICT
    with pytest.raises(RuntimeError, match="completion publication refused"):
        runtime_api.mark_complete(root, VERSION, payload_hash=changed)
    assert _evidence(root) == before


@pytest.mark.parametrize("ext", ["sh", "ps1"])
@pytest.mark.parametrize("marker", ["unbound", "legacy-hash", "malformed"])
def test_ordinary_input_hash_cannot_bless_unverified_completed_content(
    tmp_path, inputs, ext, marker,
):
    packages, runtime_api = inputs
    env = _env(tmp_path)
    root = tmp_path / "runtime"
    digest = _fingerprint(ext, tmp_path, env, packages["plugin"], root, force=True)
    slot = runtime_api.version_dir(root, VERSION)
    slot.mkdir(parents=True)
    (slot / "installed-code.py").write_text("VALUE = 1\n", encoding="utf-8")
    # Establish canonical lease scaffolding before corrupting completion evidence.
    runtime_api.mark_complete(root, VERSION, payload_hash=digest)
    completion = runtime_api.marker_path(root, VERSION)
    document = json.loads(completion.read_text(encoding="utf-8"))
    document.pop("payload_hash")
    if marker == "legacy-hash":
        document["payload_hash"] = hashlib.sha256(
            (packages["plugin"] / "pyproject.toml").read_bytes(),
        ).hexdigest()
    completion.write_text(
        "{" if marker == "malformed" else json.dumps(document), encoding="utf-8",
    )
    (root / "current-version").write_text(VERSION, encoding="utf-8")
    (root / "deploy-manifest.json").write_text('{"existing":true}', encoding="utf-8")
    before = _evidence(root)
    assert runtime_api.check_admission(root, VERSION, payload_hash=digest) not in (
        runtime_api.ADMIT_REUSE, runtime_api.ADMIT_CONSTRUCT,
    )
    with pytest.raises(RuntimeError, match="completion publication refused"):
        runtime_api.mark_complete(root, VERSION, payload_hash=digest)
    assert _evidence(root) == before


@pytest.mark.parametrize("ext", ["sh", "ps1"])
def test_ordinary_incomplete_graph_refuses_before_construction(tmp_path, inputs, ext):
    packages, _ = inputs
    env = _env(tmp_path)
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_text("healthy", encoding="utf-8")
    before = _evidence(root)
    (packages["transitive"] / "pyproject.toml").unlink()
    script = _prelude(ext) + _adapter(ext, packages["plugin"], root)
    if ext == "sh":
        script += (
            "_runtime_construction_begin() { echo 'unexpected-construction'; return 0; }\n"
            + _function(ext, "_versioned_slot_clean")
            + "if _versioned_slot_clean; then exit 90; fi\n"
        )
    else:
        script += (
            "function Start-RuntimeConstruction { Write-Output 'unexpected-construction'; return $true }\n"
            + _function(ext, "Invoke-VersionedSlotClean")
            + "try { Invoke-VersionedSlotClean; exit 90 } catch { Write-Output $_ }\n"
        )
    result = _run(ext, script, tmp_path, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unexpected-construction" not in result.stdout + result.stderr
    assert _evidence(root) == before
