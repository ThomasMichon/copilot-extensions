"""Actor-free mutation controls for targeted update receipt judgment."""
from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest
import update_machine_gate_contract as contract

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins" / "agent-worktrees" / "src" / "agent_worktrees"


def _receipt_source(tmp_path: Path) -> Path:
    for name in ("installer_run.py", "reconcile.py", "update_installer_process.py", "update_runtime.py"):
        shutil.copyfile(SOURCE / name, tmp_path / name)
    return tmp_path


def test_actual_update_receipt_contract() -> None:
    assert contract.check_receipt_judgment(SOURCE) == []


def test_install_guard_dispatches_receipt_contract() -> None:
    assert contract.check_receipt_judgment(SOURCE) == []
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
        and statement.value.args[0].func.id == "check_receipt_judgment"
    ]
    assert len(dispatches) == 1


@pytest.mark.parametrize(
    "name,before,after",
    [
        ("installer_run.py", "if target is None or complete:", "if True:"),
        ("installer_run.py", "if expired or rc == _WATCHDOG_EXIT:", "if False:"),
        ("installer_run.py", "if rc != 0:", "if False:"),
        ("installer_run.py", "_WATCHDOG_EXIT = 124", "_WATCHDOG_EXIT = 125"),
        ("reconcile.py", "if a == b:", "if True:"),
        ("update_installer_process.py",
         "run = installer_run.run_installer if receipt_owned else subprocess.run", "run = subprocess.run"),
        ("update_runtime.py", "label=step.name, receipt_owned=True,", "label=step.name, receipt_owned=False,"),
        ("update_runtime.py", "target_version=step.target_version,", "target_version=None,"),
        ("update_runtime.py", "status = run(command).status", 'run(command)\n            status = "OK"'),
    ],
    ids=["false-success", "ignored-timeout", "ignored-exit", "wrong-watchdog", "bad-version",
         "unowned-adapter", "unowned-step", "missing-target", "ignored-status"],
)
def test_receipt_contract_rejects_source_regressions(
    tmp_path: Path, name: str, before: str, after: str,
) -> None:
    source = _receipt_source(tmp_path)
    path = source / name
    text = path.read_text(encoding="utf-8")
    assert before in text
    path.write_text(text.replace(before, after, 1), encoding="utf-8")
    assert contract.check_receipt_judgment(source)


def test_receipt_contract_rejects_runner_bypass(tmp_path: Path) -> None:
    source = _receipt_source(tmp_path)
    path = source / "installer_run.py"
    text = path.read_text(encoding="utf-8")
    before = "status, complete = _judge(rc, expired, target_version, deployed)"
    assert text.count(before) == 2
    head, tail = text.rsplit(before, 1)
    path.write_text(head + 'status, complete = "OK", True' + tail, encoding="utf-8")
    assert contract.check_receipt_judgment(source)


def test_receipt_contract_accepts_an_annotated_watchdog_constant(tmp_path: Path) -> None:
    source = _receipt_source(tmp_path)
    path = source / "installer_run.py"
    text = path.read_text(encoding="utf-8")
    assert "_WATCHDOG_EXIT = 124" in text
    path.write_text(text.replace("_WATCHDOG_EXIT = 124", "_WATCHDOG_EXIT: int = 124", 1), encoding="utf-8")
    assert contract.check_receipt_judgment(source) == []
