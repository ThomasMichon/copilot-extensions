"""Private roots and immutable fixture state for Logger uninstall controls."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType

DERIVED = (
    "versions", ".venv", "snapshots", "bin", "current-version", "last-known-good",
    "payload-dir", "stamped-version", "deploy-manifest.json", "running-version.json",
)


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(tmp_path: Path, ownership: str) -> tuple[Path, dict[str, bytes], dict[str, str]]:
    home = tmp_path / "home"
    root = home / "scoped-runtime"
    root.mkdir(parents=True)
    for name in DERIVED:
        path = root / name
        if name in ("versions", ".venv", "snapshots", "bin"):
            path.mkdir()
            (path / "derived").write_text("rebuildable")
        else:
            path.write_text("selected-runtime")
    manifest = root / "deploy-manifest.json"
    if ownership in ("missing", "stamped", "foreign-stamp", "outside-stamp"):
        manifest.unlink()
    elif ownership == "malformed":
        manifest.write_text("{")
    else:
        manifest.write_text(json.dumps({
            "schema_version": 3,
            "service": "agent-logger" if ownership == "owned" else "another-product",
        }))
    if ownership in ("foreign-binding", "foreign-lifecycle", "cell", "managed"):
        record: dict[str, object] = {"schema_version": 3, "service": "agent-logger"}
        if ownership == "foreign-binding":
            record["commandBinding"] = {"sourceFingerprint": "another-source"}
        elif ownership == "foreign-lifecycle":
            record["commandLifecycle"] = {"owner": "another-source"}
        else:
            (root / ("install.json" if ownership == "cell" else "runtime-deployment.json")).write_text("{}")
        manifest.write_text(json.dumps(record))
    if ownership in ("stamped", "foreign-stamp", "outside-stamp"):
        snapshot = (
            tmp_path / "outside-snapshot" if ownership == "outside-stamp"
            else root / "snapshots" / "1.0.0"
        )
        snapshot.mkdir()
        name = "another-product" if ownership == "foreign-stamp" else "agent-logger"
        (snapshot / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "1.0.0"\n',
        )
        (root / "payload-dir").write_text(str(snapshot))
    durable = {
        "config.yaml": b"enabled: true\n",
        "sessions.db": b"durable database fixture",
        "unknown.txt": b"operator content",
        "session-state/events.jsonl": b'{"preserved":true}\n',
        "snapshots/.construction-leases/held.lock": b"persistent snapshot coordination",
    }
    for name, content in durable.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    env = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
    }
    for name in (
        "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
        "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN", "COPILOT_PLUGIN_INSTALL_STAGED",
        "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "AGENT_LOGGER_HOME",
    ):
        env.pop(name, None)
    return root, durable, env


def assert_state(
    root: Path, durable: dict[str, bytes], *, removed: bool, ownership: str = "owned",
) -> None:
    for name, content in durable.items():
        assert (root / name).read_bytes() == content
    for name in DERIVED:
        if name == "snapshots" and removed:
            assert sorted(child.name for child in (root / name).iterdir()) == [".construction-leases"]
            continue
        if name == "deploy-manifest.json" and ownership in (
            "missing", "stamped", "foreign-stamp", "outside-stamp",
        ):
            assert not (root / name).exists()
            continue
        assert (root / name).exists() is not removed, name
