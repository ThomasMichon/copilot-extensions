"""Operator allocation policy at the managed CLI scheduling boundary."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as cli, identity, ordinary_bridge
from agent_dispatch.queue import TaskQueue
from agent_dispatch.supervisor_pool_allocator import instance_allocation_enabled
from tests import test_pool_placement_client as controls
from tests.test_pool_placement import pool, publish, task

q = controls.q
api = controls.api
SELECTOR = '{"registration_id":"worker","machine":"host-a","env":"linux"}'
SELECTOR_SET = '{"instances":[' + SELECTOR + ',' + SELECTOR.replace('"worker"', '"other"') + ']}'
SETTING = "AGENT_DISPATCH_POOL_ALLOCATION"


@pytest.mark.parametrize(("setting", "registration", "machine", "env", "allowed"), [
    (SELECTOR, "worker", "host-a", "linux", True),
    (SELECTOR, "other", "host-a", "linux", False),
    (SELECTOR, "worker", "host-b", "linux", False),
    (SELECTOR, "worker", "host-a", "other", False),
    (SELECTOR_SET, "worker", "host-a", "linux", True),
    (SELECTOR_SET, "other", "host-a", "linux", True),
    (SELECTOR_SET, "third", "host-a", "linux", False),
    (SELECTOR_SET, "other", "host-b", "linux", False),
    (" YeS ", "other", "host-b", "other", True),
    ("0", "worker", "host-a", "linux", False),
    (None, "worker", "host-a", "linux", False),
])
def test_managed_cli_cycle_selects_exact_caller(
    q: TaskQueue, api: TestClient, monkeypatch: pytest.MonkeyPatch,
    setting: str | None, registration: str, machine: str, env: str, allowed: bool,
) -> None:
    publish(q, pool(registration, machine, env))
    requested = task(q)
    if setting is None:
        monkeypatch.delenv(SETTING, raising=False)
    else:
        monkeypatch.setenv(SETTING, setting)
    monkeypatch.setattr(identity, "resolve_machine", lambda: machine)
    monkeypatch.setattr("agent_dispatch.supervise_cli.configure_file_logging", lambda *_: None)
    requests: list[httpx.Request] = []
    monkeypatch.setattr(cli, "_client", lambda *a, **kw: controls.connected(api, requests))
    attempts: list[tuple[object, object]] = []

    def reserve(_client: object, row: dict[str, object], _owner: str, **kwargs: object) -> dict[str, bool]:
        attempts.append((row["id"], kwargs["instance"]))
        return {"reserved": False}

    monkeypatch.setattr(ordinary_bridge, "reserve_for_backend", reserve)
    args = cli.build_parser().parse_args([
        "supervise", "--once", "--all-repos", "--supervisor-id", registration,
        "--registration-machine", machine, "--registration-env", env,
    ])
    assert args.func(args) == 0
    assert attempts == ([(requested.id, (registration, machine, env))] if allowed else [])
    assert any(request.url.path == "/tasks" for request in requests)


@pytest.mark.parametrize("setting", [
    SELECTOR[:-1], "[" + SELECTOR + "]", '{"registration_id":"worker","machine":"host-a"}',
    SELECTOR.replace('"linux"', 'null'), SELECTOR.replace('"linux"', '" "'),
    SELECTOR.replace('"env":', '"extra":true,"env":'),
    SELECTOR.replace('"env":', '"env":"other","env":'),
    SELECTOR.replace('"env":', '"env":"linux","env":'),
    '{"instances":[' + SELECTOR + ',null]}',
    '{"instances":[' + SELECTOR + ',' + SELECTOR + ']}',
    '{"instances":[' + SELECTOR + '],"extra":true}',
    '{"instances":[]}',
])
def test_invalid_selector_never_authorizes(monkeypatch: pytest.MonkeyPatch, setting: str) -> None:
    monkeypatch.setenv(SETTING, setting)
    assert not instance_allocation_enabled(("worker", "host-a", "linux"), broad_enabled=True)


def test_selector_does_not_authorize_unqualified_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SETTING, SELECTOR)
    assert not instance_allocation_enabled(None, broad_enabled=True)
