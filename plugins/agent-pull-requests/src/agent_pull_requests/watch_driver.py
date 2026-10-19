"""Owner-specific custody around the public shared cutover orchestrator."""

from __future__ import annotations

import json
import time
from uuid import uuid4

from zdd import breadcrumb, routing
from zdd.cutover import CutoverOrchestrator, CutoverResult
from zdd.process_exit import ProcessExitReference

from .watch_control import WatchControlClient
from .watch_custody import launch_path
from .watch_journal import WatchJournal, _json_object
from .watch_notification import positive_seconds
from .watch_publication import WatchRouting
from .watch_selection import WatchSelection


class _Client:
    def __init__(self, driver: WatchDriver, role: str, client: WatchControlClient) -> None:
        self.driver, self.role, self.client = driver, role, client

    def health(self) -> dict:
        return self.client.health(timeout=self.driver.remaining())

    def drain(self, *, timeout: float, poll: float, force: bool) -> dict:
        if self.role != "predecessor":
            raise PermissionError("watch drain requires the exact predecessor")
        self.driver.journal.advance("draining")
        result = self.client.drain(
            timeout=min(timeout, self.driver.remaining()), poll=poll, force=force,
        )
        self.driver.journal.advance("released")
        return result

    def undrain(self) -> dict:
        if self.role != "predecessor":
            raise PermissionError("watch resumption requires the exact predecessor")
        self.client.control("resume", timeout=self.driver.remaining())
        health = self.health()
        if not all(health.get(key) is True for key in (
            "writerHeld", "readReady", "effectsEnabled",
        )):
            raise PermissionError("watch predecessor resumption remains unconfirmed")
        return {"draining": False}

    def shutdown(self) -> dict:
        if self.role != "predecessor":
            raise PermissionError("watch retirement requires the exact predecessor")
        self.driver.commit()
        self.client.control("retire", timeout=self.driver.remaining())
        self.driver.journal.record_exit(
            "predecessor", self.driver.references["predecessor"],
            timeout=min(30.0, self.driver.remaining()),
        )
        return {"retired": True}

    def adopt_relay(self) -> dict:
        if self.role != "successor":
            raise PermissionError("watch activation requires the exact successor")
        self.driver.commit()
        self.client.control("activate", timeout=self.driver.remaining())
        return {"adopted": True}


class _Handle:
    def __init__(self, driver: WatchDriver) -> None:
        self.driver = driver
        self.pid = driver.successor.instance.pid

    def terminate(self) -> None:
        journal = self.driver.journal
        if journal._current()["phase"] != "rolling-back":
            journal.rollback()
        self.driver.successor.control("retire", timeout=self.driver.remaining())
        journal.record_exit(
            "successor", self.driver.references["successor"],
            timeout=min(30.0, self.driver.remaining()),
        )
        WatchSelection(journal).rollback()

    def poll(self) -> int | None:
        return 0 if self.driver.references["successor"].wait(0) else None


class WatchDriver:
    """No shared success result substitutes for owner commit, exit and writer proof."""

    def __init__(self, journal: WatchJournal, *, spawn, pick_free_port, timeout: float) -> None:
        self.journal = journal
        self.deadline = time.monotonic() + positive_seconds(timeout)
        self.spawn = spawn
        self.pick_free_port = pick_free_port
        self.references: dict[str, ProcessExitReference] = {}
        self.clients: dict[str, _Client] = {}
        self.successor: WatchControlClient

    def remaining(self) -> float:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("watch owner cutover deadline expired")
        return remaining

    def _client(self, instance, endpoint) -> WatchControlClient:
        return WatchControlClient(
            endpoint=endpoint, instance=instance,
            installation=lambda: self.journal.installation,
            transaction=lambda: (self.journal.transaction_id, self.journal.authorization),
            request_timeout=self.remaining(),
        )

    def _remember(self, role, instance, endpoint) -> WatchControlClient:
        client = self._client(instance, endpoint)
        self.references[role] = client.capture_exit(timeout=self.remaining())
        self.clients[f"http://{endpoint[0]}:{endpoint[1]}"] = _Client(self, role, client)
        return client

    def _spawn(self, port: int) -> _Handle:
        instance_id = str(uuid4())
        self.journal.reserve_successor(instance_id)
        self.spawn(port, instance_id)
        receipt = launch_path(self.journal.installation, self.journal._document)
        while True:
            if not receipt.exists():
                time.sleep(min(0.05, self.remaining()))
                continue
            try:
                instance, endpoint = self.journal.launched_successor()
                break
            except FileNotFoundError:
                time.sleep(min(0.05, self.remaining()))
        if endpoint[:2] != ("127.0.0.1", port):
            raise PermissionError("watch reserved child bound a different endpoint")
        self.successor = self._remember("successor", instance, endpoint)
        candidate = routing.Endpoint(
            "127.0.0.1", port, instance.pid, instance.runtime.runtime_version,
            process_start_time=instance.start_time,
        )
        self.journal.bind_successor(
            instance, route=json.dumps({"active": candidate.to_dict()}).encode(),
        )
        self.journal.bind_endpoint(
            "successor", endpoint,
            read_health=lambda: self.successor.read_health_response(timeout=self.remaining()),
        )
        return _Handle(self)

    def _health(self, host: str, port: int) -> bool:
        client = self.clients.get(f"http://{host}:{port}")
        if client is None:
            raise PermissionError("watch health endpoint has no private custody")
        health = client.health()
        return health.get("instance") == client.client.instance.evidence()

    def prepare_publication(self) -> None:
        document = self.journal._current()
        if document["predecessor"] is None and document["phase"] == "passive":
            self.journal.advance("draining")
            self.journal.advance("released")
            phase = "released"
        else:
            phase = document["phase"]
        if phase == "released":
            self.successor.control("adopt", timeout=self.remaining())
            self.journal.advance("read-ready")
            phase = "read-ready"
        if phase == "read-ready":
            self.journal.advance("publishing")
            phase = "publishing"
            WatchSelection(self.journal).publish()
        if phase != "publishing":
            raise PermissionError("watch publication cannot cross owner commit")

    def commit(self) -> None:
        document = self.journal._document
        if document["phase"] == "publishing":
            self.journal.commit(
                read_health=lambda: self.successor.read_health_response(timeout=self.remaining()),
            )
        elif document["phase"] != "committed":
            raise PermissionError("watch activation requires owner commit")

    def run(self) -> CutoverResult:
        document = self.journal._current()
        if document["phase"] != "prepared":
            raise PermissionError("watch pending transaction requires explicit reconciliation")
        predecessor = document["predecessor"]
        if predecessor is not None:
            instance, endpoint = self.journal.endpoint("predecessor")
            predecessor_client = self._remember("predecessor", instance, endpoint)
        prior = (
            _json_object(bytes.fromhex(document["prior"]["route"]))
            if document["prior"]["route"] is not None else {}
        )

        def refuse_old(raw):
            self.journal._current()
            return None if raw == prior.get("active") else "watch predecessor route changed"

        orchestrator = CutoverOrchestrator(
            self.journal.installation.run_root, bind="127.0.0.1",
            version=document["target"]["runtimeVersion"],
            spawn_passive=self._spawn, health_check=self._health,
            make_client=lambda url: self.clients[url], pick_free_port=self.pick_free_port,
            refuse_old=refuse_old, routing_mod=WatchRouting(self),
            drain_before_publish=True, service="agent-pull-requests-watch",
        )
        try:
            result = orchestrator.run(
                held_lock=self.journal.guard, health_timeout=self.remaining(),
                drain_timeout=self.remaining(), poll=0.05,
            )
            current = self.journal._current()
            if current["phase"] == "committed":
                if predecessor is not None and "predecessor" not in current["exited"]:
                    reference = self.references["predecessor"]
                    if not reference.wait(0):
                        predecessor_client.control("retire", timeout=self.remaining())
                    self.journal.record_exit(
                        "predecessor", reference, timeout=min(30.0, self.remaining()),
                    )
                self.journal.finish(
                    read_health=lambda: self.successor.read_health_response(timeout=self.remaining()),
                )
                self.journal.archive()
            elif current["phase"] == "rolling-back" and result.rolled_back:
                health = None
                if predecessor is not None:
                    instance, endpoint = self.journal.endpoint("predecessor")
                    owner = self._client(instance, endpoint)
                    health = lambda: owner.read_health_response(timeout=self.remaining())
                self.journal.finish(read_health=health)
                self.journal.archive()
            else:
                raise RuntimeError("watch owner transaction remains pending reconciliation")
            breadcrumb.clear_breadcrumb(self.journal.installation.run_root)
            self.remaining()
            return result
        finally:
            for reference in self.references.values():
                reference.close()
