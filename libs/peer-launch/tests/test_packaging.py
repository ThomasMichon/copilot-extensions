"""Cheap canonical/vendor and bootstrap independence contracts."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_all_packaged_launchers_and_validators_match():
    canonical = ROOT / "libs" / "peer-launch" / "peer_launch.py"
    primitive = ROOT / "libs" / "installation-context" / "installation_context.py"
    for plugin, filename in (
        ("agent-dispatch", "peer_launch.py"), ("agent-codespaces", "_peer_launch.py"),
    ):
        package = ROOT / "plugins" / plugin / "src" / plugin.replace("-", "_")
        assert (package / filename).read_bytes() == canonical.read_bytes()
        assert (package / "_installation_context.py").read_bytes() == primitive.read_bytes()


def test_launcher_has_no_runtime_dependency():
    source = ROOT / "libs" / "peer-launch" / "peer_launch.py"
    imports = {
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imports <= {"__future__", "pathlib", "types", "typing"}


def test_sync_tool_registers_both_packaged_primitives():
    spec = importlib.util.spec_from_file_location(
        "sync_installation_context", ROOT / "tools" / "sync-installation-context.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    destinations = {destination for _, destination in module.vendor_pairs()}
    for plugin in ("agent-dispatch", "agent-codespaces"):
        assert (
            ROOT / "plugins" / plugin / "src" / plugin.replace("-", "_")
            / "_installation_context.py"
        ) in destinations
