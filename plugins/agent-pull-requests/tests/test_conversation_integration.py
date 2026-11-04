"""Actual CLI parser, SDK, authenticated TLS/channel and shared durable journal."""

import asyncio
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from test_workspace_integration import environment as environment, integration as integration
from test_conversation_journal import observation

from agent_pull_requests.attention import Cursor
from agent_pull_requests.conversation_store import ConversationStore
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.workspace_client import WorkspaceClient
from agent_pull_requests.workspace_operations import conversation_key
from agent_pull_requests.workspace_tools import WorkspaceTools, WorkspaceToolUnavailable
from agent_pull_requests import workspace_cli

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def test_terminal_journal_does_not_erase_an_unknown_effect_or_cached_generation(
    environment, integration
):
    store, selected, generation, _, _, _ = environment
    _, _, native, _, _, _ = integration
    expires = int(time.time()) + 600
    store.admit_review(selected, expires_at=expires)
    store.begin_review_action(
        selected["operation_id"],
        selected["scope"],
        "finding",
        expires_at=expires,
    )
    reviews = json.loads(store.path.read_bytes())["reviews"]
    journal = ConversationStore(store)
    journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    key = conversation_key(selected["scope"], store.namespace)
    journal.observe(observation(key=key, state="merged"), expected=None, expires_at=expires)
    assert json.loads(store.path.read_bytes())["reviews"] == reviews
    assert (
        native.operations.cache.lookup(selected["scope"], generation_id=generation["id"])[
            "generation"
        ]
        == generation
    )


def test_event_frontdoor_rejects_foreign_labels_and_never_uses_another_pr(integration):
    gateway, _, native, _, _, _ = integration
    expires = int(time.time()) + 600
    journal = ConversationStore(native.operations.store)
    journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    native.operations.conversations = True
    key = conversation_key(
        {**native.operations.scope, "number": 42}, native.operations.store.namespace
    )
    journal.observe(observation(key=key), expected=None, expires_at=expires)
    assert (
        native.exchange("events", {"number": 43, "after": None, "limit": 16})["result"]["status"]
        == "unavailable"
    )
    for fields in (
        {"number": 42, "after": None, "limit": 16, "account": "foreign"},
        {"number": 42, "after": {"epoch": 1, "sequence": 0, "namespace": "foreign"}, "limit": 16},
        {"number": 42, "after": None, "limit": True},
    ):
        with pytest.raises(ValueError):
            native.exchange("events", fields)
    asyncio.run(gateway.drain(timeout=2))


def test_new_cli_against_prior_owner_refuses_optional_events_without_sending_them(integration):
    gateway, client, _, _, caller, _ = integration

    async def scenario():
        host = WorkspaceTools(client)
        with pytest.raises(WorkspaceToolUnavailable, match="unsupported"):
            await host.invoke("events", {"number": 42, "after": None, "limit": 16})
        assert not any(path.endswith("/events") for path in caller.calls)
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_unknown_optional_contract_leaves_prior_workspace_operations_available(integration):
    gateway, client, native, _, caller, _ = integration
    expires = int(time.time()) + 600
    ConversationStore(native.operations.store).adopt(
        lifecycle_checkpoint=lambda: None, expires_at=expires
    )
    native.operations.conversations = True
    original = caller.request

    async def altered(*args, **kwargs):
        response = await original(*args, **kwargs)
        if args[1].endswith("/capabilities"):
            value = json.loads(response.body)
            value["conversation_contract"]["protocol"] = "unknown"
            return replace(response, body=json.dumps(value).encode())
        return response

    caller.request = altered

    async def scenario():
        capabilities = await client.capabilities()
        assert (
            "events" not in capabilities["operations"] and "lookup" in capabilities["operations"]
        )
        result = await client.exchange("lookup", {"number": 42})
        assert result["result"]["descriptor"]["scope"]["number"] == 42
        with pytest.raises(WorkspaceToolUnavailable):
            await WorkspaceTools(client).invoke(
                "events", {"number": 42, "after": None, "limit": 16}
            )
        await gateway.drain(timeout=2)

    asyncio.run(scenario())


def test_cli_events_over_real_authenticated_http_preserve_scope_cursor_and_storage(
    environment,
    integration,
    tmp_path,
    monkeypatch,
    capsys,
):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests")
    )
    from gateway_service_transport_fixtures import running
    from test_gateway_service_handler import connect_handler
    from fleet_manager.gateway_service_caller import ServiceCaller

    store, _, _, _, backend, _ = environment
    gateway, _, native, _, _, binding = integration
    expires = int(time.time()) + 600
    journal = ConversationStore(store)
    journal.adopt(lifecycle_checkpoint=lambda: None, expires_at=expires)
    native.operations.conversations = True
    key = conversation_key({**native.operations.scope, "number": 42}, store.namespace)
    now = int(time.time())
    first = observation(key=key, observed_from=now - 3, observed_to=now - 2)
    journal.observe(first, expected=None, expires_at=expires)

    async def scenario():
        async with running(tmp_path) as rig:
            await connect_handler(rig, gateway.handle)

            async def access():
                return rig.access("caller-a", 3)

            async def invoke(request):
                client = WorkspaceClient(
                    ServiceCaller(
                        gateway_origin=rig.origin,
                        gateway_ca=rig.ca,
                        access_token_callback=access,
                        proof_key=rig.h.keys[3].get_op_key("sign"),
                    ),
                    route_prefix="/api",
                    binding=binding,
                    scope=native.operations.scope,
                )
                monkeypatch.setattr(
                    sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode()))
                )
                code = await asyncio.to_thread(
                    workspace_cli.main,
                    ["workspace", "events", "--request", "-"],
                    host=WorkspaceTools(client),
                )
                assert code == 0
                return json.loads(capsys.readouterr().out)

            before = store.path.read_bytes()
            initial = await invoke({"number": 42, "after": None, "limit": 16})
            assert initial["result"]["status"] == "snapshot"
            assert initial["result"]["current"]["key"] == first.document()["key"]
            assert store.path.read_bytes() == before
            current = initial["result"]["high_water"]
            journal.observe(
                replace(
                    first,
                    observed_from=now - 1,
                    observed_to=now,
                    sections=(("comments", "2" * 64),),
                ),
                expected=Cursor(**current),
                expires_at=expires,
            )
            after = await invoke({"number": 42, "after": current, "limit": 16})
            assert after["result"]["events"][0]["changed"] == ["comments"]
            assert after["result"]["next"] == {"epoch": 1, "sequence": 2}
            restored = ConversationStore(
                ReplayStore(
                    store.root,
                    store.namespace,
                    service_identity=store.service_identity,
                )
            )
            assert (
                restored.events(key, after=Cursor(**current), limit=16, expires_at=expires)
                == after["result"]
            )
            rig.h.document["callers"] = []
            await rig.update()
            path = "/api/v2/review/project/events"
            headers = await rig.auth(path, "POST")
            before = store.path.read_bytes()
            async with rig.session.post(
                rig.origin + path,
                headers=headers,
                json={
                    "binding": binding.document(),
                    "request": {"number": 42, "after": current, "limit": 16},
                },
            ) as response:
                assert response.status == 403
            assert store.path.read_bytes() == before
            assert not backend.calls and backend.vote is None and not backend.threads
            assert all(not target.requests for target in rig.backends)
            await gateway.drain(timeout=3)

    asyncio.run(scenario())
