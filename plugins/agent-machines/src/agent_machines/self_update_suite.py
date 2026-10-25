"""Serialize only the suite phase with Manager update and enrollment."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from zdd.cutover_lock import CutoverLockedError

from .automatic_update_ownership import read_update_ownership, suite_update_transaction
from .self_update_types import CommandResult, StepResult


@dataclass
class SuiteUpdateResult:
    status: str = "ok"
    detail: str = ""
    steps: list[StepResult] = field(default_factory=list)
    blocks_restore: bool = False


def run_suite_updates(
    repo_names: list[str],
    *,
    runner: Callable[..., CommandResult],
    home: Path | None = None,
) -> SuiteUpdateResult:
    try:
        # Enrollment uses this same transaction; never decide ownership
        # before acquiring it, even when the policy file is currently absent.
        with suite_update_transaction(home):
            ownership = read_update_ownership(home)
            if ownership.state == "refused" or ownership.hands_off_suite:
                refused = ownership.state == "refused"
                return SuiteUpdateResult(
                    status="error" if refused else "ok",
                    detail=ownership.detail,
                    steps=[
                        StepResult(
                            "suite-update-ownership",
                            "error" if refused else "skipped",
                            ownership.detail
                            + "; repository and machine maintenance remain enabled",
                            path=str(ownership.path),
                        )
                    ],
                )
            steps: list[StepResult] = []
            for repo_name in sorted(set(repo_names)):
                command = runner(
                    ["agent-worktrees", "-p", repo_name, "update", "--no-manager"],
                    timeout=3600,
                )
                steps.append(
                    StepResult(
                        f"update:{repo_name}",
                        "changed" if command.returncode == 0 else "error",
                        command.output or "updated plugin payloads and runtimes",
                        command=command.argv,
                    )
                )
                if command.returncode != 0:
                    return SuiteUpdateResult(
                        status="error",
                        detail=command.output or "agent-worktrees update failed",
                        steps=steps,
                        blocks_restore=True,
                    )
            return SuiteUpdateResult(steps=steps)
    except CutoverLockedError as exc:
        return SuiteUpdateResult(
            status="deferred",
            detail=f"suite update transaction is held; maintenance continues: {exc}",
            steps=[StepResult("suite-update-ownership", "deferred", str(exc))],
        )
    except OSError as exc:
        return SuiteUpdateResult(
            status="error",
            detail=f"suite update refused: {exc}",
            steps=[StepResult("suite-update", "error", str(exc))],
        )
