"""Named receiving operation for one journaled allocation, not an arbitrary executor."""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import config as cfg, creation_requests, execution_spaces, owned_worktree as allocation
from . import receiving_allocation as receiving, receiving_creation_journal as journal
from . import resource_producer as producer, tracking, worktree_creation


def _request(payload: object, config: cfg.Config) -> tuple[receiving.ReceivingAllocation, allocation.AllocationIdentity | None]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema", "version", "origin", "request_id", "token", "target", "identity",
    }:
        raise ValueError("invalid receiving worktree allocation fields")
    if payload["schema"] != allocation.SCHEMA or type(payload["version"]) is not int or payload["version"] != allocation.VERSION:
        raise ValueError("unsupported receiving worktree allocation schema/version")
    producer.canonical_json(payload)
    admission = receiving.ReceivingAllocation.from_fields({
        key: payload[key] for key in ("origin", "request_id", "token", "target")
    })
    admission.require_current(config)
    identity = (
        allocation.AllocationIdentity.from_dict(payload["identity"])
        if payload["identity"] is not None else None
    )
    if identity is not None and allocation.qualified_ref(identity.ref)[:2] != (
        admission.target["machine"], admission.target["project"],
    ):
        raise ValueError("receiving identity names a different execution space/project")
    return admission, identity


def _options(admission: receiving.ReceivingAllocation) -> dict:
    options = admission.target.get("options", {})
    if set(options) - {
        "kind", "interface", "origin", "owner", "name", "bound_agent",
        "caller_worktree", "dispatch_attempt", "no_pair",
    }:
        raise ValueError("unsupported receiving creation option")
    if options.get("kind", "session") not in {"session", "bridge", "system"}:
        raise ValueError("invalid receiving worktree kind")
    if "no_pair" in options and type(options["no_pair"]) is not bool:
        raise ValueError("receiving no_pair must be a boolean")
    for field in ("owner", "name", "bound_agent", "caller_worktree"):
        if field in options and (not isinstance(options[field], str) or not options[field].strip()):
            raise ValueError(f"receiving {field} must be a nonempty string")
    if "interface" in options and options["interface"] not in ("cli", "acp"):
        raise ValueError("invalid receiving interface")
    if "origin" in options and options["origin"] not in ("user", "delegate", "system"):
        raise ValueError("invalid receiving origin")
    if "dispatch_attempt" in options:
        attempt = options["dispatch_attempt"]
        fields = {"task_id", "reservation_key", "attempt", "driver", "supervisor"}
        if (
            not isinstance(attempt, dict) or set(attempt) != fields
            or type(attempt["attempt"]) is not int or attempt["attempt"] < 1
            or any(not isinstance(attempt[key], str) or not attempt[key] for key in fields - {"attempt"})
        ):
            raise ValueError("invalid receiving dispatch attempt")
    return options


def _response(
    admission: receiving.ReceivingAllocation, *, status: str, finished: bool,
    identity: allocation.AllocationIdentity | None = None,
) -> dict:
    return {
        "schema": allocation.SCHEMA, "version": allocation.VERSION, **admission.to_fields(),
        "identity": identity.to_dict() if identity is not None else None,
        "status": status, "allocation_finished": finished,
    }


def _record(row: dict, admission: receiving.ReceivingAllocation, config: cfg.Config) -> tracking.WorktreeRecord | None:
    saved = row["worktree"]
    if row["status"] in {"complete", "terminal"}:
        saved = saved.get("worktree") if isinstance(saved, dict) else None
    if not isinstance(saved, dict) or not isinstance(saved.get("id"), str):
        return None
    allocation.qualified_ref(f"{admission.target['machine']}/{config.repo_name}/{saved['id']}")
    records = cfg.tracking_dir(config.repo_name)
    path = records / f"{saved['id']}.yaml"
    if path.is_symlink() or path.resolve().parent != records.resolve():
        raise ValueError("receiving child ledger is redirected")
    if not path.exists():
        return None
    record = tracking.load_record(path)
    execution_spaces.require_record_mutation(record, config)
    if producer.canonical_json(receiving.parsed_receipt(record).to_fields()) != producer.canonical_json(admission.to_fields()):
        raise ValueError("receiving operation does not own this child")
    return record


def receive(operation: str, payload: object, config: cfg.Config) -> dict:
    """Execute/recover one original request, with no origin callback or agent launch."""
    if operation not in {"create", "inspect", "confirm", "cancel", "result"}:
        raise ValueError("unsupported receiving allocation operation")
    admission, expected = _request(payload, config)
    options = _options(admission) if operation == "create" and expected is None else {}
    if operation in {"inspect", "result"} and creation_requests.inspect(config, admission.key) is None:
        proof = _response(admission, status="unknown", finished=False)
        if operation == "result":
            from .allocation_creation_result import response

            return response(proof, None, None)
        return proof
    digest = hashlib.sha256(admission.key.encode()).hexdigest()
    # This is an operation lock, not the child record's short RMW lock. A
    # concurrent inspector reports pending rather than overtaking a live creator.
    path = cfg.tracking_dir(config.repo_name).parent / "creation-operations" / f"{digest}.yaml"
    with tracking._RecordLock(path, blocking=False, require_sidecar=True) as guard:
        if operation == "result":
            from .allocation_creation_result import response

            proof = _receive("inspect", admission, expected, options, config, exclusive=guard.acquired)
            row = creation_requests.inspect(config, admission.key)
            record = (
                _record(row, admission, config)
                if row is not None and proof["status"] in {"allocated", "confirmed"} else None
            )
            return response(proof, row, record)
        return _receive(operation, admission, expected, options, config, exclusive=guard.acquired)


def _receive(
    operation: str, admission: receiving.ReceivingAllocation,
    expected: allocation.AllocationIdentity | None, options: dict,
    config: cfg.Config, *, exclusive: bool,
) -> dict:
    if operation == "cancel":
        if not exclusive:
            return _response(admission, status="pending", finished=False)
        if expected is not None:
            raise ValueError("cannot cancel a bound receiving allocation")
        journal.cancel(config, admission)
    if operation == "create" and expected is None and exclusive:
        retained = creation_requests.inspect(config, admission.key)
        if retained is not None and retained["status"] == "terminal" and retained["worktree"] == {"cancelled": True}:
            if producer.canonical_json(retained["facts"].get("receiving_allocation")) != producer.canonical_json(admission.to_fields()):
                raise ValueError("receiving request conflicts with its sealed original operation")
            return _response(admission, status="cancelled", finished=True)
        try:
            worktree_creation._create_worktree_core(
                config, **options, owner_ref=admission.origin,
                receiving_allocation=admission, creation_key=admission.key,
                build_launch_plan=False, identity_mode="tuple-v1", inherit_parent_session=False,
            )
        except creation_requests.CreationRequestFailure as exc:
            if exc.status not in {"pending", "terminal"}:
                raise
    row = creation_requests.inspect(config, admission.key)
    if row is None:
        return _response(admission, status="unknown", finished=False)
    if producer.canonical_json(row["facts"].get("receiving_allocation")) != producer.canonical_json(admission.to_fields()):
        raise ValueError("receiving request conflicts with original allocation facts")
    if row["status"] == "terminal" and row["worktree"] == {"cancelled": True}:
        if expected is not None:
            raise ValueError("cancelled receiving allocation has no child identity")
        return _response(admission, status="cancelled", finished=True)
    concluded = journal.concluded_identity(row, admission)
    if concluded is not None:
        identity, status = concluded
        if expected is not None and expected != identity:
            raise ValueError("receiving conclusion names a different child incarnation")
        return _response(admission, status=status, finished=True, identity=identity)
    record = _record(row, admission, config)
    if record is None:
        return _response(admission, status="unknown", finished=False)
    identity = receiving.record_identity(record)
    if expected is not None and expected != identity:
        raise ValueError("receiving operation names a different child incarnation")
    if record.status in {"finalized", "archived"}:
        journal.note_conclusion(record, config)
        return _response(admission, status=record.status, finished=True, identity=identity)
    if row["status"] == "pending" and exclusive:
        from .receiving_creation_recovery import recover

        if recover(record, config, row):
            row = creation_requests.inspect(config, admission.key)
    if row["status"] != "complete":
        return _response(admission, status="pending", finished=False, identity=identity)
    if not Path(record.worktree_path).is_dir():
        return _response(admission, status="unknown", finished=False, identity=identity)
    if operation == "confirm":
        if not exclusive:
            return _response(admission, status="pending", finished=False, identity=identity)
        if expected is None:
            raise ValueError("origin acknowledgement requires the exact bound child identity")
        path = record.yaml_path
        with tracking._RecordLock(path, require_sidecar=True):
            record = tracking.load_record(path)
            execution_spaces.require_record_mutation(record, config)
            if (
                receiving.record_identity(record) != expected
                or record.status in {"finalizing", "finalized", "archived"}
            ):
                raise ValueError("receiving child changed during acknowledgement")
            record.receiving_allocation = {**record.receiving_allocation, "phase": "linked"}
            with receiving.transaction(path):
                tracking.save_record(record, path)
            journal.note_link(record, config)
    status = "confirmed" if record.receiving_allocation["phase"] == "linked" else "allocated"
    return _response(admission, status=status, finished=True, identity=identity)
