"""Journal publication intent inside the shared native routing CAS."""

from __future__ import annotations

import json
import time
from zdd import publication_history, routing

from .watch_journal import WatchJournal, _json_object


def _history_matches(table: dict, expected: dict, prior: dict) -> bool:
    history = publication_history.read(table)
    if history is None:
        return False
    if publication_history.KEY in expected:
        return history == expected[publication_history.KEY]
    generated = publication_history.updated(prior, expected)
    return set(history) == set(generated) and all(
        history[key] == generated[key] for key in ("version", "saturated", "entries")
    )


class WatchRouting:
    """The shared orchestrator still owns sequencing and native route mutation."""

    ActivePublicationRefused = routing.ActivePublicationRefused
    RoutingLockTimeout = routing.RoutingLockTimeout

    def __init__(self, driver) -> None:
        self.driver = driver
        self.journal: WatchJournal = driver.journal

    def read_table(self, root):
        self.journal._guard()
        if root != self.journal.installation.run_root:
            raise PermissionError("watch route read belongs to another installation")
        return routing.read_table(root, strict=True)

    def read_active_endpoint(self, root):
        table = self.read_table(root)
        return routing.Endpoint.from_dict(table["active"]) if table and table.get("active") else None

    def publish_active_with_previous_guarded(self, root, **kwargs):
        document = self.journal._current()
        if root != self.journal.installation.run_root:
            raise PermissionError("watch publication belongs to another installation")
        predecessor = document["predecessor"]
        if predecessor is not None and kwargs.get("pid") == predecessor["pid"]:
            restored = self.journal.restore_route(
                expected_active=kwargs["expected_active"], deadline=self.driver.deadline,
            )
            return restored, kwargs["expected_active"]
        self.driver.prepare_publication()
        instance = self.driver.successor.instance
        if (
            kwargs["bind"] != "127.0.0.1"
            or kwargs["port"] != self.driver.successor._endpoint[1]
            or kwargs["pid"] != instance.pid
            or kwargs["version"] != instance.runtime.runtime_version
            or kwargs.get("demote_existing") is not True
            or "generation" in kwargs
            or kwargs.get("process_start_time", instance.start_time) != instance.start_time
        ):
            raise PermissionError("watch publication differs from the exact successor")
        expected = kwargs["expected_active"]
        original_refusal = kwargs.get("refuse_current")

        def record_intent(raw):
            current = self.journal._current()
            if current["phase"] != "publishing":
                raise PermissionError("watch publication requires precommit intent")
            actual = routing.Endpoint.from_dict(raw) if raw else None
            if actual != expected:
                raise routing.ActivePublicationRefused("watch publication owner changed")
            if original_refusal is not None:
                refusal = original_refusal(raw)
                if refusal:
                    return refusal
            table = routing.read_table(root, strict=True) or {}
            target = _json_object(bytes.fromhex(current["targetRoute"]))
            prior = (
                _json_object(bytes.fromhex(current["prior"]["route"]))
                if current["prior"]["route"] is not None else {}
            )
            body = {key: value for key, value in table.items() if key != "epoch"}
            known = [
                {key: value for key, value in item.items() if key != "epoch"}
                for item in (prior, target)
            ]
            if body not in known:
                raise PermissionError("watch publication has no captured native route custody")
            health = self.driver.successor.health(timeout=self.driver.remaining())
            if (
                health.get("writerHeld") is not True or health.get("readReady") is not True
                or health.get("effectsEnabled") is not False
            ):
                raise PermissionError("watch publication requires an effect-free read-ready writer")
            generations = []
            for role in ("active", "previous"):
                if table.get(role) is not None:
                    row = table[role]
                    endpoint = routing.Endpoint.from_dict(row)
                    if endpoint is None or endpoint.to_dict() != row or (
                        type(row.get("generation")) is not int
                        or not 0 <= endpoint.generation < 2**63 - 1
                    ):
                        raise PermissionError("watch publication generation is unverifiable")
                    generations.append(endpoint.generation)
            candidate = routing.Endpoint(
                "127.0.0.1", kwargs["port"], instance.pid,
                instance.runtime.runtime_version, max([0, *generations]) + 1,
                instance.start_time,
            )
            following = {"active": candidate.to_dict()}
            if actual is not None and actual.port != candidate.port:
                following["previous"] = actual.to_dict()
            if publication_history.read(table) is not None:
                following[publication_history.KEY] = publication_history.updated(table, following)
            current["targetRoute"] = json.dumps(following, sort_keys=True).encode().hex()
            self.journal._write(current)
            self.driver.remaining()
            return None

        publication = routing.publish_active_with_previous_guarded(
            root, **{
                **kwargs, "refuse_current": record_intent,
                "process_start_time": instance.start_time,
                "lock_deadline": min(kwargs.get("lock_deadline", self.driver.deadline),
                                     self.driver.deadline),
            },
        )
        published, _ = publication
        capture_publication(self.journal, published, deadline=self.driver.deadline)
        return publication


def resume_cold_publication(journal: WatchJournal, successor, *, deadline: float) -> None:
    """Retry the exact reserved first publication after its protected history write."""
    root = journal.installation.run_root
    document = journal._current()
    expected = _json_object(bytes.fromhex(document["targetRoute"]))
    endpoint = routing.Endpoint.from_dict(expected.get("active", {}))
    prior = (
        _json_object(bytes.fromhex(document["prior"]["route"]))
        if document["prior"]["route"] is not None else {}
    )
    if prior:
        from .watch_withdrawal import require_withdrawn_route

        require_withdrawn_route(journal.installation, prior)
    generation = max([
        0, *(prior[role]["generation"] for role in ("active", "previous") if prior.get(role)),
    ]) + 1
    if (
        document["phase"] != "publishing" or document["predecessor"] is not None
        or document["launchInstanceId"] is None
        or set(expected) - {"active", publication_history.KEY}
        or endpoint is None or endpoint.generation != generation
        or document["successor"] != successor.instance.evidence()
    ):
        raise PermissionError("watch cold retry lacks exact reserved publication intent")

    def require_protected_history(raw):
        journal._current()
        table = routing.read_table(root, strict=True) or {}
        if (
            raw is not None
            or {key: value for key, value in table.items() if key != publication_history.KEY}
            != {key: value for key, value in prior.items() if key != publication_history.KEY}
            or not _history_matches(table, expected, prior)
        ):
            raise PermissionError("watch cold retry lost exact protected promotion history")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("watch cold publication recovery deadline expired")
        health = successor.health(timeout=remaining)
        if (
            health.get("writerHeld") is not True or health.get("readReady") is not True
            or health.get("effectsEnabled") is not False
        ):
            raise PermissionError("watch cold retry requires the original effect-free writer")
        return None

    published, previous = routing.publish_active_with_previous_guarded(
        root, **endpoint.to_dict(), expected_active=None, demote_existing=True,
        refuse_current=require_protected_history, lock_deadline=deadline,
    )
    if published != endpoint or previous is not None:
        raise PermissionError("watch cold retry changed the reserved publication")
    capture_publication(journal, published, deadline=deadline)


def capture_publication(
    journal: WatchJournal, published: routing.Endpoint, *, deadline: float,
) -> None:
    """Recover only the exact native publication already bound by durable intent."""
    root = journal.installation.run_root

    def capture_native_history(raw):
        current = journal._current()
        expected = _json_object(bytes.fromhex(current["targetRoute"]))
        table = routing.read_table(root, strict=True) or {}
        history = publication_history.read(table)
        if (
            current["phase"] != "publishing" or raw != published.to_dict()
            or raw != expected["active"] or table.get("previous") != expected.get("previous")
            or set(table) - {"active", "previous", "epoch", publication_history.KEY}
            or history is None
        ):
            raise PermissionError("watch native publication differs from durable intent")
        prior = (
            _json_object(bytes.fromhex(current["prior"]["route"]))
            if current["prior"]["route"] is not None else {}
        )
        if not _history_matches(table, expected, prior):
            raise PermissionError("watch native publication lost captured promotion history")
        current["targetRoute"] = json.dumps(table, sort_keys=True).encode().hex()
        journal._write(current)
        return None

    routing.validate_active(
        root, published, refuse_current=capture_native_history, lock_deadline=deadline,
    )
