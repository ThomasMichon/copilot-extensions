"""Exercise current Bridges argv against the owning source parser declarations."""
from __future__ import annotations

import argparse
import ast
import builtins
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

from worktree_manager.production_picker.picker_tui import command_transport, pivots


def test_current_bridge_manifest_keeps_json_at_owner_global_parser_boundary(monkeypatch):
    root = Path(__file__).resolve().parents[3]
    owner = root / "plugins" / "agent-bridge"
    main = ast.parse((owner / "src" / "agent_bridge" / "__main__.py").read_text(encoding="utf-8"))
    inventory = ast.parse((owner / "src" / "agent_bridge" / "inventory_cli.py").read_text(encoding="utf-8"))
    nodes = [
        node for tree, names in [
            (main, {"_LazyVersionAction", "build_parser"}),
            (inventory, {"register_inventory_commands"}),
        ] for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names
    ]
    namespace = {"argparse": argparse, "__package__": "fixture_bridge", "sys": sys}
    for node in ast.walk(ast.Module(body=nodes, type_ignores=[])):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in namespace and node.id not in vars(builtins):
                namespace[node.id] = lambda *args, **kwargs: None
    interval = next(
        node.value for node in inventory.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "DEFAULT_SUBSCRIBE_INTERVAL"
                for target in node.targets)
    )
    namespace["DEFAULT_SUBSCRIBE_INTERVAL"] = ast.literal_eval(interval)
    for module, function in [
        ("attention_cli", "register_attention_commands"),
        ("acquisition_cli", "register"), ("relationship_cli", "register"),
    ]:
        stub = ModuleType(f"fixture_bridge.{module}")
        setattr(stub, function, lambda sub: None)
        monkeypatch.setitem(sys.modules, stub.__name__, stub)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "owner-parser", "exec"), namespace)
    manifest = json.loads((owner / "pivots" / "agent-bridge.json").read_text())
    argv = pivots.format_template(
        (*manifest["list"], "--stream", "--subscribe"), {"machine": "unused"},
    )
    monkeypatch.setattr(command_transport.shutil, "which", lambda command: "fixture.exe")
    resolved = command_transport.resolve_command(argv)
    args = namespace["build_parser"]().parse_args(resolved[1:])
    assert args.json and args.command == "agents" and args.stream and args.subscribe
    assert resolved[1:4] == ["--json", "agents", "--stream"]
    with pytest.raises(SystemExit):
        namespace["build_parser"]().parse_args(["agents", "--json"])
