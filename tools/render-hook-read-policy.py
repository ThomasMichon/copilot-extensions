#!/usr/bin/env python3
"""Render the native read exemption's exact normalized guard-source contract."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "agent-worktrees" / "scripts"
GUARDS = ("statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "pr_supersede_guard.py")
WRITE_DEPENDENCIES = ("tool_call_forms.py", "guarded_anchor_map.py")


def render(scripts: Path = SCRIPTS) -> str:
    guards = {name: hashlib.sha256((scripts / name).read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest() for name in GUARDS}
    dependencies = {
        name: hashlib.sha256((scripts / name).read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest()
        for name in WRITE_DEPENDENCIES
    }
    relevance = (scripts / "hook_pre_relevance.py").read_text(encoding="utf-8-sig")
    declarations = [
        node.value for node in ast.parse(relevance).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SHELL_READ_HEADS" for target in node.targets)
    ]
    if len(declarations) != 1:
        raise ValueError("Expected one canonical shell-read head declaration")
    commands = ast.literal_eval(declarations[0])
    if (
        not isinstance(commands, tuple) or not commands or len(commands) > 128
        or any(not isinstance(command, str) or not command for command in commands)
        or len(set(commands)) != len(commands)
    ):
        raise ValueError("Expected unique literal shell-read heads")
    rendered = json.dumps({
        "schema": "agent-worktrees.native-read-policy", "version": 1, "guards": guards,
        "nativeWriteDependencies": dependencies,
        "nativeShellReadCommands": list(commands),
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
