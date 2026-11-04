"""Own-worker finalization; never task release, process retirement or foreign cleanup."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path

from . import config as cfg
from . import finalized_checkout, finalize, native_lifecycle_source, native_source_context, output, sessions, tracking
from .execution_spaces import require_record_mutation
from .record_read import read_workspace_record
from .record_config import metadata_project
from .dispatch_worker_sessions import require_no_foreign_hold

CONTRACT = "agent-worktrees.dispatch-worker-closeout"


def describe_contract() -> dict:
    """Describe this executing provider, independently of its package version."""
    return {
        "contract": CONTRACT, "version": 1,
        "operations": ["finalize-own"],
        "native_proof": "inuse-hold-v1",
        "platforms": ["linux", "wsl"],
        "paired_closeout": False,
        "task_release": False,
        "process_retirement": False,
    }


def _text(value: object, name: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > 512 or any(ord(char) < 32 for char in value)
    ):
        raise ValueError(f"Own-worker closeout requires a bounded {name}")
    return value


def _native_source(native_id: str) -> str:
    source = native_lifecycle_source.start_identity(native_id, native_source_context.capture())
    if source is None:
        raise ValueError("Own-worker closeout requires its actual native caller process")
    return source


@dataclass(frozen=True)
class OwnWorker:
    """The provider's immutable local binding, never caller-asserted finalization."""

    task_id: str
    reservation_key: str
    native_id: str
    worktree_id: str
    creation_nonce: str
    path: Path
    source: str
    head_revision: int
    allocation: tracking.DispatchAttempt

    def require(self, record: tracking.WorktreeRecord | None, config: cfg.Config) -> None:
        """Recheck identity and preservation gates at each finalization boundary."""
        self._require_custody(record, config)
        self.require_caller()

    def require_caller(self) -> None:
        """Require the actual invoking native process, not an observer's label."""
        if (
            not Path.cwd().resolve().is_relative_to(self.path)
            or os.environ.get("COPILOT_AGENT_SESSION_ID") != self.native_id
            or _native_source(self.native_id) != self.source
        ):
            raise ValueError("Own-worker closeout requires its actual native caller and workspace")

    def _require_custody(
        self, record: tracking.WorktreeRecord | None, config: cfg.Config,
        *, readiness_claim: str | None = None,
    ) -> None:
        """Share preservation reads; normal finalization never excepts a task."""
        if record is None:
            raise ValueError("Own-worker closeout requires an existing managed record")
        require_record_mutation(record, config)
        allocation = record.dispatch_attempt
        entry = record.session_entry(self.native_id)
        if (
            record.repo != config.repo_name or record.worktree_id != self.worktree_id
            or Path(record.worktree_path).resolve(strict=True) != self.path
            or record.creation_nonce != self.creation_nonce or not self.creation_nonce
            or not record.checkout_managed
            or record.status not in {"active", "finalizing", "finalized"}
            or allocation is None or record.dispatch_attempt_opaque
            or allocation.task_id != self.task_id
            or allocation.reservation_key != self.reservation_key
            or allocation != self.allocation
            or allocation.creator_machine.casefold() != record.execution_machine.casefold()
            or record.resolved_head_session != self.native_id
            or record.head_session not in {None, self.native_id}
            or record.head_revision != self.head_revision
            or entry is None or entry.state != "active"
        ):
            raise ValueError("Own-worker closeout workspace, allocation or native head changed")
        activations = [
            activation for activation in entry.activations
            if activation.source_identity == self.source and not activation.source_ambiguous
            and activation.ended_at is None
        ]
        if len(activations) != 1:
            raise ValueError("Own-worker closeout requires its canonical active native source")
        if (
            record.pending_handoffs or tracking.effective_open_follow_up_count(record)
            or record.active_effort is not None
            or record.is_paired or record.active_controllers or record.controller_metadata_opaque
            or record.execution_leg_opaque or record.session_backend_opaque
        ):
            raise ValueError("Own-worker closeout retains pairing, handoff, follow-up or unknown custody")
        if any(
            session.session_id != self.native_id and session.state == "active"
            for session in record.sessions or []
        ):
            raise ValueError("Own-worker closeout cannot finalize a shared workspace")
        own_ref = tracking.format_claim_ref(
            record.execution_machine, record.repo, record.worktree_id, session=self.native_id,
        )
        retained = None
        if readiness_claim is not None:
            from .dispatch_worker_readiness import require_task_claim
            retained = require_task_claim(record, self.task_id, readiness_claim)
        if any(
            claim.is_unsettled and claim is not retained and claim.kind != "pr"
            and not (claim.kind == "session" and claim.ref == own_ref)
            for claim in record.resources
        ):
            raise ValueError("Own-worker closeout retains unsettled outbound responsibility")
        require_no_foreign_hold(record, self.native_id, self.source)
        context = sessions.scan_sessions_fast([record])
        live = context.active_sessions.get(sessions._normalize_path(record.worktree_path), [])
        if any(native != self.native_id for native in live):
            raise ValueError("Own-worker closeout found another live workspace session")


def finalize_own(task_id: str, reservation_key: str, native_id: str) -> dict:
    """Finalize only the invoking worker's own checkout through all normal gates."""
    for name, value in (
        ("task ID", task_id), ("reservation key", reservation_key), ("native ID", native_id),
    ):
        _text(value, name)
    config = cfg.load_config()
    context = read_workspace_record(str(Path.cwd()))
    metadata = context["worktree"]
    if not isinstance(metadata, dict):
        raise ValueError("Own-worker closeout is unavailable outside a managed worktree")
    worktree_id = metadata["id"]
    path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    record = tracking.load_record(path)
    if record.dispatch_attempt is None:
        raise ValueError("Own-worker closeout requires its recorded Dispatch allocation")
    worker = OwnWorker(
        task_id, reservation_key, native_id, worktree_id, record.creation_nonce,
        Path(metadata["path"]).resolve(strict=True), _native_source(native_id), record.head_revision,
        record.dispatch_attempt,
    )
    if record.status == "finalizing":
        raise ValueError("Own-worker closeout already has an unresolved finalization")
    worker.require(record, config)
    if not finalize.validate_and_finalize(
        worktree_id, config, record_guard=lambda fresh: worker.require(fresh, config),
    ):
        raise ValueError("Own-worker closeout blocked by normal worktree finalization")
    with tracking._RecordLock(path, require_sidecar=True):
        record = tracking.load_record(path)
        worker.require(record, config)
        if not finalized_checkout.matches(record):
            raise ValueError("Own-worker finalization outcome is unconfirmed; retain task custody")
        return {
            "contract": CONTRACT, "version": 1, "operation": "finalize-own",
            "task_id": task_id, "reservation_key": reservation_key,
            "native_id": native_id, "worktree_id": worktree_id,
            "creation_nonce": worker.creation_nonce, "status": record.status,
            "head_revision": record.head_revision, "checkout_retained": True,
            "task_release": False, "process_retirement": False,
        }


def main(argv: list[str] | None = None) -> int:
    """Expose selected-runtime discovery without invoking an effectful fallback."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe-contract", action="store_true")
    parser.add_argument("--task-id")
    parser.add_argument("--reservation-key")
    parser.add_argument("--native-id")
    args = parser.parse_args(argv)
    supplied = (args.task_id, args.reservation_key, args.native_id)
    if args.describe_contract:
        if any(value is not None for value in supplied):
            parser.error("--describe-contract cannot be combined with a closeout request")
        output._json_output(describe_contract())
        return 0
    if any(value is None for value in supplied):
        parser.error("--task-id, --reservation-key and --native-id are required")
    previous_project = cfg.active_project()
    try:
        with output.stdout_to_stderr():
            project = metadata_project(None, cwd=Path.cwd())
            if project is None:
                raise ValueError("Own-worker closeout requires a project resolved from its actual cwd")
            cfg.set_active_project(project)
            result = finalize_own(*supplied)
    except Exception as exc:
        output._json_output({
            "contract": CONTRACT, "version": 1, "operation": "finalize-own",
            "ok": False, "error": str(exc), "task_release": False,
            "process_retirement": False,
        })
        return 1
    finally:
        cfg.set_active_project(previous_project)
    output._json_output({"ok": True, **result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
