#!/usr/bin/env python3
"""Render the native read exemption's exact normalized guard-source contract."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "agent-worktrees" / "scripts"
GUARDS = ("statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "pr_supersede_guard.py")
WRITE_DEPENDENCIES = ("tool_call_forms.py", "guarded_anchor_map.py")


def declaration(source: str, name: str) -> ast.expr:
    """Select exactly one original declaration without executing its source."""
    declarations = [
        node.value for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]
    if len(declarations) != 1:
        raise ValueError(f"Expected one {name} declaration")
    return declarations[0]


def literal_strings(source: str, name: str) -> tuple[str, ...]:
    """Read one bounded literal tuple without executing its source."""
    values = ast.literal_eval(declaration(source, name))
    if (
        not isinstance(values, tuple) or not values or len(values) > 128
        or any(not isinstance(value, str) or not value for value in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError(f"Expected unique literal strings for {name}")
    return values


def regex_text(node: ast.expr, heads: tuple[str, ...]) -> str:
    """Read only literal concatenation, literal joins and the original head join."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return regex_text(node.left, heads) + regex_text(node.right, heads)
    if (
        isinstance(node, ast.Call) and not node.keywords and len(node.args) == 1
        and isinstance(node.func, ast.Attribute) and node.func.attr == "join"
        and isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str)
    ):
        values = node.args[0]
        if isinstance(values, (ast.List, ast.Tuple)):
            return node.func.value.value.join(regex_text(value, heads) for value in values.elts)
        expected = ast.parse("(re.escape(head) for head in SHELL_READ_HEADS)", mode="eval").body
        if ast.dump(values) == ast.dump(expected):
            return node.func.value.value.join(re.escape(head) for head in heads)
    raise ValueError("Unsupported original regex expression")


def boolean_pattern(source: str, name: str, heads: tuple[str, ...]) -> str:
    """Translate the explicitly supported ASCII boolean regex subset to .NET."""
    node = declaration(source, name)
    if (
        not isinstance(node, ast.Call) or node.keywords or len(node.args) != 2
        or ast.dump(node.func) != ast.dump(ast.parse("re.compile", mode="eval").body)
        or ast.dump(node.args[1]) != ast.dump(ast.parse("re.IGNORECASE", mode="eval").body)
    ):
        raise ValueError(f"Unsupported original regex declaration: {name}")
    pattern = regex_text(node.args[0], heads)
    compiled = re.compile(pattern, re.IGNORECASE)
    named = re.findall(r"\(\?P<([A-Za-z_][A-Za-z_0-9]*)>", pattern)
    if len(named) != len(compiled.groupindex) or set(named) != set(compiled.groupindex):
        raise ValueError(f"Unsupported named regex syntax in {name}")
    pattern = re.sub(r"\(\?P<[A-Za-z_][A-Za-z_0-9]*>", "(?:", pattern)
    index, in_class, repeated = 0, False, False
    while index < len(pattern):
        char = pattern[index]
        if not 32 <= ord(char) <= 126:
            raise ValueError(f"Unsupported regex character in {name}")
        if char == "\\":
            index += 1
            if index == len(pattern) or pattern[index] not in r"bdsSwWtnr.\?*=-:/[]":
                raise ValueError(f"Unsupported regex escape in {name}")
            repeated = False
        elif char == "[":
            if in_class:
                raise ValueError(f"Unsupported nested regex class in {name}")
            in_class = True
        elif char == "]":
            in_class = False
            repeated = False
        elif not in_class:
            if pattern.startswith("(?:", index):
                index += 2
                repeated = False
            elif pattern.startswith("(?", index) or char in "{}^$.":
                raise ValueError(f"Unsupported regex syntax in {name}")
            elif char in "*+?":
                if repeated:
                    raise ValueError(f"Unsupported repeated quantifier in {name}")
                repeated = True
            else:
                repeated = False
        index += 1
    return pattern


def render(scripts: Path = SCRIPTS) -> str:
    sources = {name: (scripts / name).read_text(encoding="utf-8-sig") for name in GUARDS}
    guards = {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in sources.items()}
    key_declarations = [literal_strings(text, "CMD_ARG_KEYS") for text in sources.values()]
    if any(keys != key_declarations[0] for keys in key_declarations[1:]):
        raise ValueError("Original guards disagree on command-key selection")
    dependencies = {
        name: hashlib.sha256((scripts / name).read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest()
        for name in WRITE_DEPENDENCIES
    }
    relevance = (scripts / "hook_pre_relevance.py").read_text(encoding="utf-8-sig")
    commands = literal_strings(relevance, "SHELL_READ_HEADS")
    rendered = json.dumps({
        "schema": "agent-worktrees.native-read-policy", "version": 1, "guards": guards,
        "nativeWriteDependencies": dependencies,
        "nativeShellReadCommands": list(commands),
        "nativeShellReadCommandKeys": list(key_declarations[0]),
        "nativeShellReadArguments": {
            "version": 1,
            "read": boolean_pattern(relevance, "_SAFE_READ_SHELL", commands),
            "reject": [
                *(boolean_pattern(sources[name], "_WRITE_VERBS", commands) for name in GUARDS[:3]),
                *(boolean_pattern(sources[GUARDS[3]], name, commands)
                  for name in ("_GH_PR_CLOSE", "_GH_API_PULL")),
            ],
        },
        "nativeShellReadDependencies": {
            "hook_pre_relevance.py": hashlib.sha256(relevance.encode("utf-8")).hexdigest(),
        },
    }, indent=2) + "\n"
    if len(rendered.encode("utf-8")) > 4096:
        raise ValueError("Native read policy exceeds its consumer's 4096-byte bound")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = SCRIPTS / "hook-read-policy.json"
    expected = render()
    if args.check:
        if not target.is_file() or target.read_text(encoding="utf-8") != expected:
            parser.exit(1, "Native read policy differs; run python tools/render-hook-read-policy.py\n")
    else:
        target.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
