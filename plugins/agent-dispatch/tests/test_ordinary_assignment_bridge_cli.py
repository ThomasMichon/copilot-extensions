"""Check ordinary resume against Bridge's actual shipped parser declarations."""

import argparse
import ast
import re
from pathlib import Path

import pytest

from agent_dispatch import ordinary_bridge
from tests.test_ordinary_assignment import _start, assigned as assigned
from tests._hosting_owner import admission_owner as admission_owner

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
    start = next(
        i for i, node in enumerate(registration.body)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "resume_p" for target in node.targets
        )
    )
    end = next(
        i for i, node in enumerate(registration.body[start:], start)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "resume_p"
        and node.value.func.attr == "set_defaults"
    )
    statements = [
        node for node in registration.body[start:end + 1]
        if not isinstance(node, ast.ImportFrom)
    ]
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    namespace = {
        "argparse": argparse, "re": re,
        "sub": parser.add_subparsers(dest="command"),
        "_cmd_resume": object(),
    }
    # Execute shipped parser declarations and their pure helpers, not Bridge I/O.
    for filename, name, alias in (
        ("hosting_admission_cli.py", "add_argument", "add_hosting_argument"),
        ("resume_cli_contract.py", "exact_worktree_id", "exact_worktree_id"),
    ):
        helper = source.with_name(filename)
        definition = next(
            node for node in ast.parse(helper.read_text(encoding="utf-8")).body
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        exec(compile(ast.Module(body=[definition], type_ignores=[]), str(helper), "exec"), namespace)
        namespace[alias] = namespace[name]
    exec(compile(ast.Module(body=statements, type_ignores=[]), str(source), "exec"), namespace)
    exact = parser.parse_args(["--json", "resume", "--worktree-id", "worktree-a", "--strict"])
    assert exact.worktree_id == "worktree-a" and exact.session_id is None
    with pytest.raises(SystemExit) as invalid:
        parser.parse_args(["--json", "resume", "bridge-a", "--worktree-id", "worktree-a"])
    assert invalid.value.code == 2
    original = ordinary_bridge.subprocess.run
    parsed = []

    def run(argv, **kwargs):
        if argv[:3] == ["bridge", "--json", "resume"]:
            arguments = parser.parse_args(argv[1:])
            assert arguments.command == "resume"
            assert arguments.session_id == "worktree-a" and arguments.strict is True
            assert arguments.require_agent_hosting is True
            assert getattr(arguments, "cli", False) is False
            parsed.append(arguments)
        return original(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", run)
    _start(assigned)
    assert len(parsed) == 1
