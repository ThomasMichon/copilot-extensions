"""Reconcile attributable owner custody without spawning another process."""

from __future__ import annotations

import time

from zdd import breadcrumb, routing
from zdd.process_exit import observe_original_exit

from .watch_control import WatchControlClient
from .watch_custody import require_absent
from .watch_journal import WatchJournal, _json_object, _selection, _target_selection
from .watch_notification import positive_seconds
from .watch_publication import capture_publication, resume_cold_publication
from .watch_selection import WatchSelection


def reconcile(journal: WatchJournal, *, timeout: float) -> None:
    """The caller retains the same whole-transaction native guard throughout."""
    deadline = time.monotonic() + positive_seconds(timeout)

    def remaining() -> float:
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise TimeoutError("watch owner reconciliation deadline expired")
        return budget

    def client(role: str) -> WatchControlClient:
        instance, endpoint = journal.endpoint(role)
        return WatchControlClient(
            endpoint=endpoint, instance=instance, installation=lambda: journal.installation,
            transaction=lambda: (journal.transaction_id, journal.authorization),
            request_timeout=remaining(),
        )

    def health(owner: WatchControlClient) -> dict:
        return owner.health(timeout=remaining())

    def finish(read_health=None) -> None:
        journal.finish(read_health=read_health)
        journal.archive()
        breadcrumb.clear_breadcrumb(journal.installation.run_root)
        remaining()

    document = journal._current()
    if document["phase"] in {"complete", "rolled-back"}:
        journal.archive()
        breadcrumb.clear_breadcrumb(journal.installation.run_root)
        remaining()
        return
    metadata_only = (
        document["predecessor"] is None and document["successor"] is None
        and document["launchInstanceId"] is None
    )
    if metadata_only:
        require_absent(journal.installation)
        if document["phase"] in {"prepared", "committed"}:
            WatchSelection(journal).publish()
            if document["phase"] == "prepared":
                journal.commit()
        elif document["phase"] == "rolling-back":
            WatchSelection(journal).rollback()
        else:
            raise PermissionError("watch metadata recovery has an invalid owner phase")
        finish()
        return
    if (
        document["phase"] == "publishing" and document["predecessor"] is None
        and _selection(journal.installation)["route"] is not None
    ):
        expected = _json_object(bytes.fromhex(document["targetRoute"]))
        endpoint = routing.Endpoint.from_dict(expected.get("active", {}))
        if endpoint is None:
            raise PermissionError("watch cold recovery has no exact publication intent")
        table = routing.read_table(journal.installation.run_root, strict=True) or {}
        successor = client("successor")
        if table.get("active") is None:
            resume_cold_publication(journal, successor, deadline=deadline)
        else:
            capture_publication(journal, endpoint, deadline=deadline)
        document = journal._current()
        _target_selection(journal.installation, document)
        journal.commit(read_health=lambda: successor.read_health_response(timeout=remaining()))
        document = journal._current()
    if document["phase"] == "committed":
        successor = client("successor")
        status = health(successor)
        if status.get("writerHeld") is not True or status.get("readReady") is not True:
            raise PermissionError("watch committed recovery has no verified target writer")
        if status.get("effectsEnabled") is False:
            successor.control("activate", timeout=remaining())
        elif status.get("effectsEnabled") is not True:
            raise PermissionError("watch committed recovery has ambiguous effect state")
        predecessor = document["predecessor"]
        if predecessor is not None and "predecessor" not in document["exited"]:
            if observe_original_exit(predecessor["pid"], predecessor["startTime"]):
                journal.observe_exit("predecessor")
            else:
                old = client("predecessor")
                reference = old.capture_exit(timeout=remaining())
                try:
                    old.control("retire", timeout=remaining())
                    journal.record_exit("predecessor", reference, timeout=min(30.0, remaining()))
                finally:
                    reference.close()
        finish(lambda: successor.read_health_response(timeout=remaining()))
        return
    if document["successor"] is None and document["launchInstanceId"] is not None:
        instance, endpoint = journal.launched_successor()
        candidate = WatchControlClient(
            endpoint=endpoint, instance=instance, installation=lambda: journal.installation,
            transaction=lambda: (journal.transaction_id, journal.authorization),
            request_timeout=remaining(),
        )
        journal.bind_cleanup_successor(
            instance, endpoint=endpoint,
            read_health=lambda: candidate.cleanup_health(timeout=remaining()),
        )
    elif (
        document["phase"] == "passive" and document["launchInstanceId"] is not None
        and document["endpoints"]["successor"] is None
    ):
        instance, endpoint = journal.launched_successor()
        candidate = WatchControlClient(
            endpoint=endpoint, instance=instance, installation=lambda: journal.installation,
            transaction=lambda: (journal.transaction_id, journal.authorization),
            request_timeout=remaining(),
        )
        journal.bind_endpoint(
            "successor", endpoint,
            read_health=lambda: candidate.read_health_response(timeout=remaining()),
        )
    if journal._current()["phase"] != "rolling-back":
        journal.rollback()
    document = journal._current()
    if document["successor"] is not None and "successor" not in document["exited"]:
        instance = document["successor"]
        if observe_original_exit(instance["pid"], instance["startTime"]):
            journal.observe_exit("successor")
        else:
            successor = client("successor")
            if health(successor).get("effectsEnabled") is not False:
                raise PermissionError("watch precommit recovery cannot retire an effectful target")
            reference = successor.capture_exit(timeout=remaining())
            try:
                successor.control("retire", timeout=remaining())
                journal.record_exit("successor", reference, timeout=min(30.0, remaining()))
            finally:
                reference.close()
    WatchSelection(journal).rollback()
    document = journal._current()
    selected = _selection(journal.installation)
    if selected["route"] != document["prior"]["route"]:
        table = routing.read_table(journal.installation.run_root, strict=True) or {}
        active = routing.Endpoint.from_dict(table.get("active", {}))
        if active is None:
            raise PermissionError("watch recovery has no attributable route to restore")
        journal.restore_route(expected_active=active, deadline=deadline)
    read_health = None
    if document["predecessor"] is not None:
        predecessor = client("predecessor")
        status = health(predecessor)
        if status.get("effectsEnabled") is False:
            predecessor.control("resume", timeout=remaining())
        elif not all(status.get(key) is True for key in (
            "writerHeld", "readReady", "effectsEnabled",
        )):
            raise PermissionError("watch recovery predecessor remains unconfirmed")
        read_health = lambda: predecessor.read_health_response(timeout=remaining())
    finish(read_health)
