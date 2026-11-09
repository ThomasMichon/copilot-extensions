"""Installed standalone process over real Gateway TLS/PoP, synthetic provider inputs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from importlib import import_module
from typing import TYPE_CHECKING, Any, TypedDict, cast

import pytest
from cryptography.hazmat.primitives import serialization

from agent_pull_requests_service.hosting import ready

# The strict service target analyzes canonical sources, not generated bundled copies.
if TYPE_CHECKING:
    from attribution_profile import load_profile
    from comment_preparation import prepare
else:
    from agent_pull_requests_service._attribution.attribution_profile import load_profile
    from agent_pull_requests_service._attribution.comment_preparation import prepare

class ProcessFlags(TypedDict, total=False):
    creationflags: int

_WORKSPACE_NATIVE = """
import argparse,json,pathlib,sys
path=pathlib.Path(sys.argv[1])
operation=sys.argv[2]
with path.with_suffix('.calls').open('a') as calls:
    calls.write(operation+'\\n')
if operation=='capabilities':
    print(json.dumps({
        'schema':'agent-worktrees.review-evidence-capabilities','version':1,
        'providers':['azure-devops'],'evidence_versions':[1],
        'capabilities':['finite_acquisition','renewable_observe_credentials',
            'principal_binding','credential_budget','enclosing_scope_credentials'],
        'effects':'none','submission':False,'source_attachment':False,
    }))
else:
    assert operation=='read'
    parser=argparse.ArgumentParser(allow_abbrev=False)
    for name in ('repo','number','endpoint','expected-principal','credential-command','budget'):
        parser.add_argument('--'+name,required=True)
    parser.add_argument('--contained-worker',action='store_true',required=True)
    args=parser.parse_args(sys.argv[3:])
    value=json.loads(path.read_text())
    assert args.repo=='example/project' and int(args.number)==42
    assert args.endpoint==value['scope']['endpoint']
    assert args.expected_principal==value['scope']['principal_id']
    assert json.loads(args.credential_command)==[sys.executable,'-I','-m','fleet_manager']
    assert float(args.budget)>0
    print(json.dumps(value))
"""

_EMBEDDED = """
import asyncio,sys
from pathlib import Path
from agent_pull_requests_service.config import load_config
from agent_pull_requests_service.hosting import run
async def main():
    stop=asyncio.Event()
    async def input_stop():
        await asyncio.to_thread(sys.stdin.readline)
        stop.set()
    reader=asyncio.create_task(input_stop())
    try:
        await run(load_config(Path(sys.argv[1])),stop=stop)
    finally:
        if not reader.done():
            reader.cancel()
        await asyncio.gather(reader,return_exceptions=True)
asyncio.run(main())
"""

_PREPARATION_NATIVE = """
import json,pathlib,sys
path=pathlib.Path(sys.argv[1])
state=json.loads(path.read_text())
args=sys.argv[2:]
assert args[:2]==['attribution-prepare','--comment-origin']
with path.with_suffix('.calls').open('a') as calls:
    calls.write(json.dumps(args)+'\\n')
if args[2:]==['--capabilities']:
    if not state['supported']:
        raise SystemExit(2)
    print(json.dumps({
        'schema':'agent-worktrees.comment-origin-capabilities','version':1,
        'origin_protocol':'agent-worktrees.comment-origin/v1',
        'capabilities':['explicit_source_root'],'effects':'none',
    }))
else:
    assert args[2:]==state['expected']
    if not state['origin']:
        raise SystemExit(1)
    print(json.dumps({
        'protocol':'agent-worktrees.comment-origin/v1','privacy':'public',
        'origin':{'codename':'foreground-producer'},'effects':'none',
    }))
"""


def _seed_confirmed_comment(path: Path, evidence: dict[str, Any]) -> dict[str, Any]:
    """Seed a synthetic original receipt before any foreground writer exists."""
    from agent_pull_requests.review_workspace import build_generation
    from agent_pull_requests_service.config import load_config

    config = load_config(path)
    assert config.workspaces is not None
    preparation = config.workspaces.preparations[0][2] if config.workspaces.preparations else None
    profile, errors = load_profile(
        preparation.profile_home if preparation else path.parent / "producer-home",
        preparation.source_root if preparation else path.parent / "producer", env={},
    )
    assert not errors
    prepared = prepare(
        "Retained note.", profile, {"domain": "forge", "visibility": "public", "activity": "comment"},
        {"protocol": "agent-worktrees.comment-origin/v1", "privacy": "public",
         "origin": {"codename": "foreground-producer"}, "effects": "none"},
        comment_id="11111111-1111-4111-8111-111111111111",
        write_id="22222222-2222-4222-8222-222222222222",
    )
    evidence["sections"]["discussions"]["data"] = [{
        "id": 100, "status": "active", "comments": [{
            "id": 1, "content": prepared["body"], "commentType": 1, "parentCommentId": 0,
            "author": {"id": evidence["scope"]["principal_id"]},
        }],
    }]
    generation = build_generation(evidence)["generation"]
    selected = {
        "schema": "agent-pull-requests.review-intent", "version": 2,
        "operation_id": "original-confirmed-comment", "scope": generation["scope"],
        "generation_id": generation["id"], "source_id": "a" * 64,
        "revision": {name: generation["revision"][name] for name in ("head_sha", "common_sha", "iteration")},
        "actions": [{
            "id": "retained-finding", "kind": "comment", "body": prepared["body"],
            "preparation": {"identity": prepared["identity"], "body_id": prepared["body_id"]},
        }],
    }
    store = config.store()
    expires_at = int(time.time()) + 60
    if config.workspaces.terminal_mapping_reads:
        from agent_pull_requests.conversation_store import ConversationStore
        from agent_pull_requests.terminal_retention import bind_generation
        from agent_pull_requests.watch_replay import ObservationTarget

        observed = ConversationStore(store).observe_workspace(
            generation, expires_at=expires_at,
            terminal_target=ObservationTarget("account-a", "example/project", 42, frozenset()),
        )
        transaction: Callable[..., Any] = store._review_transaction
        transaction(
            lambda document: bind_generation(document, generation, observed["cursor"]),
            expires_at=expires_at,
        )
    store.admit_review(selected, expires_at=expires_at)
    begun = store.begin_review_action(
        selected["operation_id"], selected["scope"], "retained-finding", expires_at=expires_at,
    )
    store.record_review_receipt(
        selected["operation_id"], selected["scope"], "retained-finding",
        begun["action"]["attempt_id"],
        {"origin": "response", "platform_ids": {"thread_id": 100, "comment_id": 1}, "proof": None},
        expires_at=expires_at, confirm=True,
    )
    if config.workspaces.terminal_mapping_reads:
        ConversationStore(store).observe_workspace(
            generation, expires_at=expires_at,
            comment_mappings=True,
            terminal_target=ObservationTarget("account-a", "example/project", 42, frozenset()),
        )
    return {"number": 42, "operation_id": selected["operation_id"], "action_id": "retained-finding"}


def _compact_terminal_mapping(path: Path, scope: dict[str, Any], cursor: dict[str, int]) -> None:
    from agent_pull_requests.attention import Cursor
    from agent_pull_requests_service.config import load_config
    from single_instance_lease import SingleInstance

    config = load_config(path)
    assert config.workspaces is not None
    with SingleInstance(config.root, service="agent-pull-requests-watch") as lease:
        def current() -> None:
            assert lease.held
        store = config.store()
        operations = config.workspaces.operations(store, current, 60)[("caller-a", "project")]
        expires_at, deadline = int(time.time()) + 60, time.monotonic() + 60
        policies = {
            row["intent"]["operation_id"]: {
                "owner": "original-owner", "deadline_at": expires_at - 10,
                "escalation_ref": "original-escalation",
            } for row in json.loads(store.path.read_bytes())["reviews"]
        }
        operations.retention.compact_recovery(
            scope, Cursor(**cursor), policies, expires_at=expires_at,
            checkpoint=current, deadline=deadline, lifecycle_checkpoint=current,
        )
        operations.retention.retire_cache(
            scope, Cursor(**cursor), expires_at=expires_at,
            checkpoint=current, deadline=deadline, lifecycle_checkpoint=current,
        )
        assert not list(operations.cache.generations.glob("*.json"))


@pytest.mark.parametrize("workspace_enabled,preparation_enabled,missing_preparer,mapping_enabled,terminal_enabled,terminal_mapping_enabled", [
    (False, False, False, False, False, False), (True, False, False, False, False, False),
    (True, True, False, False, False, False), (True, True, True, True, False, False),
    (True, False, False, False, True, False), (True, False, False, False, True, True),
])
def test_installed_host_process_register_observe_reconnect_restart_and_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    workspace_enabled: bool, preparation_enabled: bool, missing_preparer: bool, mapping_enabled: bool,
    terminal_enabled: bool, terminal_mapping_enabled: bool,
) -> None:
    # The existing Fleet fixture owns issuer, policy and TLS; do not add a fake Gateway.
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "fleet-manager" / "tests"))
    fixtures = import_module("gateway_service_transport_fixtures")
    eventually, running = fixtures.eventually, fixtures.running
    from agent_pull_requests.workspace_tools import WorkspaceTools

    workspace_errors: list[Exception] = []
    invoke: Callable[..., Awaitable[Any]] = WorkspaceTools.invoke

    async def diagnostic_invoke(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return await invoke(self, *args, **kwargs)
        except Exception as error:
            workspace_errors.append(error)
            raise

    monkeypatch.setattr(WorkspaceTools, "invoke", diagnostic_invoke)

    async def scenario() -> None:
        path = tmp_path / "host.json"
        state = tmp_path / "replay"
        status = state / "service-status.json"
        calls = tmp_path / "native-calls"
        proof, access = tmp_path / "service.pem", tmp_path / "access"
        evidence_path = tmp_path / "workspace-evidence.json"
        generation_id = ""
        cursor: dict[str, int] = {}
        workspace_calls = ""
        evidence: dict[str, Any] = {}
        scope: dict[str, Any] = {}
        preparation_state = tmp_path / "preparation-state.json"
        preparation_profile = tmp_path / "producer/.github/ai-attribution.json"
        allocation: dict[str, Any] = {}
        prepared_preview: dict[str, Any] = {}
        retained_request: dict[str, Any] = {}
        terminal_completion: dict[str, Any] = {}
        terminal_mapping: dict[str, Any] = {}
        snapshot = {
            "schema": "agent-worktrees.pr-snapshot", "version": 1,
            "repo": "example/project", "pr": 1, "provider": "github",
            "pr_state": "closed", "merged": True, "head_sha": "head", "base_ref": "main",
            "mergeable": True, "checks_state": "success", "title": "Fixture", "draft": False,
        }
        native = (
            "import json,pathlib,sys;"
            "assert sys.argv[1:4]==['pr-watch','snapshot','example/project'];"
            "assert sys.argv[5]=='--json';"
            "assert sys.argv[6]=='--credential-command';"
            f"pathlib.Path({str(calls)!r}).open('a').write('read\\n');"
            f"value=dict({snapshot!r});"
            "number=int(sys.argv[4]);"
            "value.update(pr=number,pr_state='closed' if number==1 else 'open',merged=number==1);"
            "print(json.dumps(value))"
        )
        flags: ProcessFlags = {
            "creationflags": cast(int, getattr(subprocess, "CREATE_NO_WINDOW")),
        } if os.name == "nt" else {}

        async def command(*arguments: str) -> tuple[int | None, bytes, bytes]:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-m", "agent_pull_requests_service", *arguments,
                cwd=tmp_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                **flags,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
                return process.returncode, stdout, stderr
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

        async def launch() -> asyncio.subprocess.Process:
            # Windows cannot deliver POSIX SIGTERM. Its control uses the public
            # foreground embedding stop event, not TerminateProcess as "drain".
            arguments = (
                ["-I", "-c", _EMBEDDED, str(path)] if os.name == "nt"
                else ["-I", "-m", "agent_pull_requests_service", "serve", "--config", str(path)]
            )
            return await asyncio.create_subprocess_exec(
                sys.executable, *arguments, cwd=tmp_path,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, **flags,
            )

        async def close(process: asyncio.subprocess.Process) -> None:
            if process.returncode is None:
                if os.name == "nt":
                    assert process.stdin is not None
                    process.stdin.write(b"stop\n")
                    await process.stdin.drain()
                else:
                    process.send_signal(signal.SIGTERM)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
                if stderr:
                    print(stderr.decode("utf-8", errors="replace")[-2048:])
                assert process.returncode == 0, (stdout, stderr)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

        async with running(tmp_path) as rig:
            proof.write_bytes(rig.h.keys[1].get_op_key("sign").private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            access.write_text(rig.access("service-a", 1))
            config = {
                "schema": "agent-pull-requests.service", "version": 1,
                "root": str(state), "namespace": "installation-a",
                "fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
                "gateway_origin": rig.origin, "gateway_ca": rig.ca,
                "proof_key_file": str(proof), "access_token_file": str(access),
                "bindings": [{
                    "principal": "caller-a", "target": "project", "consumer": "dispatch-a",
                    "account": "account-a", "repo": "example/project",
                }],
                "observations": [{
                    "account": "account-a", "repo": "example/project", "command": sys.executable,
                    "prefix": [sys.executable, "-I", "-c", native],
                    # The native fixture asserts transport of the required reference;
                    # it does not claim a live credential-provider exchange.
                    "credential_command": [sys.executable, "-I", "-m", "fleet_manager"],
                }],
                "interval": 1, "operation_seconds": 60, "drain_timeout": 5,
            }
            if workspace_enabled:
                from agent_pull_requests.native_read import PROFILE
                from agent_pull_requests.owner_read import PRINCIPAL_SEMANTICS
                evidence = json.loads((
                    root / "plugins/agent-pull-requests/docs/review-evidence.example.json"
                ).read_text())
                evidence["sections"]["pull_request"]["data"]["createdBy"] = {"id": "author-id"}
                template = evidence["sections"]["pull_request"]
                for name in ("discussions", "reviewers", "policies"):
                    evidence["sections"][name] = {
                        key: value for key, value in template.items() if key != "data"
                    } | {"data": []}
                evidence["acquisition"] = {
                    "source": PROFILE, "effects": "none", "principal_semantics": PRINCIPAL_SEMANTICS,
                    "principal_source": "provider_connection_data", "snapshot_atomic": False,
                    "duration_ms": 1, "credential_identity_checks": 2, "omissions": [],
                }
                scope = {name: value for name, value in evidence["scope"].items() if name != "number"}
                script = tmp_path / "workspace-native.py"
                script.write_text(_WORKSPACE_NATIVE)
                config["workspaces"] = {
                    "bindings": [{
                        "principal": "caller-a", "target": "project",
                        "command": [sys.executable, "-I", str(script), str(evidence_path)],
                        "scope": scope,
                    }],
                    "cache_bytes": 4 * 1024 * 1024, "cache_generations": 20,
                    **({"terminal_reads": True} if terminal_enabled else {}),
                    **({"terminal_mapping_reads": True} if terminal_mapping_enabled else {}),
                }
                if preparation_enabled:
                    preparation_profile.parent.mkdir(parents=True)
                    preparation_profile.write_text(json.dumps({
                        "version": 1, "rules": [{"header": "[Foreground producer] "}],
                    }))
                    home = tmp_path / "producer-home"
                    home.mkdir()
                    source_config = tmp_path / "producer-config.json"
                    source_config.write_text("{}")
                    origin_script = tmp_path / "origin-native.py"
                    origin_script.write_text(_PREPARATION_NATIVE)
                    producer_root = preparation_profile.parent.parent
                    preparation_state.write_text(json.dumps({
                        "supported": True, "origin": True,
                        "expected": ["--source-root", str(producer_root),
                                     "--config", str(source_config), "--visibility", "public"],
                    }))
                    config["workspaces"]["bindings"][0]["preparation"] = {
                        "command": [sys.executable, "-I", str(origin_script), str(preparation_state)],
                        "source_root": str(producer_root), "source_config": str(source_config),
                        "profile_home": str(home), "visibility": "public", "allow_anonymous": False,
                    }
                    config["workspaces"]["bindings"][0]["comment_mappings"] = mapping_enabled

            def save_evidence(offset: int) -> None:
                for section in evidence["sections"].values():
                    for field, delta in (("observed_from", 0), ("observed_to", 1)):
                        section[field] = datetime.fromtimestamp(
                            observed_at + offset + delta, timezone.utc,
                        ).isoformat()
                evidence_path.write_text(json.dumps(evidence))

            observed_at = int(time.time()) - 20
            if workspace_enabled:
                save_evidence(0)
            path.write_text(json.dumps(config))
            assert (await command("provision", "--config", str(path)))[0] == 0
            assert (await command("provision", "--config", str(path)))[0] == 1
            if mapping_enabled or terminal_mapping_enabled:
                retained_request = _seed_confirmed_comment(path, evidence)
                save_evidence(0)
            owner = await launch()
            try:
                await eventually(lambda: ready(status), timeout=10)
                assert (await command("ready", "--status", str(status)))[0] == 0
                # A second actual host cannot adopt or withdraw this writer.
                assert (await command("serve", "--config", str(path)))[0] == 1
                assert ready(status)

                async def send(
                    operation: str, payload: dict[str, Any] | None = None,
                ) -> dict[str, Any]:
                    for _ in range(100):
                        rig.h.clock.value = int(time.time())
                        target = "/api/v1/watch/project/" + operation
                        async with rig.session.post(
                            rig.origin + target, headers=await rig.auth(target, "POST"),
                            json=payload or {},
                        ) as response:
                            value = await response.json()
                            if not (
                                response.status == 503
                                and value == {"error": "watch_store_busy"}
                            ):
                                assert response.status == 200, value
                                return cast(dict[str, Any], value)
                        # The real nonblocking store lock can be held by observation.
                        # This exact response precedes store entry; retain the
                        # original request identity and never retry uncertainty.
                        await asyncio.sleep(0.05)
                    raise AssertionError(f"{operation} remained busy")

                async def workspace(
                    operation: str, payload: dict[str, Any],
                ) -> dict[str, Any]:
                    from agent_pull_requests import workspace_cli
                    from agent_pull_requests.watch_replay_binding import ReplayBinding
                    from agent_pull_requests.workspace_client import WorkspaceClient
                    from agent_pull_requests.workspace_tools import WorkspaceTools
                    from fleet_manager.gateway_service_caller import ServiceCaller

                    async def caller_access() -> str:
                        rig.h.clock.value = int(time.time())
                        return cast(str, rig.access("caller-a", 3))

                    client = WorkspaceClient(
                        ServiceCaller(
                            gateway_origin=rig.origin, gateway_ca=rig.ca,
                            access_token_callback=caller_access,
                            proof_key=rig.h.keys[3].get_op_key("sign"),
                        ),
                        route_prefix="/api",
                        binding=ReplayBinding(
                            "fleet-a", "gateway-a", "service-a", "installation-a",
                            "caller-a", "project", "dispatch-a", "account-a", "example/project",
                        ),
                        scope=scope,
                    )
                    request = tmp_path / "workspace-request.json"
                    request.write_text(json.dumps(payload))
                    arguments = ["workspace", operation]
                    if operation != "capabilities":
                        arguments += ["--request", str(request)]
                    for _ in range(100):
                        errors_before = len(workspace_errors)
                        code = await asyncio.to_thread(
                            workspace_cli.main, arguments,
                            host=cast(Any, WorkspaceTools)(client),
                        )
                        output = capsys.readouterr()
                        if code == 0:
                            return cast(dict[str, Any], json.loads(output.out))
                        if (
                            operation in {"capabilities", "events", "inspect", "materialize", "retained-comment"}
                            and len(workspace_errors) > errors_before
                            and getattr(workspace_errors[-1], "http_status", None) == 503
                        ):
                            # The observer holds a nonblocking store lock briefly.
                            # Retry only effect-free reads, never load/sync/allocation.
                            await asyncio.sleep(0.05)
                            continue
                        raise AssertionError((output.out, output.err, workspace_errors))
                    raise AssertionError(f"{operation} remained unavailable")

                original = {
                    "request_id": "original", "issued_at": time.time(),
                    "number": 1, "until": ["merged"],
                }
                receipt = await send("register", original)
                assert await send("register", original) == receipt
                for _ in range(100):
                    pending = await send("pending")
                    if pending["events"]:
                        break
                    await asyncio.sleep(0.05)
                assert len(pending["events"]) == 1
                event = pending["events"][0]
                assert event["registration_id"] == receipt["registration_id"]
                assert not event["timed_out"]
                if workspace_enabled:
                    capabilities = await workspace("capabilities", {})
                    assert "submit" not in capabilities["operations"]
                    assert ("retained-comment" in capabilities["operations"]) is (
                        mapping_enabled or terminal_mapping_enabled
                    )
                    assert capabilities["backend_contract"]["read_acquisition"] == PROFILE
                    if terminal_enabled:
                        from agent_pull_requests.workspace_terminal_read import supports
                        assert supports(capabilities["backend_contract"])
                    if terminal_mapping_enabled:
                        from agent_pull_requests.workspace_terminal_mapping import FIELD, contract
                        assert capabilities["backend_contract"][FIELD] == contract()
                        assert not {"prepare", "allocate", "receipt", "observe-comment"} & set(
                            capabilities["operations"]
                        )
                    initial = (await workspace("load", {
                        "number": 42, "expected_current": None, "budget_seconds": 10,
                    }))["result"]
                    generation_id = initial["descriptor"]["id"]
                    cursor = initial["conversation"]["cursor"]
                    assert cursor == {"epoch": 1, "sequence": 1}
                    materialized = await workspace("materialize", {
                        "number": 42, "generation_id": generation_id,
                    })
                    assert materialized["sections"]["pull_request"]["data"]["title"] == "Synthetic example"
                    if mapping_enabled:
                        mapping = (await workspace("retained-comment", retained_request))["result"]
                        assert mapping["mapping"]["status"] == "correlated"
                        assert mapping["mapping"]["generation_id"] == generation_id
                        assert mapping["action_state"] == "confirmed"
                        assert not any(mapping[name] for name in (
                            "fresh_native_read", "original_version_proven", "feedback_progress",
                        ))
                        assert "Retained note." not in json.dumps(mapping)
                    if terminal_mapping_enabled:
                        terminal_mapping = (await workspace("retained-comment", retained_request))["result"]
                        assert terminal_mapping["representation"] == "active"
                        assert terminal_mapping["mapping"]["status"] == "correlated"
                        assert terminal_mapping["action_state"] == "confirmed"
                    if preparation_enabled:
                        from agent_pull_requests.review_workspace import _digest

                        source = {
                            "schema": "agent-pull-requests.local-source", "version": 1,
                            "scope": materialized["scope"], "revision": materialized["revision"],
                            "complete": True, "errors": [], "files": [], "workspace_id": "selected-source",
                        }
                        source["id"] = _digest(source)
                        allocation = {
                            "number": 42, "budget_seconds": 10,
                            "source_attachment": {
                                "state": "ready", "scope": materialized["scope"],
                                "generation_id": generation_id, "source": source,
                            },
                            "intent": {
                                "schema": "agent-pull-requests.review-intent", "version": 1,
                                "operation_id": "foreground-selection", "scope": materialized["scope"],
                                "generation_id": generation_id, "source_id": source["id"],
                                "revision": {key: materialized["revision"][key]
                                             for key in ("head_sha", "common_sha", "iteration")},
                                "actions": [{"id": "finding", "kind": "comment", "body": "Selected note."}],
                            },
                        }
                        prepared_preview = (await workspace("allocate", allocation))["result"]
                        assert prepared_preview["intent"]["actions"][0]["body"].startswith(
                            "[Foreground producer] Selected note."
                        )
                        preparation_profile.write_text("invalid-profile")
                        native_state = json.loads(preparation_state.read_text())
                        preparation_state.write_text(json.dumps({**native_state, "origin": False}))
                        assert (await workspace("allocate", allocation))["result"] == prepared_preview
                    before_read = (state / "watch-replay.json").read_bytes()
                    page = (await workspace("events", {
                        "number": 42, "after": None, "limit": 16,
                    }))["result"]
                    assert page["high_water"] == cursor
                    assert (state / "watch-replay.json").read_bytes() == before_read
                    evidence["sections"]["discussions"]["data"] = [{
                        "id": 8, "status": "active",
                        "comments": [{"id": 1, "content": "Same-head dialogue", "author": {"id": "author-id"}}],
                    }]
                    save_evidence(2)
                    synced = (await workspace("sync", {
                        "number": 42, "expected_current": generation_id,
                        "previous_id": generation_id, "budget_seconds": 10,
                    }))["result"]
                    assert synced["conversation"]["cursor"] == {"epoch": 1, "sequence": 2}
                    assert synced["comparison"]["changed_sections"] == ["discussions"]
                    generation_id = synced["descriptor"]["id"]
                    save_evidence(4)
                    unchanged = (await workspace("sync", {
                        "number": 42, "expected_current": generation_id,
                        "previous_id": generation_id, "budget_seconds": 10,
                    }))["result"]
                    assert unchanged["conversation"]["status"] == "unchanged"
                    assert unchanged["conversation"]["cursor"] == synced["conversation"]["cursor"]
                    generation_id = unchanged["descriptor"]["id"]
                    cursor = unchanged["conversation"]["cursor"]
                    if terminal_enabled:
                        terminal_original = {
                            "request_id": "workspace-close", "issued_at": time.time(),
                            "number": 42, "until": ["closed"], "deadline_at": time.time() + 60,
                        }
                        terminal_receipt = await send("register", terminal_original)
                        evidence["sections"]["pull_request"]["data"]["status"] = "abandoned"
                        save_evidence(6)
                        terminal_result = (await workspace("load", {
                            "number": 42, "expected_current": generation_id, "budget_seconds": 10,
                        }))["result"]
                        assert terminal_result["publication"]["status"] == "retired"
                        assert terminal_result["descriptor"] is None
                        cursor = terminal_result["conversation"]["cursor"]
                        pending = await send("pending")
                        terminal_event = next(
                            value for value in pending["events"]
                            if value["registration_id"] == terminal_receipt["registration_id"]
                        )
                        assert not terminal_event["timed_out"]
                        terminal_completion = {
                            "request_id": terminal_original["request_id"],
                            "issued_at": terminal_original["issued_at"],
                            "registration_id": terminal_receipt["registration_id"],
                            "event_id": terminal_event["event_id"],
                        }
                        page = (await workspace("events", {
                            "number": 42, "after": None, "limit": 16,
                        }))["result"]
                        assert page["current"]["sections"] == {}
                        assert page["current"]["state"] == "closed-unmerged"
                    workspace_calls = evidence_path.with_suffix(".calls").read_text()
                    assert workspace_calls.splitlines() == ["capabilities", "read", "read", "read"] + (
                        ["read"] if terminal_enabled else []
                    )
                settled_reads = calls.read_text()
                assert settled_reads.splitlines() and set(settled_reads.splitlines()) == {"read"}
                persisted = (state / "watch-replay.json").read_bytes()
                assert access.read_bytes() not in persisted
                assert b"PRIVATE KEY" not in persisted
                channel = rig.state.channels["service-a"]
                # Fresh token contents are consumed by the actual reattachment.
                access.write_text(rig.access("service-a", 1))
                await channel.close()
                await eventually(
                    lambda: "service-a" in rig.state.channels
                    and rig.state.channels["service-a"] is not channel,
                    timeout=10,
                )
                assert await send("pending") == pending
            finally:
                await close(owner)
            if terminal_mapping_enabled:
                _compact_terminal_mapping(path, {**scope, "number": 42}, cursor)
            # Remounting the same namespace does not rebind the retained volume.
            # The real installed host must refuse before attachment/observation.
            retained = (state / "watch-replay.json").read_bytes()
            assert json.loads(retained)["service"] == {
                name: config[name] for name in ("fleet", "gateway", "slot")
            }
            for field in ("fleet", "gateway", "slot"):
                path.write_text(json.dumps(dict(config, **{field: "other"})))
                assert (await command("serve", "--config", str(path)))[0] == 1
                assert (state / "watch-replay.json").read_bytes() == retained
                assert calls.read_text() == settled_reads
            path.write_text(json.dumps(config))
            if preparation_enabled:
                native_state = json.loads(preparation_state.read_text())
                preparation_state.write_text(json.dumps({**native_state, "supported": False}))
                if missing_preparer:
                    config["workspaces"]["bindings"][0]["preparation"]["command"][0] = str(
                        tmp_path / "withdrawn-preparation-executable"
                    )
                    path.write_text(json.dumps(config))
            prior_channel = rig.state.channels.get("service-a")
            successor = await launch()
            try:
                await eventually(lambda: ready(status), timeout=10)
                # A stale predecessor snapshot can pass briefly; require actual attachment too.
                await eventually(
                    lambda: "service-a" in rig.state.channels
                    and rig.state.channels["service-a"] is not prior_channel,
                    timeout=10,
                )
                assert await send("pending") == pending
                if workspace_enabled:
                    if preparation_enabled:
                        capabilities = await workspace("capabilities", {})
                        assert "allocate" not in capabilities["operations"]
                        assert "submit" not in capabilities["operations"]
                        assert ("retained-comment" in capabilities["operations"]) is mapping_enabled
                        inspected = (await workspace("inspect", {
                            "number": 42, "operation_id": "foreground-selection",
                        }))["result"]
                        assert inspected["intent"] == prepared_preview["intent"]
                        assert inspected["selection"]["stage"] == "prepared"
                        origin_calls = [
                            json.loads(line) for line in
                            preparation_state.with_suffix(".calls").read_text().splitlines()
                        ]
                        assert len([call for call in origin_calls if "--source-root" in call]) == 1
                    if mapping_enabled:
                        mapping = (await workspace("retained-comment", retained_request))["result"]
                        assert mapping["mapping"]["status"] == "unavailable"
                        assert mapping["mapping"]["reason"] == "native_thread_missing"
                        assert mapping["mapping"]["generation_id"] == generation_id
                        assert mapping["action_state"] == "confirmed"
                        assert not any(mapping[name] for name in (
                            "fresh_native_read", "original_version_proven", "feedback_progress",
                        ))
                    if terminal_mapping_enabled:
                        mapping_before = (state / "watch-replay.json").read_bytes()
                        mapping = (await workspace("retained-comment", retained_request))["result"]
                        assert mapping == {**terminal_mapping, "representation": "compact"}
                        assert (state / "watch-replay.json").read_bytes() == mapping_before
                        assert evidence_path.with_suffix(".calls").read_text() == workspace_calls
                        assert not list((state / "review-cache/generations").glob("*.json"))
                    retained = (await workspace("events", {
                        "number": 42, "after": cursor, "limit": 16,
                    }))["result"]
                    assert retained["high_water"] == cursor and retained["events"] == []
                    assert evidence_path.with_suffix(".calls").read_text() == workspace_calls
                    if terminal_enabled:
                        before_reopen = (await workspace("events", {
                            "number": 42, "after": None, "limit": 16,
                        }))["result"]
                        assert before_reopen["current"]["state"] == "closed-unmerged"
                        failures = len(workspace_errors)
                        with pytest.raises(AssertionError):
                            await workspace("materialize", {"number": 42, "generation_id": generation_id})
                        assert len(workspace_errors) > failures
                        assert getattr(workspace_errors[-1], "outcome_unknown", None) is False
                        evidence["sections"]["pull_request"]["data"]["status"] = "active"
                        save_evidence(8)
                        reopened = (await workspace("load", {
                            "number": 42,
                            "expected_current": None if terminal_mapping_enabled else generation_id,
                            "budget_seconds": 10,
                        }))["result"]
                        assert reopened["conversation"]["cursor"] == {"epoch": 2, "sequence": 1}
                        generation_id = reopened["descriptor"]["id"]
                        stale = (await workspace("events", {
                            "number": 42, "after": cursor, "limit": 16,
                        }))["result"]
                        assert stale["status"] == "resync_required"
                        assert await send("pending") == pending
                    materialized = await workspace("materialize", {
                        "number": 42, "generation_id": generation_id,
                    })
                    assert materialized["sections"]["discussions"]["data"][0]["comments"][0][
                        "content"
                    ] == "Same-head dialogue"
                completion = {
                    "request_id": original["request_id"], "issued_at": original["issued_at"],
                    "registration_id": receipt["registration_id"], "event_id": event["event_id"],
                }
                ack = await send("acknowledge", completion)
                assert ack["status"] == "acknowledged"
                assert await send("acknowledge", completion) == ack
                if terminal_enabled:
                    remaining = await send("pending")
                    assert len(remaining["events"]) == 1
                    assert remaining["events"][0]["event_id"] == terminal_completion["event_id"]
                    assert (await send("acknowledge", terminal_completion))["status"] == "acknowledged"
                assert await send("pending") == {"events": [], "more": False}
                if not terminal_enabled:
                    assert calls.read_text() == settled_reads
                assert rig.backends[0].requests == []
            finally:
                await close(successor)

    asyncio.run(scenario())
