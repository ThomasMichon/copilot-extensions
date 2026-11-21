"""Original active-operation adoption through actual CLI and negotiated readers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import asyncio
from typing import TYPE_CHECKING, Any
from types import SimpleNamespace

import pytest

from agent_pull_requests import retained_review_adoption, watch_replay_store
from agent_pull_requests.terminal_retention import TerminalAdmissionClosed
from agent_pull_requests.watch_replay_store import ReplayStoreInvalid
from agent_pull_requests.workspace_operations import NativeWorkspaceClient, WorkspaceUnavailable
from agent_pull_requests.workspace_retained_review import FIELD, SELECTION, contract
from agent_pull_requests.workspace_terminal_read import OPERATIONS, serve, supports
from agent_pull_requests_service import terminal_transition
from agent_pull_requests_service.config import HostConfig, load_config

if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration
    from services.pr.tests.test_recorded_terminal_cache import recorded
    from services.pr.tests.test_retained_live_cache import generation, reader
    from services.pr.tests.test_terminal_transition import cache_bytes, command
else:
    from test_host import Configuration, configuration as configuration
    from test_recorded_terminal_cache import recorded
    from test_retained_live_cache import generation, reader
    from test_terminal_transition import cache_bytes, command

Document = dict[str, Any]


def original(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, *, native: bool = True,
    body: str = "Original uncertain comment.", receipt_bytes: int = 4096,
) -> tuple[Path, Document, HostConfig, Document, Document]:
    path, value, baseline, generations = recorded(configuration, monkeypatch, native_targets=native)
    owner = baseline.store()
    if receipt_bytes != 4096:
        owner._review_transaction(
            lambda document: document["capacities"].update(receipt_bytes=receipt_bytes),
            expires_at=int(time.time()) + 60,
        )
    generation = generations[-1]
    intent = {
        "schema": "agent-pull-requests.review-intent", "version": 1,
        "operation_id": "original-unknown", "scope": generation["scope"],
        "generation_id": generation["id"], "source_id": "a" * 64,
        "revision": {name: generation["revision"][name] for name in (
            "head_sha", "common_sha", "iteration",
        )},
        "actions": [
            {"id": "comment", "kind": "comment", "body": body},
            {"id": "vote", "kind": "verdict", "verdict": "APPROVED"},
        ],
    }
    expires = int(time.time()) + 60
    owner.admit_review(intent, expires_at=expires)
    attempt = owner.begin_review_action(
        intent["operation_id"], intent["scope"], "comment", expires_at=expires,
    )
    ids: Document = {"thread_id": 100, "comment_id": 200}
    if receipt_bytes > 4096:
        ids.update({f"native-{index}": "\U0001f600" * 200 for index in range(6)})
    owner.record_review_receipt(
        intent["operation_id"], intent["scope"], "comment", attempt["action"]["attempt_id"],
        {"origin": "response", "platform_ids": ids, "proof": None},
        confirm=False, expires_at=expires,
    )
    operation = owner.inspect_review(intent["operation_id"], intent["scope"], expires_at=expires)
    value["workspaces"]["retained_active_reviews"] = True
    path.write_text(json.dumps(value))
    return path, value, baseline, intent, operation


@pytest.mark.parametrize("native", [False, True])
def test_actual_cli_and_fresh_reader_preserve_original_inspection_and_permanent_fence(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, native: bool,
) -> None:
    path, value, baseline, intent, operation = original(configuration, monkeypatch, native=native)
    before = json.loads(baseline.store().path.read_bytes())
    objects = cache_bytes(baseline)
    result = command(path)
    assert result.returncode == 0, result.stderr
    config = load_config(path)
    owner = config.store()
    document = json.loads(owner.path.read_bytes())
    imported = {**operation, "recovery_only": True}
    assert document["reviews"] == [imported]
    assert imported["reserved_bytes"] == operation["reserved_bytes"]
    for field in ("replay", "conversations", "generation_associations"):
        assert document[field] == before[field]
    assert cache_bytes(config) == objects
    assert command(path).returncode == 0
    assert json.loads(owner.path.read_bytes()) == document
    program = """
import json, sys, time
from agent_pull_requests_service.config import load_config
from agent_pull_requests.workspace_operations import NativeWorkspaceClient
from agent_pull_requests.workspace_tools import WorkspaceTools
import asyncio
c = load_config(sys.argv[1])
w = c.workspaces.operations(c.store(), lambda: None, 60)[("caller-a", "repository-a")]
p = json.loads(sys.argv[2])
n = NativeWorkspaceClient(w, checkpoint=lambda: None, expires_at=lambda: int(time.time()) + 60)
async def run():
    tools = WorkspaceTools(n)
    caps = await tools.capabilities()
    assert "inspect" in caps["operations"]
    assert not {"submit", "allocate", "receipt", "observe-comment"} & set(caps["operations"])
    return await tools.invoke("inspect", p)
print(json.dumps(asyncio.run(run())))
"""
    restarted = subprocess.run(
        [sys.executable, "-I", "-c", program, str(path), json.dumps({
            "number": intent["scope"]["number"], "operation_id": intent["operation_id"],
        })],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(restarted.stdout)["result"] == imported
    options: Document = {"expires_at": int(time.time()) + 60}
    for _ in range(2):
        with pytest.raises(TerminalAdmissionClosed, match="permanently recovery-only"):
            owner.admit_review(intent, **options)
        with pytest.raises(TerminalAdmissionClosed, match="permanently recovery-only"):
            owner.begin_review_action(intent["operation_id"], intent["scope"], "vote", **options)
    assert owner.inspect_retained_review(intent["operation_id"], intent["scope"], **options) == imported
    assert json.loads(owner.path.read_bytes()) == document
    value["workspaces"].pop("retained_active_reviews")
    path.write_text(json.dumps(value))
    with pytest.raises(ReplayStoreInvalid):
        reader(load_config(path)).validate_storage(checkpoint=lambda: None, **options)


def test_inspection_is_explicit_scoped_read_only_and_never_uses_observer_credentials(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _, _, intent, operation = original(configuration, monkeypatch)
    assert command(path).returncode == 0
    config = load_config(path)
    workspace = reader(config)
    options: Document = {"expires_at": int(time.time()) + 60, "checkpoint": lambda: None}

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("inspection entered acquisition, admission, sender or a receipt mutation")

    for name in ("admit_review", "begin_review_action", "record_review_receipt"):
        monkeypatch.setattr(workspace.store, name, forbidden)
    monkeypatch.setattr(workspace.read_owner, "read", forbidden)
    monkeypatch.setattr(type(workspace.cache), "lookup", forbidden)
    client = NativeWorkspaceClient(
        workspace, checkpoint=lambda: None, expires_at=lambda: int(time.time()) + 60,
    )
    payload = {"number": intent["scope"]["number"], "operation_id": intent["operation_id"]}
    before = workspace.store.path.read_bytes()
    with pytest.raises(WorkspaceUnavailable):
        client.exchange("inspect", payload)
    capabilities = client.exchange("capabilities", {})
    assert supports(capabilities) and capabilities["operations"] == list(OPERATIONS)
    assert capabilities[FIELD] == contract() and capabilities[FIELD]["reconciliation"] is False
    with pytest.raises(WorkspaceUnavailable):
        serve(workspace, "inspect", payload, selection=capabilities["terminal_read_contract"]["protocol"], **options)
    assert serve(workspace, "inspect", payload, selection=SELECTION, **options)["result"] == {
        **operation, "recovery_only": True,
    }
    with pytest.raises(ValueError):
        client.exchange("inspect", {**payload, "number": payload["number"] + 1})
    for name in ("submit", "allocate", "receipt", "observe-comment"):
        with pytest.raises(WorkspaceUnavailable):
            serve(workspace, name, payload, selection=SELECTION, **options)
    assert workspace.store.path.read_bytes() == before


@pytest.mark.parametrize("failure", ["scope", "capacity", "old-core", "lifecycle", "expiry"])
def test_refusal_precedes_first_commit_and_keeps_original_reader(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    path, value, baseline, intent, operation = original(configuration, monkeypatch)
    owner = baseline.store()
    if failure == "scope":
        value["workspaces"]["bindings"][0]["scope"]["principal_id"] = "another-reviewer"
        path.write_text(json.dumps(value))
    elif failure == "capacity":
        document = json.loads(owner.path.read_bytes())
        monkeypatch.setattr(
            watch_replay_store, "MAX_OWNER_BYTES",
            sum(document["capacities"].values()) + 4096 + 4 * 1024 * 1024,
        )
        assert owner.inspect_review(
            intent["operation_id"], intent["scope"], expires_at=int(time.time()) + 60,
        ) == operation
    elif failure == "old-core":
        monkeypatch.setattr(retained_review_adoption, "contract", lambda: {})
    elif failure == "expiry":
        clock = [0.0]
        stage = retained_review_adoption.stage

        def expired(*args: Any, **kwargs: Any) -> None:
            stage(*args, **kwargs)
            clock[0] = 300.0
        monkeypatch.setattr(terminal_transition, "time", SimpleNamespace(
            time=time.time, monotonic=lambda: clock[0],
        ))
        monkeypatch.setattr(retained_review_adoption, "stage", expired)
    elif failure == "lifecycle":
        from single_instance_lease import SingleInstance
        with SingleInstance(owner.root, service="agent-pull-requests-watch"):
            before = owner.path.read_bytes()
            with pytest.raises(Exception):
                terminal_transition.adopt(load_config(path))
            assert owner.path.read_bytes() == before
        return
    before, objects = owner.path.read_bytes(), cache_bytes(baseline)
    with pytest.raises((ValueError, RuntimeError)):
        terminal_transition.adopt(load_config(path))
    assert owner.path.read_bytes() == before
    assert cache_bytes(baseline) == objects
    monkeypatch.undo()
    assert owner.inspect_review(
        intent["operation_id"], intent["scope"], expires_at=int(time.time()) + 60,
    ) == operation


@pytest.mark.parametrize("uncertain", [False, True])
@pytest.mark.parametrize("boundary", [
    "terminal_retention_version", "terminal_recovery_version", "terminal_cache_retirement_version",
])
def test_original_attempt_survives_partial_or_uncertain_adoption_and_actual_cli_retry(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
    uncertain: bool, boundary: str,
) -> None:
    path, _, baseline, intent, operation = original(configuration, monkeypatch)
    write = watch_replay_store._atomic_write_json
    objects = cache_bytes(baseline)
    before = json.loads(baseline.store().path.read_bytes())

    def interrupted(destination: Path, value: Document) -> None:
        if boundary in value:
            if uncertain:
                write(destination, value)
            raise OSError("synthetic original operation adoption interruption")
        write(destination, value)

    with monkeypatch.context() as patch:
        patch.setattr(watch_replay_store, "_atomic_write_json", interrupted)
        with pytest.raises(OSError):
            terminal_transition.adopt(load_config(path))
    owner = load_config(path).store()
    after_failure = json.loads(owner.path.read_bytes())
    assert owner.inspect_retained_review(
        intent["operation_id"], intent["scope"], expires_at=int(time.time()) + 60,
    ) == {**operation, **({"recovery_only": True} if "retained_active_review_version" in after_failure else {})}
    result = command(path)
    assert result.returncode == 0, result.stderr
    after = json.loads(owner.path.read_bytes())
    assert after["reviews"] == [{**operation, "recovery_only": True}]
    for field in ("replay", "conversations", "generation_associations"):
        assert after[field] == before[field]
    assert cache_bytes(load_config(path)) == objects


def test_reopen_does_not_revive_original_unattempted_actions(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests.native_read import NativeReadHandler

    path, _, _, intent, operation = original(configuration, monkeypatch)
    assert command(path).returncode == 0
    config = load_config(path)
    workspace = reader(config)
    options: Document = {"expires_at": int(time.time()) + 60, "checkpoint": lambda: None}
    current = intent["generation_id"]
    for index, terminal in ((2, True), (3, False)):
        selected = generation(index, terminal=terminal)
        monkeypatch.setattr(NativeReadHandler, "read", lambda *_args, **_kwargs: {
            "generation": selected, "acquisition": {"source": "synthetic-owner"},
        })
        result = workspace.execute("load", {
            "number": intent["scope"]["number"], "expected_current": current, "budget_seconds": 30,
        }, **options)["result"]
        if not terminal:
            assert result["conversation"]["cursor"]["epoch"] == 2
        with pytest.raises(TerminalAdmissionClosed, match="permanently recovery-only"):
            workspace.store.begin_review_action(
                intent["operation_id"], intent["scope"], "vote", **options,
            )
        client = NativeWorkspaceClient(
            reader(load_config(path)), checkpoint=lambda: None, expires_at=lambda: int(time.time()) + 60,
        )
        client.exchange("capabilities", {})
        assert client.exchange("inspect", {
            "number": intent["scope"]["number"], "operation_id": intent["operation_id"],
        })["result"] == {**operation, "recovery_only": True}


def test_async_client_negotiates_inspection_separately_and_refuses_skew(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests.watch_replay_binding import ReplayBinding
    from agent_pull_requests.workspace_client import WorkspaceClient, WorkspaceClientError
    from fleet_manager.gateway_service_caller import ServiceResponse

    path, _, _, intent, operation = original(configuration, monkeypatch)
    assert command(path).returncode == 0
    workspace = reader(load_config(path))
    binding = ReplayBinding(
        fleet="fleet-a", gateway="gateway-a", slot="pr-watch",
        namespace="installation-a", principal="caller-a",
        target="repository-a", repo="example/repository", consumer="dispatch-a", account="account-a",
    )
    requests: list[Document] = []
    declaration: Document | None = contract()

    class Caller:
        async def request(self, method: str, target: str, **kwargs: Any) -> ServiceResponse:
            body = json.loads(kwargs["body"])
            requests.append(body)
            result = serve(
                workspace, target.rsplit("/", 1)[-1], body["request"],
                expires_at=int(time.time()) + 60, checkpoint=lambda: None,
                selection=body.get("workspace_contract"),
            )
            if target.endswith("/capabilities"):
                if declaration is None:
                    result.pop(FIELD)
                else:
                    result[FIELD] = declaration
            return ServiceResponse(
                200, (("content-type", "application/json"),),
                json.dumps({**result, "binding": binding.document()}).encode(),
            )

    async def scenario() -> None:
        nonlocal declaration
        client = WorkspaceClient(Caller(), route_prefix="/api", binding=binding, scope=workspace.scope)
        await client.capabilities()
        payload = {"number": intent["scope"]["number"], "operation_id": intent["operation_id"]}
        assert (await client.exchange("inspect", payload))["result"] == {
            **operation, "recovery_only": True,
        }
        assert requests[-1]["workspace_contract"] == SELECTION
        for declaration in (None, {**contract(), "reconciliation": True}):
            await client.capabilities()
            count = len(requests)
            with pytest.raises(WorkspaceClientError, match="capability_required"):
                await client.exchange("inspect", payload)
            assert len(requests) == count
    asyncio.run(scenario())


def test_actual_gateway_preserves_large_original_body_and_nondefault_receipt_reservation(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_service
    from agent_pull_requests.watch_replay_binding import ReplayBinding
    from agent_pull_requests.workspace_client import WorkspaceClient
    from agent_pull_requests.workspace_retained_review import RESPONSE_BYTES
    from fleet_manager.gateway_service_channel import BODY_BYTES
    from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest
    from agent_pull_requests_service import hosting

    path, _, _, intent, operation = original(
        configuration, monkeypatch, body="x" * 40000, receipt_bytes=16384,
    )
    assert len(json.dumps(operation["actions"][0]["receipt"]).encode()) > 4096
    assert command(path).returncode == 0
    config = load_config(path)
    workspace = reader(config)
    connectors: list[Any] = []

    class Connector:
        def __init__(self, **kwargs: Any) -> None:
            self.handler = kwargs["handler"]
            self.connected = asyncio.Event()
            connectors.append(self)

        async def run(self, stop: asyncio.Event) -> None:
            self.connected.set()
            await stop.wait()

    async def access() -> str:
        pytest.fail("the fixture connector must not acquire observation credentials")
    monkeypatch.setattr(watch_service, "ServiceConnector", Connector)
    monkeypatch.setattr(hosting, "load_service_credentials", lambda **_kwargs: SimpleNamespace(
        proof_key=object(), access_token=access,
    ))
    selected = config.bindings[0]
    binding = ReplayBinding(
        "fleet-a", "gateway-a", "pr-watch", config.namespace, selected.principal, selected.target,
        selected.scope.consumer, selected.scope.account, selected.scope.repo,
    )
    sizes: list[tuple[int, int]] = []

    class Caller:
        async def request(self, method: str, target: str, **kwargs: Any) -> Any:
            assert len(kwargs["body"]) <= BODY_BYTES
            handler = connectors[0].handler
            response = await handler.handle(ServiceRequest(
                CallerContext("fleet-a", "gateway-a", "pr-watch", "caller-a", int(time.time()) + 60),
                method, target, tuple((name.lower(), value) for name, value in kwargs["request_headers"]),
                kwargs["body"],
                response_limit=handler.response_limit,
            ))
            assert response.status == 200, response.body
            assert len(response.body) <= kwargs["response_limit"]
            if target.endswith("/inspect"):
                sizes.append((len(response.body), kwargs["response_limit"]))
            return response

    async def scenario() -> None:
        stop = asyncio.Event()
        owner = asyncio.create_task(hosting.run(config, stop=stop))
        try:
            for _ in range(1000):
                if connectors and connectors[0].connected.is_set():
                    break
                if owner.done():
                    await owner
                await asyncio.sleep(.001)
            assert connectors and connectors[0].connected.is_set()
            assert connectors[0].handler.response_limit == RESPONSE_BYTES
            payload = {"number": intent["scope"]["number"], "operation_id": intent["operation_id"]}
            native = NativeWorkspaceClient(
                workspace, checkpoint=lambda: None, expires_at=lambda: int(time.time()) + 60,
            )
            native.exchange("capabilities", {})
            expected = native.exchange("inspect", payload)
            client = WorkspaceClient(Caller(), route_prefix="", binding=binding, scope=workspace.scope)
            capabilities = await client.capabilities()
            assert capabilities[FIELD]["max_response_bytes"] == RESPONSE_BYTES
            assert await client.exchange("inspect", payload) == expected
            assert expected["result"] == {**operation, "recovery_only": True}
            assert sizes and BODY_BYTES < sizes[-1][0] <= sizes[-1][1] == RESPONSE_BYTES
        finally:
            stop.set()
            await asyncio.wait_for(owner, 5)
    before = workspace.store.path.read_bytes()
    asyncio.run(scenario())
    assert workspace.store.path.read_bytes() == before
