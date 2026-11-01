"""Late source callbacks must not undo a newer confirmed absence."""

from __future__ import annotations

import json
import copy
import time
from collections.abc import Iterator
from threading import Event
from typing import Any

import pytest

from agent_dispatch.pool_source_snapshot import DeclarationRead
from agent_dispatch.pool_source_snapshot import read_supervisor_declarations
from agent_dispatch.registrar_discovery import RegistrarSources, add_pointer
from tests.test_pool_publication_custody import owner as owner


def test_timed_out_actual_registrar_recheck_cannot_restore_confirmed_deletion(
    owner: Any, tmp_path: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch import registrar_discovery

    daemon, _queue, _calls = owner
    daemon.client.set_regs([])
    daemon._reconcile_call_budget = 0.1
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    document = trusted / "worker.json"
    document.write_text(json.dumps({
        "name": "worker", "owner": "repo:example", "labels": ["worker"],
    }), encoding="utf-8")
    base = tmp_path / "registrar"
    add_pointer("trusted", trusted, base=base)
    sources = RegistrarSources(
        base=base, dropins=tmp_path / "absent-registrar.d",
        activation_source=lambda: None,
    )
    daemon.declared_source = lambda: read_supervisor_declarations(sources)
    discover = registrar_discovery.discover_trusted
    recheck_started = Event()
    release_old = Event()
    deletion_observed = Event()
    scans = 0

    def controlled_scan(*, base: Any) -> Any:
        nonlocal scans
        scans += 1
        result = discover(base=base)
        if scans == 2:
            recheck_started.set()
            assert release_old.wait(3)
        if not result[1]:
            deletion_observed.set()
        return result

    monkeypatch.setattr(registrar_discovery, "discover_trusted", controlled_scan)
    try:
        inputs = daemon.capture_pool_publication_inputs()
        assert recheck_started.is_set() and not inputs.coherent
        old = daemon._discovery_futures["pool-local-recheck"]
        assert not old.done()
        document.unlink()
        daemon._reconcile_deadline = time.monotonic() + 0.1
        daemon._desired()
        current = daemon._discovery_futures["declarations"]
        assert not current.done() and not deletion_observed.is_set()
        assert not daemon.pool_source_snapshot().complete
        release_old.set()
        old.result(timeout=1)
        current.result(timeout=1)
        assert deletion_observed.is_set() and sources._trusted == []
        daemon._reconcile_deadline = time.monotonic() + 1
        assert daemon._desired() == {}
        daemon._reconcile_deadline = None
        assert daemon._desired() == {}
        assert daemon.pool_source_snapshot().complete
        # An indeterminate scan must now retain the confirmed empty set.
        (base / "pointers.json").write_text("{invalid", encoding="utf-8")
        assert daemon._desired() == {}
        assert sources._trusted == [] and daemon._last_declared == []
        assert not daemon.pool_source_snapshot().complete
    finally:
        release_old.set()
        daemon._reconcile_deadline = None


def test_timed_out_override_recheck_uses_the_same_stateful_reader(owner: Any) -> None:
    daemon, _queue, _calls = owner
    daemon._reconcile_call_budget = 0.1
    value = {"logical:repo:example:worker": {"disabled": True}}
    retained: dict = {}
    reads = 0
    recheck_started = Event()
    release_old = Event()
    absence_observed = Event()

    def overrides() -> dict:
        nonlocal reads, retained
        reads += 1
        observed = {key: dict(record) for key, record in value.items()}
        if reads == 2:
            recheck_started.set()
            assert release_old.wait(3)
        retained = observed
        if not observed:
            absence_observed.set()
        return observed

    daemon.overrides_source = overrides
    try:
        inputs = daemon.capture_pool_publication_inputs()
        assert recheck_started.is_set() and not inputs.coherent
        old = daemon._discovery_futures["pool-local-recheck"]
        value = {}
        daemon._reconcile_deadline = time.monotonic() + 0.1
        assert daemon._effective_registrations() == {}
        current = daemon._discovery_futures["overrides"]
        assert not current.done() and not absence_observed.is_set()
        release_old.set()
        old.result(timeout=1)
        current.result(timeout=1)
        assert absence_observed.is_set() and retained == {}
        daemon._reconcile_deadline = time.monotonic() + 1
        assert daemon._effective_registrations()
        assert not daemon.pool_source_snapshot().complete
        daemon._reconcile_deadline = None
        assert daemon._effective_registrations()
        assert daemon.pool_source_snapshot().complete
    finally:
        release_old.set()
        daemon._reconcile_deadline = None


def test_declaration_reader_guard_covers_lazy_callback_consumption(owner: Any) -> None:
    from tests.test_pool_source_collection import _declaration

    daemon, _queue, _calls = owner
    daemon.client.set_regs([])
    daemon._reconcile_call_budget = 0.1
    values = [_declaration()]
    retained: list = []
    reads = 0
    recheck_started = Event()
    release_old = Event()
    empty_observed = Event()

    def declarations() -> Iterator[Any]:
        nonlocal reads, retained
        reads += 1
        observed = list(values)
        yield from observed
        if reads == 2:
            recheck_started.set()
            assert release_old.wait(3)
        retained = observed
        if not observed:
            empty_observed.set()

    daemon.declared_source = declarations
    try:
        assert not daemon.capture_pool_publication_inputs().coherent
        assert recheck_started.is_set()
        old = daemon._discovery_futures["pool-local-recheck"]
        values = []
        daemon._reconcile_deadline = time.monotonic() + 0.1
        daemon._desired()
        current = daemon._discovery_futures["declarations"]
        assert not current.done() and not empty_observed.is_set()
        release_old.set()
        old.result(timeout=1)
        current.result(timeout=1)
        assert empty_observed.is_set() and retained == []
        daemon._reconcile_deadline = time.monotonic() + 1
        assert daemon._desired() == {}
        assert daemon._last_declared == []
    finally:
        release_old.set()
        daemon._reconcile_deadline = None


@pytest.mark.parametrize("source", ["declarations", "registrations", "overrides"])
def test_new_synchronous_observation_supersedes_cached_older_future(owner: Any, source: str) -> None:
    from tests.test_pool_source_collection import _declaration

    daemon, _queue, _calls = owner
    started = Event()
    release_old = Event()
    pause = False
    value: Any = DeclarationRead((_declaration(),), True) if source == "declarations" else {}

    def read() -> Any:
        nonlocal pause
        observed = value if source == "declarations" else copy.deepcopy(value)
        if pause:
            pause = False
            started.set()
            assert release_old.wait(3)
        return observed

    if source == "declarations":
        daemon.client.set_regs([])
        daemon.declared_source = read
    elif source == "overrides":
        daemon.overrides_source = read
    else:
        list_registrations = daemon.client.list_registrations

        def registrations(**kwargs: Any) -> Any:
            nonlocal pause
            observed = copy.deepcopy(list_registrations(**kwargs))
            if pause:
                pause = False
                started.set()
                assert release_old.wait(3)
            return observed

        daemon.client.list_registrations = registrations
    try:
        assert daemon._desired()
        pause = True
        daemon._reconcile_deadline = time.monotonic() + 0.1
        daemon._desired()
        assert started.is_set()
        old = daemon._discovery_futures[source]
        assert not old.done() and old in daemon._probe_inflight
        if source == "declarations":
            value = DeclarationRead((), True)
        elif source == "overrides":
            value = {"logical:repo:example:worker": {"disabled": True}}
        else:
            daemon.client.set_regs([])
        release_old.set()
        old.result(timeout=1)
        daemon._reconcile_deadline = None
        assert daemon._desired() == {}
        daemon._reconcile_deadline = time.monotonic() + 1
        assert daemon._desired() == {}
    finally:
        release_old.set()
        daemon._reconcile_deadline = None
