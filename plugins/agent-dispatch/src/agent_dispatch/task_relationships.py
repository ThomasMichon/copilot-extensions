"""Exact assignment settlement evidence for Worktrees' retained claim owner."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass

from .client_errors import DispatchError
from .task_obligations import valid_snapshot

SCHEMA = "dispatch-task-assignment"
VERSION = 1


def _unique(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate task relationship member")
        result[key] = value
    return result


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")


@dataclass(frozen=True)
class AssignmentSubject:
    """Original writer and audited assignment, never a worker's status assertion."""

    subject: str
    task_id: str
    queue_identity: str
    assignment_event_id: int
    worker_id: str
    request_id: str | None = None
    creator: str | None = None
    recovery: dict | None = None

    @classmethod
    def decode(cls, bare: str, *, authority: str) -> AssignmentSubject:
        """Accept only the existing canonical bound retained-relationship encoding."""
        try:
            if authority not in {"local", "central"}:
                raise ValueError("task relationship requires explicit local or central authority")
            subject = f"dispatch-{authority}-task:{bare}"
            if not bare.startswith("l1.") or len(subject.encode("utf-8")) > 4096:
                raise ValueError("invalid task relationship subject")
            encoded = bare[3:]
            envelope = json.loads(
                base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True),
                object_pairs_hook=_unique,
            )
            fields = {"version", "token", "creator", "request_id", "recovery", "phase"}
            if not isinstance(envelope, dict) or set(envelope) not in (fields, fields | {"identity"}):
                raise ValueError("task relationship has invalid fields")
            if (
                type(envelope["version"]) is not int or envelope["version"] != 2
                or envelope["phase"] not in {"bound", "pending"}
                or not isinstance(envelope["token"], str)
                or re.fullmatch(r"[A-Za-z0-9_-]{32,128}", envelope["token"]) is None
                or not isinstance(envelope["request_id"], str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", envelope["request_id"]) is None
            ):
                raise ValueError("unsupported task relationship identity")
            if base64.urlsafe_b64encode(_canonical(envelope)).decode("ascii").rstrip("=") != encoded:
                raise ValueError("noncanonical task relationship subject")
            recovery = envelope["recovery"]
            if not isinstance(recovery, dict):
                raise ValueError("invalid original task recovery")
            recovery_version = recovery.get("version")
            recovery_fields = {"schema", "version", "authority", "queue_identity", "task_id"}
            if type(recovery_version) is not int or recovery_version not in {1, 2}:
                raise ValueError("unsupported task recovery version")
            if recovery_version == 2:
                recovery_fields |= {"assignment_event_id", "worker_id"}
                identity = {key: recovery.get(key) for key in ("assignment_event_id", "worker_id")}
                if envelope["phase"] == "bound" and envelope.get("identity") != identity:
                    raise ValueError("bound assignment conflicts with retained recovery")
            else:
                if envelope["phase"] != "bound":
                    raise ValueError("legacy task recovery must be bound")
                identity = envelope.get("identity")
            if (envelope["phase"] == "pending") == ("identity" in envelope):
                raise ValueError("task relationship phase conflicts with identity")
            if (
                set(recovery) != recovery_fields
                or recovery["schema"] != SCHEMA or recovery["authority"] != authority
                or not isinstance(recovery["queue_identity"], str)
                or re.fullmatch(r"[0-9a-f]{32}", recovery["queue_identity"]) is None
                or not isinstance(recovery["task_id"], str)
                or re.fullmatch(r"[0-9a-f]{32}", recovery["task_id"]) is None
                or not isinstance(identity, dict)
                or set(identity) != {"assignment_event_id", "worker_id"}
                or type(identity["assignment_event_id"]) is not int
                or not 1 <= identity["assignment_event_id"] <= 2**63 - 1
                or not isinstance(identity["worker_id"], str)
            ):
                raise ValueError("invalid original task assignment")
            creator = envelope["creator"]
            parts = creator.split("/", 2) if isinstance(creator, str) and "#" not in creator else []
            if (
                len(parts) != 3 or not all(parts)
                or identity["worker_id"] != f"{parts[0]}/{parts[2]}"
            ):
                raise ValueError("task relationship creator does not match its assigned worker")
            return cls(
                subject, recovery["task_id"], recovery["queue_identity"],
                identity["assignment_event_id"], identity["worker_id"],
                envelope["request_id"] if recovery_version == 2 else None,
                creator if recovery_version == 2 else None,
                recovery if recovery_version == 2 else None,
            )
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise DispatchError(409, f"task relationship refused: {exc}") from exc

    def verification(self, task: dict) -> dict:
        """Require one original-queue snapshot; missing and old providers never retire."""
        from .assignment_workspace import same_assignment_worker

        value = task.get("task_obligation") if isinstance(task, dict) else None
        if (
            not valid_snapshot(value, task_id=self.task_id)
            or value.get("queue_identity") != self.queue_identity
            or value.get("assignment") != {
                "event_id": self.assignment_event_id, "worker_id": self.worker_id,
            }
        ):
            raise DispatchError(409, "original queue and exact task assignment are unconfirmed")
        if self.recovery is not None:
            request = value.get("relationship")
            workspace = request.get("workspace") if isinstance(request, dict) else None
            if (
                not isinstance(workspace, dict)
                or request.get("request_id") != self.request_id
                or request.get("recovery") != self.recovery
                or f"{workspace.get('machine')}/{workspace.get('project')}/{workspace.get('worktree')}" != self.creator
            ):
                raise DispatchError(409, "original task relationship request is unconfirmed")
        outcome = value["release_outcome"]
        retired = outcome == "completed" or (
            outcome == "unassigned_queued"
            and same_assignment_worker(value["unassignment"]["worker_id"], self.worker_id)
            and value["unassignment"]["event_id"] > self.assignment_event_id
        ) or (
            outcome == "relevance_closeout_ready" and self.recovery is not None
            and value["closeout"]["relationship_request_id"] == self.request_id
        )
        return {
            "exists": True, "state": value["state"],
            "relationship_verification": {
                "version": VERSION, "subject": self.subject,
                "retired": retired, "quiescent": retired,
            },
        }
