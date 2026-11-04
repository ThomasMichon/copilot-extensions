"""Read-only relationship callback on the original explicitly selected writer."""

from __future__ import annotations

import argparse
import sys
from types import ModuleType

from .client_authority import client_authority
from .client_errors import DispatchError
from .task_relationships import AssignmentSubject


def claim_status(args: argparse.Namespace, *, core: ModuleType) -> int:
    """Never start a service, fail over, or turn a missing task into retirement."""
    try:
        if getattr(args, "native_restart_policy", False) or not (
            getattr(args, "local", False) or getattr(args, "shared", False)
        ) or getattr(args, "url", None):
            raise DispatchError(409, "task relationship requires its explicit local or central writer")
        subject = AssignmentSubject.decode(args.task_id, authority=client_authority(args))
        with core._client(
            args, ensure=False, allow_ssh_failover=False, read_only=True,
        ) as client:
            task = client.get(
                subject.task_id, obligation_assignment_event_id=subject.assignment_event_id,
            )
        result = subject.verification(task)
    except DispatchError as exc:
        print(f"agent-dispatch: task relationship unconfirmed HTTP {exc.status_code}: {exc.detail}",
              file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"agent-dispatch: task relationship unavailable: {exc}", file=sys.stderr)
        return 1
    return core._emit(result)
