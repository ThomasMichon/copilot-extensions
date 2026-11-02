"""Settle monitor publication without turning unknown outcomes into rollback."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from zdd import breadcrumb

from . import runtime_monitor_recovery as recovery


class PublicationOutcomePending(RuntimeError):
    """Durable custody remains; the installer must not restore or claim commit."""


def settle_monitor(
    root: Path, slot: Path, summary: dict, predecessor: tuple[int, str] | None,
    handles: list[subprocess.Popen], *,
    candidate_serving: Callable[[Path, Path], bool], refused: type[Exception],
) -> bool:
    action = summary.get("action")
    if action == "noop":
        if summary.get("reason") not in ("disabled", "no-live-monitor"):
            raise refused(f"Monitor transition refused: {summary.get('reason')}")
        return True
    result = summary.get("result") or {}
    committed = result.get("committed") is True
    restart = summary.get("restart") or {}
    committed = committed or (action == "restart" and (
        restart.get("spawned") is True or restart.get("already_current") is True
    ))
    if committed and result.get("committed") is True and not result.get("publication_uncertain"):
        if result.get("ok") is not False:
            try:
                ready = candidate_serving(root, slot)
            except (OSError, ValueError, ConnectionError) as error:
                ready = False
                result["error"] = f"Committed serving attestation failed: {error}"
            if not ready:
                result["ok"] = False
                result["error"] = result.get("error") or "Committed successor stopped serving after publication"
        summary["result"] = result
        return True
    announcements: set[str] = set()

    def announce(message: str) -> None:
        if message not in announcements:
            print(message, file=sys.stderr, flush=True)
            announcements.add(message)

    def again() -> bool:
        return settle_monitor(
            root, slot, summary, predecessor, handles,
            candidate_serving=candidate_serving, refused=refused,
        )

    def recovered_outcome(outcome) -> bool:
        if outcome == "rolled_back":
            result.update(committed=False, publication_uncertain=False, rolled_back=True, ok=False)
            summary["result"] = result
            return False
        result.update(committed=True, publication_uncertain=False, ok=outcome == "committed")
        if outcome == "committed":
            result["error"] = None
        summary["result"] = result
        return again()

    while True:
        directory = root / "status-monitor-routing"
        interrupted_record = breadcrumb.read_breadcrumb(directory) if action == "interrupted" else None
        if action == "interrupted" and (
            interrupted_record is not None and (
                breadcrumb.is_stale(interrupted_record)
                or interrupted_record.get("self_retirement") is True
                and interrupted_record.get("state") == "committed"
            )
            or interrupted_record is None and breadcrumb.breadcrumb_path(directory).exists()
        ):
            result["publication_uncertain"] = True
        if result.get("publication_uncertain"):
            record = breadcrumb.read_breadcrumb(directory)
            if record is not None and record.get("self_retirement") is True and record.get("state") == "committed":
                try:
                    recovered = recovery.recover_owned_publication(root, record, predecessor, handles)
                except (OSError, ValueError, RuntimeError, ConnectionError) as error:
                    recovered = False
                    result["error"] = f"Committed monitor recovery unavailable: {error}"
                return recovered_outcome(recovered)
            if record is None or record.get("state") not in ("committed", "rolled_back", "failed"):
                announce("Monitor publication is uncertain; retaining both generations for owner-led recovery")
                if record is not None:
                    try:
                        recovered = recovery.recover_owned_publication(root, record, predecessor, handles)
                        if recovered in ("committed", "rolled_back"):
                            return recovered_outcome(recovered)
                        if recovered:
                            continue
                    except (OSError, ValueError, RuntimeError, ConnectionError) as error:
                        result["error"] = f"Owned monitor recovery unavailable: {error}"
                        announce(result["error"])
                if summary.get("self_retirement") is True or (
                    isinstance(record, dict) and record.get("self_retirement") is True
                ):
                    message = result.get("error") or "Monitor publication remains unresolved; candidate and witness retained"
                    result.update(ok=False, publication_uncertain=True, error=message)
                    summary["result"] = result
                    raise PublicationOutcomePending(message)
                time.sleep(1)
                continue
            committed = record["state"] == "committed"
            if committed:
                return recovered_outcome("committed")
        try:
            ready = candidate_serving(root, slot)
        except (OSError, ValueError, ConnectionError) as error:
            announce(f"Monitor serving attestation unavailable: {error}")
            time.sleep(0.2)
            continue
        if ready:
            return True
        live_handles = [handle for handle in handles if handle.poll() is None]
        if live_handles and result.get("rolled_back"):
            record = breadcrumb.read_breadcrumb(directory)
            if record is not None:
                try:
                    recovery.recover_owned_publication(root, record, predecessor, handles)
                except (OSError, ValueError, RuntimeError, ConnectionError) as error:
                    announce(f"Owned monitor recovery unavailable: {error}")
        if not live_handles and (not committed or handles):
            return False
        announce("Retaining both generations until the candidate serves or every failed child exits")
        time.sleep(0.2)
