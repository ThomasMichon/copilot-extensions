"""Exact exit evidence remains separate from writer and lifecycle authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

from zdd.process_exit import ProcessExitReference, observe_original_exit

if TYPE_CHECKING:
    from .watch_journal import WatchJournal


def _obsolete(document: dict, role: str) -> dict:
    phase = "rolling-back" if role == "successor" else "committed"
    instance = document.get(role) if role in {"predecessor", "successor"} else None
    if document["phase"] != phase or instance is None:
        raise PermissionError("watch exit evidence is not authorized for this phase")
    return instance


def record_exit(
    journal: WatchJournal, role: str, reference: ProcessExitReference, *, timeout: float,
) -> None:
    document = journal._current()
    instance = _obsolete(document, role)
    if (reference.pid, reference.start_time) != (instance["pid"], instance["startTime"]):
        raise PermissionError("watch exit reference belongs to another instance")
    if not reference.wait(timeout):
        raise TimeoutError("watch instance has not exited; writer resumption is blocked")
    if role not in document["exited"]:
        document["exited"].append(role)
        journal._write(document)


def observe_exit(journal: WatchJournal, role: str) -> None:
    """Recover lost reference custody, never from tolerant PID diagnostics."""
    document = journal._current()
    instance = _obsolete(document, role)
    if observe_original_exit(instance["pid"], instance["startTime"]) is not True:
        raise TimeoutError("watch original instance remains live; reconciliation is blocked")
    if role not in document["exited"]:
        document["exited"].append(role)
        journal._write(document)


def observe_cleanup_exit(journal: WatchJournal) -> None:
    from .watch_custody import endpoint_value, read_launch
    from .watch_journal import _cleanup_document

    document = journal._current_identity()
    journal.installation.require_cleanup(environment=journal.environment)
    _cleanup_document(journal.installation, document)
    if document["successor"] is None:
        instance, endpoint = read_launch(
            journal.installation, document, environment=journal.environment,
        )
        document["successor"] = instance.evidence()
        document["endpoints"]["successor"] = endpoint_value(endpoint)
    instance = document["successor"]
    if observe_original_exit(instance["pid"], instance["startTime"]) is not True:
        raise TimeoutError("watch original passive remains live; cleanup is blocked")
    if "successor" not in document["exited"]:
        document["exited"].append("successor")
        journal._write_cleanup(document)
