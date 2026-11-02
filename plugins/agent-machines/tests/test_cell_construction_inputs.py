"""Real construction and retained builds must not mutate selected snapshots."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "plugins/agent-machines/scripts"
HOSTS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell")))))


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    tests = ROOT / "libs/installation-context/tests"
    monkeypatch.syspath_prepend(str(tests))
    fixtures = _load("cell_construction_inputs_fixtures", tests / "test_snapshot_provenance.py")
    layout = fixtures._receipt_layout(tmp_path, plugin_id="agent-machines")
    selected = Path(layout["snapshot_root"])
    scripts = selected / "scripts"
    scripts.mkdir()
    for name in (
        "versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_construction.py",
        "versioned_runtime_source.py", "versioned_runtime_publication.py", "cell_installer.py",
    ):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    shutil.copytree(
        SCRIPTS / "installation-context", scripts / "installation-context",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (selected / "plugin.json").write_text('{"name":"agent-machines","version":"1.0.0"}')
    (selected / "pyproject.toml").write_text('[project]\nname = "agent-machines"\nversion = "1.0.0"\n')
    seed = selected / "seed/__pycache__/keep.pyc"
    seed.parent.mkdir(parents=True)
    seed.write_bytes(b"pre-existing copied cache must stay bound")
    monkeypatch.syspath_prepend(str(SCRIPTS))
    identity = _load("cell_construction_inputs_identity", SCRIPTS / "cell_installer.py")
    return fixtures, layout, selected, identity


@pytest.mark.parametrize("host", ["bash", *HOSTS])
def test_snapshot_backed_construction_preserves_all_selected_bytes(
    snapshot: tuple, tmp_path: Path, host: str,
) -> None:
    fixtures, layout, selected, identity = snapshot
    fixtures._stamp_with_python(layout)
    fixtures._provision_slot_with_python(layout, runtime_version="1.0.0")
    expected = identity.payload_identity(selected, "1.0.0")
    helper = selected / "scripts/versioned_runtime.py"
    root = Path(layout["plugin_root"])
    style = "sh" if host == "bash" else "ps1"
    rendered = subprocess.run(
        [sys.executable, "-I", "-B", str(helper), "--root", str(root),
         "construction-shell", "--shell", style],
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert rendered.returncode == 0, rendered.stdout + rendered.stderr
    payload_hash = "a" * 64
    if style == "sh":
        if not shutil.which("bash"):
            pytest.skip("Bash is unavailable")
        harness = tmp_path / "construct.sh"
        harness.write_text(
            rendered.stdout + "\n"
            + "_runtime_construction_begin "
            + " ".join(shlex.quote(value) for value in (
                sys.executable, str(helper), str(root), "1.0.0", payload_hash, "current",
            ))
            + " || exit 1\n"
            + f"_runtime_construction_complete {payload_hash} || exit 1\n"
            + "_runtime_construction_finish || exit 1\n"
            + "printf 'construction-ready\\n'\n",
            encoding="utf-8",
        )
        command = [host, str(harness)]
    else:
        literal = lambda value: "'" + str(value).replace("'", "''") + "'"
        harness = tmp_path / "construct.ps1"
        harness.write_text(
            "$ErrorActionPreference = 'Stop'\n" + rendered.stdout + "\n"
            + "try {\n"
            + f"[void](Start-RuntimeConstruction -Python {literal(sys.executable)} "
            + f"-Helper {literal(helper)} -Root {literal(root)} -Version '1.0.0' "
            + f"-PayloadHash '{payload_hash}' -LinkName 'current')\n"
            + f"Complete-RuntimeConstruction -PayloadHash '{payload_hash}'\n"
            + "} finally { Stop-RuntimeConstruction }\n"
            + "Write-Output 'construction-ready'\n",
            encoding="utf-8",
        )
        command = [host, "-NoProfile", "-NonInteractive", "-File", str(harness)]
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=20,
        env={**os.environ, "COPILOT_EXTENSIONS_CONTEXT": str(layout["install"])},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "construction-ready" in result.stdout
    assert identity.payload_identity(selected, "1.0.0") == expected
    assert (selected / "seed/__pycache__/keep.pyc").read_bytes() == (
        b"pre-existing copied cache must stay bound"
    )


def test_snapshot_backed_package_build_retains_outputs_outside_selection(
    snapshot: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixtures, _layout, selected, identity = snapshot
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    from test_installer_dependency_policy import offline_environment, source_project

    source_project(selected, name="agent-machines")
    manifest = selected / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('version="1.0"', 'version = "1.0.0"'))
    for path in SCRIPTS.glob("installer_*.py"):
        shutil.copyfile(path, selected / "scripts" / path.name)
    witness = tmp_path / "build-witness.json"
    backend = selected / "fixture_backend.py"
    backend.write_text(backend.read_text().replace(
        "    name = ",
        "    import json, os\n"
        "    root = pathlib.Path.cwd()\n"
        "    outputs = ['build/generated.py', 'agent_machines.egg-info/PKG-INFO']\n"
        "    for relative in outputs:\n"
        "        path = root / relative\n"
        "        path.parent.mkdir(parents=True, exist_ok=True)\n"
        "        path.write_text('actual retained build output')\n"
        "    pathlib.Path(os.environ['CELL_BUILD_WITNESS']).write_text(json.dumps({\n"
        "        'root': str(root), 'outputs': {name: (root / name).read_text() for name in outputs}}))\n"
        "    name = ",
    ))
    expected = identity.payload_identity(selected, "1.0.0")
    uv = shutil.which("uv")
    assert uv, "native UV is required for retained snapshot build coverage"
    target = tmp_path / "installed"
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(selected / "scripts/installer_dependency_policy.py"),
         "--uv", uv, "--trusted-source-root", str(selected), "--",
         "--no-index", "--no-config", "--python", sys.executable,
         "--target", str(target), str(selected)],
        cwd=tmp_path, env={**offline_environment(tmp_path), "CELL_BUILD_WITNESS": str(witness)},
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(witness.read_text())
    retained = Path(report["root"])
    assert retained != selected and not retained.is_relative_to(selected)
    assert not retained.exists(), "canonical retained-input cleanup did not settle"
    assert set(report["outputs"]) == {"build/generated.py", "agent_machines.egg-info/PKG-INFO"}
    assert set(report["outputs"].values()) == {"actual retained build output"}
    assert (target / "agent_machines-1.0.dist-info/METADATA").is_file()
    assert identity.payload_identity(selected, "1.0.0") == expected
