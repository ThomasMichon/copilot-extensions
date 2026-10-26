"""Pure merge/parser proofs without importing or starting the managed runtime."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "plugins" / "agent-worktrees" / "src" / "agent_worktrees" / "config.py"
PLATFORMS = ("windows", "linux", "wsl")


def validation_functions(path: Path):
    module = ast.parse(path.read_text(encoding="utf-8"))
    functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    builder = functions["_build_repo_config"]
    start = next(
        index for index, node in enumerate(builder.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "raw_vpaths" for target in node.targets)
    )
    end = next(
        index for index, node in enumerate(builder.body)
        if isinstance(node, ast.For) and "validate_hook" in ast.unparse(node.iter)
    )
    parser = ast.parse("def parse_validation(data):\n    pass\n").body[0]
    parser.body = [
        *builder.body[start:end + 1],
        ast.Return(value=ast.Tuple(
            elts=[ast.Name(id="validate_hook", ctx=ast.Load()),
                  ast.Name(id="validate_paths", ctx=ast.Load())],
            ctx=ast.Load(),
        )),
    ]
    selected = ast.Module(
        body=[ast.parse("from __future__ import annotations").body[0],
              functions["_deep_merge"], parser],
        type_ignores=[],
    )
    namespace = {}
    exec(compile(ast.fix_missing_locations(selected), str(path), "exec"), namespace)
    return namespace["_deep_merge"], namespace["parse_validation"]


@pytest.mark.parametrize("override,cleared", [
    ({}, False),
    ({"validate_hook": {}}, False),
    ({"validate_hook": {platform: [] for platform in PLATFORMS}}, True),
])
def test_platform_lists_clear_but_omission_and_empty_map_inherit(override, cleared):
    merge, parse = validation_functions(CONFIG)
    hooks = {platform: ["validator", platform] for platform in PLATFORMS}
    merged = merge({"validate_hook": hooks, "validate_paths": ["src"]}, override)
    actual, paths = parse(merged)
    assert actual == ({platform: [] for platform in PLATFORMS} if cleared else hooks)
    assert paths == ["src"]


def test_empty_validate_paths_replaces_inherited_list():
    merge, parse = validation_functions(CONFIG)
    _, paths = parse(merge({"validate_paths": ["src", "tests"]}, {"validate_paths": []}))
    assert paths == []


def test_higher_precedence_machine_override_still_wins():
    merge, parse = validation_functions(CONFIG)
    disabled = {"validate_hook": {platform: [] for platform in PLATFORMS}, "validate_paths": []}
    hooks, paths = parse(merge(disabled, {
        "validate_hook": {"windows": ["machine-validator"]},
        "validate_paths": ["machine-path"],
    }))
    assert hooks["windows"] == ["machine-validator"]
    assert paths == ["machine-path"]
