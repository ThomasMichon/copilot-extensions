"""Check ordinary resume against Bridge's actual shipped parser declarations."""

import argparse
import ast
from pathlib import Path

import pytest

from agent_dispatch import ordinary_bridge
from tests.test_ordinary_assignment import _start, assigned as assigned

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")


def test_ordinary_assignment_argv_uses_real_bridge_resume_parser(assigned, monkeypatch):
    source = (
        Path(__file__).resolve().parents[2] / "agent-bridge" / "src" / "agent_bridge"
        / "session_lifecycle_cli.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    registration = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "register_session_lifecycle_commands"
    )
    statements = [
        node for node in registration.body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "resume_p" for target in node.targets
        ) or isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "resume_p"
    ]
    assert len(statements) >= 4
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    # Execute only the real resume parser declaration, never Bridge imports or I/O.
    exec(compile(ast.Module(body=statements, type_ignores=[]), str(source), "exec"), {
        "sub": parser.add_subparsers(dest="command"),
        "_cmd_resume": object(),
    })
    with pytest.raises(SystemExit) as invalid:
        parser.parse_args(["--json", "resume", "--worktree-id", "worktree-a", "--strict"])
    assert invalid.value.code == 2
    original = ordinary_bridge.subprocess.run
    parsed = []

    def run(argv, **kwargs):
        if argv[:3] == ["bridge", "--json", "resume"]:
            arguments = parser.parse_args(argv[1:])
            assert arguments.command == "resume"
            assert arguments.session_id == "worktree-a" and arguments.strict is True
            assert getattr(arguments, "cli", False) is False
            parsed.append(arguments)
        return original(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    _start(assigned)
    assert len(parsed) == 1
