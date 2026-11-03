"""Actor-free controls for legacy monitor transition reporting."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import update_machine_gate_contract as contract

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"
MONITOR = "status_monitor_cutover.py"


def test_actual_monitor_outcome_contract() -> None:
    assert contract.check_monitor_outcome(SOURCE) == []


def test_install_guard_dispatches_monitor_outcome_contract() -> None:
    assert contract.check_monitor_outcome(SOURCE) == []
    tree = ast.parse((ROOT / "tools" / "check-install-contract.py").read_text(encoding="utf-8"))
    check = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "check")
    dispatches = [
        statement for statement in check.body
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Attribute)
        and isinstance(statement.value.func.value, ast.Name)
        and statement.value.func.value.id == "violations" and statement.value.func.attr == "extend"
        and len(statement.value.args) == 1 and isinstance(statement.value.args[0], ast.Call)
        and isinstance(statement.value.args[0].func, ast.Name)
        and statement.value.args[0].func.id == "check_monitor_outcome"
    ]
    assert len(dispatches) == 1
    expected = ast.parse(
        'PLUGINS_DIR / "agent-worktrees" / "src" / "agent_worktrees"', mode="eval"
    ).body
    assert len(dispatches[0].value.args[0].args) == 1
    assert ast.dump(dispatches[0].value.args[0].args[0]) == ast.dump(expected)


@pytest.mark.parametrize(
    "before,after",
    [
        ('return 0 if summary.get("reason") == "no-live-monitor" else 1', "return 0"),
        ('if action == "cutover" and result.get("ok") is True:', 'if action == "cutover":'),
        ('if restart.get("spawned") is True:', 'if "spawned" in restart:'),
        (
            (
                'print(f"status-monitor transition incomplete ({result.get(\'error\') or summary.get(\'reason\')})")\n'
                "    return 1"
            ),
            'print("status-monitor transition complete")\n    return 0',
        ),
        (
            'monitor_was_live=os.environ.get("AGENT_WORKTREES_MONITOR_WAS_LIVE") == "1",',
            "monitor_was_live=False,",
        ),
        (
            (
                "    summary = activate_after_update(\n"
                '        monitor_was_live=os.environ.get("AGENT_WORKTREES_MONITOR_WAS_LIVE") == "1",\n'
                "    )"
            ),
            (
                "    try:\n"
                "        summary = activate_after_update(\n"
                '            monitor_was_live=os.environ.get("AGENT_WORKTREES_MONITOR_WAS_LIVE") == "1",\n'
                "        )\n"
                "    except RuntimeError:\n"
                "        return 0"
            ),
        ),
    ],
    ids=["busy-noop", "failed-cutover", "failed-restart", "refused", "live-flag", "exception"],
)
def test_monitor_outcome_contract_rejects_source_regressions(
    tmp_path: Path, before: str, after: str,
) -> None:
    assert contract.check_monitor_outcome(SOURCE) == []
    text = (SOURCE / MONITOR).read_text(encoding="utf-8")
    assert text.count(before) == 1
    (tmp_path / MONITOR).write_text(text.replace(before, after, 1), encoding="utf-8")
    assert contract.check_monitor_outcome(tmp_path)


def test_monitor_outcome_contract_accepts_an_annotated_local(tmp_path: Path) -> None:
    assert contract.check_monitor_outcome(SOURCE) == []
    text = (SOURCE / MONITOR).read_text(encoding="utf-8")
    before = 'action = str(summary.get("action") or "noop")'
    assert text.count(before) == 1
    (tmp_path / MONITOR).write_text(
        text.replace(before, 'action: str = str(summary.get("action") or "noop")', 1),
        encoding="utf-8",
    )
    assert contract.check_monitor_outcome(tmp_path) == []


def test_monitor_outcome_contract_accepts_equivalent_environment_lookup(tmp_path: Path) -> None:
    assert contract.check_monitor_outcome(SOURCE) == []
    text = (SOURCE / MONITOR).read_text(encoding="utf-8")
    before = 'os.environ.get("AGENT_WORKTREES_MONITOR_WAS_LIVE")'
    assert text.count(before) == 1
    (tmp_path / MONITOR).write_text(
        text.replace(before, 'os.getenv("AGENT_WORKTREES_MONITOR_WAS_LIVE")', 1),
        encoding="utf-8",
    )
    assert contract.check_monitor_outcome(tmp_path) == []
