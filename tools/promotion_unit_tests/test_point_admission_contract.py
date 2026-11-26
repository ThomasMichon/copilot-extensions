"""Actor-free required admission controls; actual HTTP/SQLite stays opt-in."""

from __future__ import annotations

import ast
import sys
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING or __package__:
    from .test_point_service_binding import SOURCE, api as api
else:
    from test_point_service_binding import SOURCE, api as api


class _Task:
    def __init__(self, operation: Any) -> None:
        self.operation = operation
        self.callbacks: list[Any] = []
        self.finished = False
        self.result: Any = None

    def add_done_callback(self, callback: Any) -> None:
        self.callbacks.append(callback)

    def __await__(self) -> Any:
        while not self.finished:
            try:
                pending = self.operation.send(None)
            except StopIteration as result:
                self.result = result.value
                self.finished = True
                for callback in self.callbacks:
                    callback(self)
            else:
                yield pending
        return self.result


def _hints(api: SimpleNamespace) -> Any:
    hints = sys.modules["agent_index.indexing.point_hints"]
    source, family, account, binding = api.runtime.point_scope
    return hints.PointChangeHints(
        source=source, canonical_family=family, account_id=account, binding=binding,
        targets=(hints.WorkItemRevisionTarget("42", 7, ("event",)),),
    )


def _functions(path: str, parent: str, names: tuple[str, ...]) -> ast.Module:
    tree = ast.parse((SOURCE / path).read_bytes())
    owner = next(node for node in tree.body if getattr(node, "name", None) == parent)
    assert isinstance(owner, (ast.ClassDef, ast.FunctionDef))
    functions = [node for node in owner.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert len(functions) == len(names)
    for node in functions:
        node.decorator_list = []
    body: list[ast.stmt] = list(functions)
    return ast.Module(body=body, type_ignores=[])


def test_required_point_validators_reject_coercion_and_legacy_hints(api: SimpleNamespace) -> None:
    hints = _hints(api)
    namespace = {"PointChangeHints": type(hints)}
    exec(compile(_functions(
        "reindex_intake.py", "HintedReindexRequest", ("check_point_requirement", "check_scope"),
    ), str(SOURCE / "reindex_intake.py"), "exec"), namespace)
    validate = namespace["check_point_requirement"]
    assert validate(None, 1) == 1
    invalid: tuple[object, ...] = (None, True, False, "1", 1.0, 0, 2, [], {})
    for value in invalid:
        with pytest.raises(ValueError):
            validate(None, value)
    body = SimpleNamespace(
        source=hints.source, full=False, change_hints=hints, required_point_service_version=1,
    )
    assert namespace["check_scope"](body) is body
    legacy = sys.modules["agent_index.indexing.change_hints"].ChangeHints(
        source=hints.source, canonical_family="family", account_id="account",
    )
    body.change_hints = legacy
    with pytest.raises(ValueError, match="typed v2"):
        namespace["check_scope"](body)
    body.required_point_service_version = None
    assert namespace["check_scope"](body) is body


@pytest.mark.parametrize("selection", ["paused", "custom", "scope", "failed", "draining", "stopped"])
def test_required_admission_refuses_before_queue_callback(
    api: SimpleNamespace, selection: str,
) -> None:
    service = api.service(runtime_context=lambda: api.context(
        api.runtime if selection == "custom" else api.qualified,
    ))
    api.finish(service.start(legacy_data_dir=Path("legacy")))
    if selection != "paused":
        api.finish(service.activate())
    hints = _hints(api)
    if selection == "scope":
        hints = replace(hints, account_id="different")
    elif selection == "failed":
        api.state.dispatch_failure = True
        api.finish(service.run_pending())
    elif selection == "draining":
        service.pause()
    elif selection == "stopped":
        api.finish(service.stop())

    async def enqueue(_: Any) -> Any:
        pytest.fail("refused selection cannot start queue effects")

    with pytest.raises(api.module.PointAdmissionRefused):
        api.finish(service.enqueue_required(hints, enqueue))
    assert not service._intakes
    if selection != "stopped":
        api.finish(service.stop())


@pytest.fixture
def point_client(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    transport = ModuleType("httpx")
    setattr(transport, "HTTPError", OSError)
    monkeypatch.setitem(sys.modules, "httpx", transport)
    api.load("indexing.change_hints_contract")
    client = api.load("client").AgentIndexClient(
        "http://fixture",
        installation_id="",
        instance_token="selected",
    )
    hints = _hints(api)
    service = api.service(runtime_context=lambda: api.context(api.qualified))
    api.finish(service.start(legacy_data_dir=Path("legacy")))
    api.finish(service.activate())
    health = {
        "installationId": "",
        "instanceToken": "selected",
        "point_service_contract": service.contract(),
    }
    api.finish(service.stop())
    receipt = {
        "accepted": True,
        "installationId": "",
        "instanceToken": "selected",
        "point_service_admission": {
            "id": "agent-index.point-service",
            "version": 1,
            "scope_fingerprint": api.contract.point_scope_fingerprint(hints.scope),
        },
        "task": {
            "source": hints.source,
            "full": False,
            "change_hints": hints.to_dict(),
        },
    }
    state = SimpleNamespace(health=health, receipt=receipt, failure=None, calls=[])

    def request(method: str, path: str, **kwargs: Any) -> Any:
        state.calls.append((method, path, kwargs))
        if method == "GET":
            return state.health
        if state.failure is not None:
            raise state.failure
        return state.receipt

    monkeypatch.setattr(client, "_request", request)
    return SimpleNamespace(
        client=client,
        hints=hints,
        state=state,
        client_type=type(client),
        contract=api.contract,
    )


def test_point_client_requires_bound_capability_and_receipts_complete_union(
    point_client: SimpleNamespace,
) -> None:
    p = point_client
    extra = replace(p.hints.targets[0], revision=8)
    stored = replace(p.hints, targets=(*p.hints.targets, extra))
    p.state.receipt["task"]["change_hints"] = stored.to_dict()
    assert p.client.reindex_points(p.hints) is p.state.receipt
    assert p.state.calls == [
        ("GET", "/health", {}),
        (
            "POST",
            "/reindex/hints",
            {
                "json": {
                    "source": p.hints.source,
                    "full": False,
                    "change_hints": p.hints.to_dict(),
                    "required_point_service_version": 1,
                }
            },
        ),
    ]
    assert p.client.installation_id == "" and p.client.instance_token == "selected"


def test_point_client_refuses_unsupported_peer_scope_and_admission_before_post(
    point_client: SimpleNamespace,
) -> None:
    p = point_client
    original = deepcopy(p.state.health)
    for key, value in (
        ("required_admission_versions", None),
        ("required_admission_versions", [True]),
        ("required_admission_versions", "1"),
        ("version", True),
        ("paired", False),
        ("scope_fingerprint", "different"),
        ("state", "paused"),
        ("execution_admitted", False),
        ("coverage", "complete-discussion"),
    ):
        p.state.health = deepcopy(original)
        p.state.health["point_service_contract"][key] = value
        p.state.calls.clear()
        with pytest.raises(RuntimeError):
            p.client.reindex_points(p.hints)
        assert [call[:2] for call in p.state.calls] == [("GET", "/health")]
    for key, value in (("installationId", "other"), ("instanceToken", "other")):
        p.state.health = deepcopy(original)
        p.state.health[key] = value
        p.state.calls.clear()
        with pytest.raises(RuntimeError, match="selected installation"):
            p.client.reindex_points(p.hints)
        assert len(p.state.calls) == 1
    p.state.health = original
    p.client.instance_token = None
    p.state.calls.clear()
    with pytest.raises(RuntimeError, match="nonempty instance"):
        p.client.reindex_points(p.hints)
    assert len(p.state.calls) == 1 and p.client.instance_token is None


def test_point_client_malformed_receipts_are_unknown_without_replay(
    point_client: SimpleNamespace,
) -> None:
    p = point_client
    original = deepcopy(p.state.receipt)
    bad = [{}, {**original, "accepted": 1}]
    for key, value in (
        ("installationId", "other"),
        ("instanceToken", "other"),
        ("point_service_admission", None),
        ("task", None),
    ):
        bad.append({**original, key: value})
    for admission_key, admission_value in (
        ("version", True),
        ("scope_fingerprint", "other"),
        ("id", "other"),
    ):
        receipt = deepcopy(original)
        receipt["point_service_admission"][admission_key] = admission_value
        bad.append(receipt)
    for task_key, task_value in (
        ("source", "other"),
        ("full", 0),
        ("change_hints", {}),
        (
            "change_hints",
            replace(
                p.hints,
                targets=(replace(p.hints.targets[0], event_ids=()),),
            ).to_dict(),
        ),
        (
            "change_hints",
            replace(
                p.hints,
                targets=(replace(p.hints.targets[0], revision=8),),
            ).to_dict(),
        ),
        ("change_hints", replace(p.hints, account_id="other").to_dict()),
    ):
        receipt = deepcopy(original)
        receipt["task"][task_key] = task_value
        bad.append(receipt)
    for receipt in bad:
        p.state.receipt = receipt
        p.state.calls.clear()
        with pytest.raises(RuntimeError, match="acceptance outcome unknown"):
            p.client.reindex_points(p.hints)
        assert [call[:2] for call in p.state.calls] == [
            ("GET", "/health"),
            ("POST", "/reindex/hints"),
        ]
    p.state.receipt = {"accepted": False, "error": "unavailable"}
    p.state.calls.clear()
    assert p.client.reindex_points(p.hints) == p.state.receipt
    assert len(p.state.calls) == 2


def test_point_client_lost_returns_preserve_unknown_acceptance_and_old_api(
    point_client: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = point_client
    for failure in (OSError("lost return"), ValueError("invalid response JSON")):
        p.state.calls.clear()
        p.state.failure = failure
        with pytest.raises(RuntimeError, match="acceptance outcome unknown") as outcome:
            p.client.reindex_points(p.hints)
        assert outcome.value.__cause__ is failure and len(p.state.calls) == 2
    p.state.failure = None
    p.state.calls.clear()
    p.client.reindex(source="legacy")
    assert p.state.calls == [
        ("POST", "/reindex", {"json": {"source": "legacy", "full": False}})
    ]
    p.state.calls.clear()
    with pytest.raises(ValueError, match="PointChangeHints"):
        p.client.reindex_points(object())
    assert not p.state.calls
    readonly = p.client_type(
        "http://fixture",
        installation_id="",
        instance_token="selected",
        read_headers={},
    )
    monkeypatch.setattr(readonly, "health", lambda: p.state.health)
    with pytest.raises(RuntimeError, match="read clients"):
        readonly.reindex_points(p.hints)


def test_required_admission_joins_queue_before_closing_selected_resources(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api.module, "_CleanupTask", _Task)
    service = api.service(runtime_context=lambda: api.context(api.qualified))
    api.finish(service.start(legacy_data_dir=Path("legacy")))
    api.finish(service.activate())
    api.state.blocked = True

    async def enqueue(store: Any) -> Any:
        assert store is service.store
        result = await api.module.asyncio.get_running_loop().run_in_executor(
            None, lambda: api.result,
        )
        api.events.append("enqueued")
        return result

    posting = service.enqueue_required(_hints(api), enqueue)
    assert posting.send(None) is None
    pending = api.state.pending
    stopping = service.stop()
    assert stopping.send(None) is None
    assert "close" not in api.events and len(service._intakes) == 1
    pending.ready = True
    api.finish(stopping)
    assert api.finish(posting) is api.result
    assert not service._intakes
    assert api.events[-3:] == ["enqueued", "consumer:close", "close"]


def test_required_intake_route_receipts_selected_peer_scope_and_typed_union(
    api: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api.module, "_CleanupTask", _Task)
    hints = _hints(api)
    service = api.service(runtime_context=lambda: api.context(api.qualified))
    api.finish(service.start(legacy_data_dir=Path("legacy")))
    api.finish(service.activate())
    body = SimpleNamespace(required_point_service_version=1, change_hints=hints)
    intake = ModuleType("agent_index.reindex_intake")
    effects: list[Any] = []

    async def read(_: Any) -> Any:
        return body

    async def enqueue(store: Any, accepted: Any, notify: Any) -> Any:
        effects.append(store)
        assert accepted is body
        notify()
        return SimpleNamespace(to_dict=lambda: {
            "source": hints.source, "full": False, "change_hints": hints.to_dict(),
        })

    setattr(intake, "read_hinted_request", read)
    setattr(intake, "enqueue_hinted_request", enqueue)
    monkeypatch.setitem(sys.modules, intake.__name__, intake)

    class Refused(Exception):
        def __init__(self, **_: Any) -> None:
            pass

    state = SimpleNamespace(
        installation_id="", instance_token="selected", promoted=True,
        drain_gate=SimpleNamespace(draining=False), task_store=object(),
        task_runner=SimpleNamespace(point_service=service, notify=lambda: effects.append("notify")),
    )
    namespace: dict[str, Any] = {
        "__package__": "agent_index", "Any": Any, "Request": object,
        "HTTPException": Refused, "INSTALLATION_HEADER": "installation",
        "INSTANCE_HEADER": "instance", "os": SimpleNamespace(getpid=lambda: 1),
        "track_read": lambda _: nullcontext(), "_missing_indexing_dependencies": lambda: [],
        "intake_contract": lambda: {"execution_versions": [1]},
    }
    exec(compile(_functions(
        "server.py", "build_app", ("service_identity", "authorize_control", "reindex_hints"),
    ), str(SOURCE / "server.py"), "exec"), namespace)
    request = SimpleNamespace(
        app=SimpleNamespace(state=state), headers={"installation": "", "instance": "selected"},
    )
    receipt = api.finish(namespace["reindex_hints"](request))
    assert receipt["point_service_admission"] == {
        "id": "agent-index.point-service", "version": 1,
        "scope_fingerprint": api.contract.point_scope_fingerprint(hints.scope),
    }
    assert receipt["accepted"] and receipt["instanceToken"] == "selected"
    assert receipt["installationId"] == "" and receipt["task"]["change_hints"] == hints.to_dict()
    assert effects == [service.store, "notify"] and effects[0] is not state.task_store
    for headers in ({}, {"instance": "different"}, {"installation": "wrong", "instance": "selected"}):
        request.headers = headers
        with pytest.raises(Refused):
            api.finish(namespace["reindex_hints"](request))
    assert effects == [service.store, "notify"]
    api.finish(service.stop())
