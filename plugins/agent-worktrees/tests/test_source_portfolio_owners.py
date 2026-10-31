"""Materialized required-context owners carry the source-update lifecycle SDK."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from types import ModuleType

import pytest

from agent_worktrees.update_runtime_source import PreparedRuntimeSource

ROOT = Path(__file__).resolve().parents[3]


def required_context_plugins() -> list[str]:
    plugins = []
    for manifest in sorted((ROOT / "plugins").glob("*/payload-invocation.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if (
            data["version"] == 2 and data.get("installationContext") == "required"
            and (manifest.parent / "pyproject.toml").is_file()
        ):
            plugins.append(manifest.parent.name)
    assert plugins, "the required-context runtime portfolio must not be empty"
    return plugins


@pytest.fixture
def preparation(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location(
        "_portfolio_source_preparation", ROOT / "tools/prepare_runtime_source.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def source_snapshot(tmp_path_factory: pytest.TempPathFactory) -> Path:
    snapshot = tmp_path_factory.mktemp("portfolio-source")
    spec = importlib.util.spec_from_file_location(
        "_portfolio_materializer", ROOT / "tools/materialize_main.py",
    )
    assert spec and spec.loader
    materializer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(materializer)
    roots = [
        ROOT / "libs", ROOT / "worktree-manager/bin", ROOT / "docs/patterns",
        *(ROOT / "plugins" / name for name in required_context_plugins()),
    ]
    # The canonical ignore policy must not leave bytecode-only library directories.
    for root in roots:
        for directory, directories, files in os.walk(root):
            ignored = materializer._ignore(directory, directories + files)
            directories[:] = [name for name in directories if name not in ignored]
            for name in files:
                if name not in ignored:
                    source = Path(directory) / name
                    destination = snapshot / source.relative_to(ROOT)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
    return snapshot


@pytest.mark.parametrize("plugin", required_context_plugins())
def test_materialized_owner_accepts_source_without_changing_runtime(
    tmp_path: Path, preparation: ModuleType, source_snapshot: Path, plugin: str,
) -> None:
    home = tmp_path / "home"
    owner = home / ".copilot/installed-plugins/example" / plugin
    owner.parent.mkdir(parents=True)
    preparation._materialize(source_snapshot, plugin, owner)
    artifact = tmp_path / "prepared"
    shutil.copytree(owner, artifact)
    version = preparation.payload_package_version(artifact, plugin)
    digest = preparation._assembly_hash(artifact)
    identity = preparation.SourceIdentity(
        plugin, version, "a" * 40, "b" * 40, digest,
        preparation.runtime_version(version, "a" * 40, digest),
    )
    (artifact / "runtime-source.json").write_text(
        json.dumps(identity.document()), encoding="utf-8",
    )
    source = PreparedRuntimeSource(artifact, json.dumps({
        "runtime_source": identity.document(), "runtime_version": identity.runtime_version,
    }))
    manifest = json.loads((owner / "payload-invocation.json").read_text(encoding="utf-8"))
    runtime = home / manifest["legacyRuntimeRoot"]
    runtime.mkdir()
    (runtime / "current-version").write_text("existing-release\n", encoding="utf-8")
    (runtime / "config.json").write_text('{"preserved":true}\n', encoding="utf-8")
    database = runtime / "retained.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE retained (value TEXT)")
        connection.execute("INSERT INTO retained VALUES ('existing')")
    before = {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in runtime.iterdir()
    }
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_", "GIT_"))
        and key not in {"HOME", "USERPROFILE", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment.update(HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
    # Fixture identities test SDK readiness, not Git admission or production formats.
    source.validate(plugin, owner, environment)
    for name in ("command_binding.py", "command_lifecycle.py"):
        assert (owner / "scripts" / name).read_bytes() == (
            ROOT / "libs/payload-invocation" / name
        ).read_bytes()
    for name, canonical in (
        ("__init__.py", "human.py"), ("redact.py", "redact.py"),
        ("capabilities.py", "capabilities.py"),
    ):
        assert (owner / "scripts/_lifecycle_human" / name).read_bytes() == (
            ROOT / "libs/update-telemetry/src/update_telemetry" / canonical
        ).read_bytes()
    assert {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in runtime.iterdir()
    } == before
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM retained").fetchall() == [("existing",)]
