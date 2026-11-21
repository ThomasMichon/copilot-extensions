"""Runtime adapter for installation-scoped reversible action controls."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .queue_common import Task

from .action_policy import ActionDecision, evaluate_action
from .install_paths import install_dir, uses_legacy_install_dir

AUTOMATIC_ACTIONS = frozenset({
    "coordinator-replacement", "supervisor-self-update", "automatic-runtime-reconcile",
})


def action_decision(
    action: str, *, root: Path | None = None, automatic: bool = False,
) -> ActionDecision:
    selected = install_dir() if root is None else root
    shared = None
    fallback = None
    if (automatic and action in AUTOMATIC_ACTIONS and uses_legacy_install_dir(selected)
            and not os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()):
        override = os.environ.get("BOOTSTRAP_KILLSWITCH_STATE_FILE")
        if override:
            shared = Path(override).expanduser()
        else:
            base = Path.home() / ".copilot-extensions"
            shared = base / "killSwitches" / "bootstrap-killswitch.json"
            fallback = base / "bootstrap-killswitch.json"
    return evaluate_action(
        action, selected, shared_bootstrap=shared,
        shared_bootstrap_fallback=fallback,
    )


def action_inhibited(
    action: str, *, logger: logging.Logger, root: Path | None = None,
    automatic: bool = False,
) -> bool:
    decision = action_decision(action, root=root, automatic=automatic)
    if decision.inhibited or decision.error:
        logger.log(
            logging.WARNING if decision.error else logging.INFO,
            "action %s %s: scope=%s source=%s reason=%s%s",
            action, "inhibited" if decision.inhibited else "allowed",
            decision.scope, decision.source, decision.reason or "(none)",
            f"; {decision.error}" if decision.error else "",
        )
    return decision.inhibited


class InstallationWorkerAdmission:
    """Production queue policy; embeddings can still inject a global-only callable."""

    def __call__(self) -> None:
        from .queue_records import WorkerAdmissionInhibited

        if action_inhibited("worker-admission", logger=logging.getLogger(__name__)):
            raise WorkerAdmissionInhibited()

    def require_spawn(self, task: Task, owner: str | None,
                      instance: tuple[str, str, str] | None, driver: str | None,
                      reservation: Mapping[str, object] | None = None) -> None:
        from .admission_holds import require_spawn_admission
        require_spawn_admission(task, owner, instance, driver, reservation)


require_worker_admission = InstallationWorkerAdmission()
