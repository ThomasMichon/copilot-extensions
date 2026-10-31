"""Exact original grants, distinct current repair authority and interrupted publication."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from fleet_manager import komodo_state
from fleet_manager.komodo_control import CONTRACT, REPAIR_CONTRACT, repair_bootstrap, repair_status
from fleet_manager.komodo_declaration import KomodoDeclaration
from fleet_manager.komodo_evidence import native_fingerprint
from fleet_manager.komodo_native import KomodoNative
from fleet_manager.komodo_owner import KomodoOwner
from fleet_manager.komodo_repair import KomodoRepairOwner
from fleet_manager.komodo_repair_selection import KomodoRepairSelection
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.komodo_state import KomodoState
from fleet_manager.node_journal import NodeJournal
from fleet_manager.planning import PlanError, canonical, decode, digest
from fleet_manager.runtime_admission import AdmissionError, AdmissionAuthority, OperationGrant
from komodo_public_fixtures import PUBLIC_DER, PUBLIC_PEM
from test_komodo_declaration import intent
from test_komodo_native import native_rig as native_rig
from test_runtime_admission import Harness, actor, policy
from test_runtime_identity import keys as keys

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux original-owner repair")

CRASHES = [
    "native-before", "health-after", "retain-before", "retain-after", "save-before", "save-after",
    "journal-before", "journal-after", "clear-before", "clear-after",
    "complete-before", "complete-after", "journal-revoke", "append-revoke", "save-revoke",
    "pending-succeeded-replaced-network",
]
PARTIAL_SAVES = ["save-partial-renewed", "clear-partial-renewed"]
TERMINAL_NETWORK = ["terminal-failed-network", "terminal-failed-attachment"]
CLEANUP_DRIFTS = ["cleanup-key", "cleanup-endpoint"]
REPLACED_TERMINAL = [
    "terminal-succeeded-replaced-network", "status-succeeded-replaced-network",
    "status-failed-replaced-network",
]
DRIFTS = [
    f"publication-drift-{stage}-{change}"
    for stage in ("retain", "save", "append")
    for change in ("stop", "restart", "resource", "network", "endpoint", "attachment", "disconnect",
                   "immutable-outcome-missing", "immutable-outcome-corrupt", "immutable-history-missing",
                   "immutable-history-corrupt", "host-runtime", "key-during-native")
]
REFUSALS = [
    "different-request", "different-generation", "changed-candidate", "changed-executor", "same-executor",
    "changed-material", "inode-material", "wrong-fleet", "expired-transition", "expired-candidate", "revoked-transition",
    "revoked-grant", "missing-owner", "missing-lock", "missing-journal", "missing-profile",
    "missing-selection", "busy-owner",
    "unsettled", "not-fixed", "changed-profile", "changed-native", "wrong-journal",
    "revoke-after-health", "grant-after-health", "expiry-after-health", "key-after-health",
    "process-after-health", "state-after-health", "executor-after-health", "runtime-after-health",
    "health-endpoint-drift", "host-root-after-health",
    "missing-network-membership",
    "mismatched-network-attachment",
] + DRIFTS


@pytest.mark.parametrize("mode", [
    pytest.param(mode, marks=[] if mode in (
        "success", "revoked-transition", "revoke-after-health", "journal-revoke",
    ) else [pytest.mark.client_distribution_full])
    for mode in ("success", "failed", "mount-order", "renewed", "prior-failed",
                 "status-late-network",
                 "status-endpoint-drift", "status-missing-network-membership",
                 "status-mismatched-network-attachment", *REFUSALS, *CRASHES, *PARTIAL_SAVES,
                 *TERMINAL_NETWORK, *CLEANUP_DRIFTS, *REPLACED_TERMINAL)
])
def test_original_unknown_uses_current_repair_without_native_replay(
    native_rig: Any, keys: Any, monkeypatch: Any, mode: str,
) -> None:
    rig = native_rig
    (rig["directory"] / "stateful").touch()
    runtime = rig["root"] / "repair-runtime"
    runtime.mkdir(mode=0o700)
    for name in ("core", "periphery"):
        (rig["material"].keys / (name + ".pub")).write_text(PUBLIC_PEM[name])
    sentinel = rig["material"].mongo_data / "retained-fixture"
    sentinel.write_text("original-data")
    harness = Harness(keys)
    original_health_failure = [True]
    health_changes: list[bool] = []
    candidate: list[KomodoSelection] = []
    executor: list[KomodoSelection] = []
    transition = {"active": True, "revision": 1, "expiry": harness.clock() + 1000}
    runtime_identity = ["9" * 64]
    resolver_calls: list[tuple[str, str, str]] = []
    health_requests: list[str] = []

    def live() -> dict[str, Any]:
        return json.loads((rig["directory"] / "live.json").read_text())

    def replace_bridge() -> tuple[bytes, bytes]:
        path = rig["directory"] / "network.json"
        original_network, original_native = path.read_bytes(), (rig["directory"] / "live.json").read_bytes()
        network = json.loads(original_network)
        network["Id"] = "f" * 64
        path.write_text(json.dumps(network))
        changed = live()
        for item in changed.values():
            for attachment in item["NetworkSettings"]["Networks"].values():
                attachment["NetworkID"] = network["Id"]
        assert {name: native_fingerprint(item) for name, item in changed.items()} == {
            name: native_fingerprint(item) for name, item in json.loads(original_native).items()}
        (rig["directory"] / "live.json").write_text(json.dumps(changed))
        return original_network, original_native

    async def exercise() -> None:
        async def handler(request: web.Request) -> web.Response:
            health_requests.append(request.path)
            if request.path == "/auth/login/LoginLocalUser":
                assert await request.json() == {
                    "username": "admin_username-fixture-material",
                    "password": "admin_password-fixture-material",
                }
                return web.json_response({"data": {"jwt": "repair-fixture-token"}})
            assert request.headers.get("authorization") == "repair-fixture-token"
            result = {
                "/read/GetVersion": {"version": "9.0.0" if original_health_failure[0] else "2.0.0"},
                "/read/GetServer": {
                    "id": "server-a", "name": "example-host", "config": {"enabled": True, "address": ""},
                    "info": {"public_key": ""},
                },
                "/read/GetServerState": {"status": "Ok"},
                "/read/GetPeripheryInformation": {
                    "version": "2.0.0", "public_key": PUBLIC_DER["periphery"],
                    "docker_connected": True,
                },
            }[request.path]
            if request.path == "/read/GetPeripheryInformation" and not health_changes:
                health_changes.append(True)
                if mode in ("revoke-after-health", "executor-after-health"):
                    if mode.startswith("revoke"):
                        transition["active"] = False
                    else:
                        executor[0] = replace(executor[0], release_digest="8" * 64)
                elif mode in ("grant-after-health", "renewed"):
                    harness.current = policy(revision=2, enrollments=() if mode.startswith("grant") else (
                        actor(operations=(OperationGrant(
                            "manager.converge", "host-a", 1, original.request_digest),)),))
                    transition["revision"] = 2
                elif mode == "expiry-after-health":
                    harness.clock.value += 31
                elif mode == "runtime-after-health":
                    runtime_identity[0] = "8" * 64
                elif mode == "key-after-health":
                    (rig["material"].keys / "core.pub").write_text(PUBLIC_PEM["periphery"])
                elif mode in ("health-endpoint-drift", "status-endpoint-drift"):
                    (rig["directory"] / "endpoint-overrides.json").write_text(json.dumps({
                        live()["core"]["Id"]: {"EndpointID": "changed-during-health"},
                    }))
                elif mode == "host-root-after-health":
                    shifted = rig["root"] / "shifted-runtime"
                    shifted.mkdir(mode=0o700)
                    candidate[0], executor[0] = (
                        replace(item, host_runtime=shifted) for item in (candidate[0], executor[0]))
                elif mode in ("process-after-health", "mount-order"):
                    changed = live()
                    if mode == "mount-order":
                        for item in changed.values():
                            item["Mounts"].reverse()
                    else:
                        changed["core"]["State"]["StartedAt"] = "different-native-process"
                    (rig["directory"] / "live.json").write_text(json.dumps(changed))
            return web.json_response(result)

        app = web.Application()
        app.router.add_post("/{tail:.*}", handler)
        async with TestServer(app, host="127.0.0.1") as server:
            desired = rig["selected"].to_dict()
            desired.update(core_port=server.port, outfitting_seconds=10)
            candidate.append(KomodoSelection(
                KomodoDeclaration.from_dict(desired), "fleet-a", "install-a", 1, "policy-1",
                harness.clock() + 1000, "e" * 64, runtime, rig["tooling"], rig["material"]))
            executor.append(replace(candidate[0], release_digest="f" * 64))
            prior_ref = None
            baseline = KomodoOwner(
                current=lambda: candidate[0], admission=harness.owner,
                authority="gateway-a", clock=harness.clock)
            if mode == "prior-failed" or "immutable-history-" in mode:
                candidate[0] = replace(candidate[0], release_digest="d" * 64)
                before = intent("prior-failed", generation=1, deployment=candidate[0].deployment_digest)
                harness.current = policy(enrollments=(actor(operations=(
                    OperationGrant("manager.converge", "host-a", 1, before.request_digest),
                )),))
                with pytest.raises(PlanError, match="current Core version"):
                    await asyncio.to_thread(baseline.converge, before, harness.admit(before))
                failure = await asyncio.to_thread(baseline.settle_failed, before, harness.admit(before))
                prior_ref = failure.to_dict()["result_ref"]
                prior_deployment = candidate[0].deployment_digest
                candidate[0] = replace(candidate[0], release_digest="e" * 64,
                                       generation=2, compatible_with=(prior_deployment,))
                executor[0] = replace(candidate[0], release_digest="f" * 64)
            generation = candidate[0].generation
            original = intent("retained-original", generation=1, deployment=candidate[0].deployment_digest)
            if generation != 1:
                original = intent("retained-original", generation=generation,
                                  deployment=candidate[0].deployment_digest)
            harness.current = policy(revision=2 if prior_ref is not None else 1, enrollments=(actor(operations=(
                OperationGrant("manager.converge", "host-a", generation, original.request_digest),
            )),))
            with pytest.raises(PlanError, match="current Core version"):
                await asyncio.to_thread(baseline.converge, original, harness.admit(original))
            expected_effects = "one\n" * (2 if prior_ref is not None else 1)
            assert (rig["directory"] / "effects").read_text() == expected_effects
            native_before = live()
            owner_root = runtime / candidate[0].resource
            retained = decode((owner_root / "selection.json").read_bytes())["attempt"]
            journal = NodeJournal(
                owner_root / "node.sqlite", authority="gateway-a", installation="install-a",
                target="host-a", existing_only=True)
            uncertain = journal.inspect(original)
            assert uncertain.to_dict()["status"] == "unknown"
            assert uncertain.to_dict()["sequence"] == 2 and uncertain.to_dict()["result_ref"] is None
            failed = mode == "failed" or mode in TERMINAL_NETWORK or mode == "status-failed-replaced-network"
            original_health_failure[0] = failed

            def resolve(identity: Any, request: Any, role: str, installation: str) -> KomodoRepairSelection:
                resolver_calls.append((identity.subject, role, installation))
                if not transition["active"]:
                    raise PlanError("fixture repair transition withdrawn")
                if (identity.subject, role, installation, request.request_digest) != (
                        "principal-1", "client", "client-a", original.request_digest):
                    raise PlanError("fixture repair transition scope differs")
                return KomodoRepairSelection(
                    candidate[0], executor[0], original.request_digest, runtime_identity[0],
                    "repair-transition-a", transition["revision"], transition["expiry"])

            def repair_owner() -> KomodoRepairOwner:
                return KomodoRepairOwner(
                    resolve=resolve, admission=harness.owner, authority="gateway-a", clock=harness.clock)

            def call(*, status: bool = False) -> Any:
                return (repair_status if status else repair_bootstrap)(
                    repair_owner(), harness.identity(), selected_request,
                    role="client", installation="client-a",
                    **({} if status else {"failed": failed}))

            selected_request = original
            if mode in ("missing-network-membership", "status-missing-network-membership"):
                (rig["directory"] / "excluded-endpoints.json").write_text(json.dumps([live()["core"]["Id"]]))
            elif mode in ("mismatched-network-attachment", "status-mismatched-network-attachment"):
                (rig["directory"] / "endpoint-overrides.json").write_text(json.dumps({
                    live()["core"]["Id"]: {"EndpointID": "f" * 64},
                }))
            if mode == "success":
                baseline.current = lambda: executor[0]
                calls = (rig["directory"] / "calls.jsonl").read_bytes()
                with pytest.raises(PlanError, match="current selected owner content"):
                    await asyncio.to_thread(baseline.converge, original, harness.admit(original))
                with pytest.raises(PlanError, match="unsupported"):
                    repair_bootstrap(baseline, harness.identity(), original,
                                     role="client", installation="client-a")
                assert (rig["directory"] / "calls.jsonl").read_bytes() == calls
                status = await asyncio.to_thread(call, status=True)
                assert status["accepted"] == "unknown" and status["unresolved_effects"] is True
                assert status["candidate"] == status["observed"] == candidate[0].deployment_digest
                assert status["candidate_release"] == "e" * 64 and status["executor_release"] == "f" * 64
                assert status["desired"] == executor[0].deployment_digest != status["candidate"]
                assert status["installed"] is status["currently_healthy"] is True
                assert not status["converged"] and not status["native_mutations_performed"]

            if mode == "different-request":
                selected_request = intent("replacement-operation", generation=1,
                                          deployment=candidate[0].deployment_digest)
            elif mode == "different-generation":
                candidate[0] = replace(candidate[0], generation=2)
                executor[0] = replace(executor[0], generation=2)
            elif mode == "changed-candidate":
                value = candidate[0].declaration.to_dict()
                value["revision"] = "unselected-revision"
                candidate[0] = replace(candidate[0], declaration=KomodoDeclaration.from_dict(value))
                executor[0] = replace(executor[0], declaration=candidate[0].declaration)
            elif mode == "changed-executor":
                executor[0] = replace(executor[0], generation=2)
            elif mode == "same-executor":
                executor[0] = replace(executor[0], release_digest=candidate[0].release_digest)
            elif mode == "changed-material":
                rig["material"].admin_password.write_text("unselected-credential")
            elif mode == "inode-material":
                path = rig["material"].admin_password
                retained_path = path.with_name(path.name + ".retained")
                path.rename(retained_path)
                path.touch(mode=0o600)
                path.write_bytes(retained_path.read_bytes())
            elif mode == "wrong-fleet":
                harness.current = policy(fleet="other-fleet", enrollments=(actor(operations=(
                    OperationGrant("manager.converge", "host-a", 1, original.request_digest),
                )),))
                harness.owner = AdmissionAuthority(snapshot=lambda: harness.current, clock=harness.clock)
            elif mode == "expired-transition":
                transition["expiry"] = harness.clock()
            elif mode == "expired-candidate":
                candidate[0] = replace(candidate[0], valid_until=harness.clock())
            elif mode == "revoked-transition":
                transition["active"] = False
            elif mode == "revoked-grant":
                harness.current = policy(revision=2, enrollments=())
            elif mode == "missing-owner":
                owner_root.rename(owner_root.with_name("retained-owner"))
            elif mode in ("missing-lock", "missing-journal", "missing-selection", "missing-profile"):
                path = (owner_root / {"missing-lock": "owner.lock", "missing-journal": "node.sqlite",
                                     "missing-selection": "selection.json"}[mode]
                        if mode != "missing-profile" else Path(retained["profile"]))
                path.rename(path.with_name(path.name + ".retained"))
            elif mode in ("unsettled", "not-fixed"):
                with KomodoState.open(runtime, daemon="daemon-a", project=desired["project"]) as state:
                    record = state.read()
                    record["attempt"]["native_settled" if mode == "unsettled" else "fixed_profile"] = False
                    state.save(record)
            elif mode == "changed-profile":
                Path(retained["profile"]).write_bytes(Path(retained["profile"]).read_bytes() + b"\n")
            elif mode == "changed-native":
                changed = live()
                changed["core"]["Mounts"][0]["Source"] = "/unselected"
                (rig["directory"] / "live.json").write_text(json.dumps(changed))
            elif mode == "wrong-journal":
                foreign = owner_root / "foreign.sqlite"
                foreign.touch(mode=0o600)
                NodeJournal(foreign, authority="other-authority", installation="install-a", target="host-a")
                (owner_root / "node.sqlite").rename(owner_root / "original.sqlite")
                foreign.rename(owner_root / "node.sqlite")
            elif mode == "state-after-health":
                def corrupt(record: Any) -> None:
                    record["attempt"]["native_settled"] = False
                def change_after_health(*args: Any, **kwargs: Any) -> Any:
                    health = ready(*args, **kwargs)
                    record = decode((owner_root / "selection.json").read_bytes())
                    corrupt(record)
                    (owner_root / "selection.json").write_text(json.dumps(record))
                    return health
                ready = KomodoOwner._ready
                monkeypatch.setattr(KomodoOwner, "_ready", change_after_health)

            fired: list[bool] = []
            partial_save: list[tuple[Path, bytes, int, int]] = []
            late_key = [False]
            if mode == "status-late-network":
                status_network = KomodoNative.network
                finished_health_networks: list[bool] = []

                def replaced_after_inspection(instance: Any, *args: Any, **kwargs: Any) -> Any:
                    result = status_network(instance, *args, **kwargs)
                    if health_changes:
                        finished_health_networks.append(True)
                        if len(finished_health_networks) == 2:
                            fired.append(True)
                            replace_bridge()
                    return result

                monkeypatch.setattr(KomodoNative, "network", replaced_after_inspection)
            if mode in CLEANUP_DRIFTS:
                save = KomodoState.save

                def drift_after_cleanup(instance: Any, value: dict[str, Any]) -> None:
                    selected = not fired and type(value["good"]) is dict and "repair" in value["good"]
                    selected = selected and value["attempt"] is None
                    save(instance, value)
                    if selected:
                        fired.append(True)
                        if mode == "cleanup-key":
                            (rig["material"].keys / "core.pub").write_text(PUBLIC_PEM["periphery"])
                        else:
                            (rig["directory"] / "endpoint-overrides.json").write_text(json.dumps({
                                live()["core"]["Id"]: {"EndpointID": "f" * 64},
                            }))

                monkeypatch.setattr(KomodoState, "save", drift_after_cleanup)
            if mode.endswith("key-during-native"):
                original_network = KomodoNative.network

                def changed_after_network(instance: Any, *args: Any, **kwargs: Any) -> Any:
                    result = original_network(instance, *args, **kwargs)
                    if late_key[0]:
                        late_key[0] = False
                        (rig["material"].keys / "core.pub").write_text(PUBLIC_PEM["periphery"])
                    return result

                monkeypatch.setattr(KomodoNative, "network", changed_after_network)
            if mode in PARTIAL_SAVES:
                complete_pending = komodo_state._complete_pending

                def interrupted_pending(path: Path, content: bytes) -> None:
                    value = decode(content)
                    selected = not fired and path.parent == owner_root
                    selected = selected and type(value.get("good")) is dict and "repair" in value["good"]
                    selected = selected and ((value["attempt"] is None) == mode.startswith("clear"))
                    if selected:
                        fired.append(True)
                        prefix = content[:-1]
                        complete_pending(path, prefix)
                        info = path.stat()
                        partial_save.append((path, prefix, info.st_dev, info.st_ino))
                        raise PlanError("fixture state write interrupted")
                    complete_pending(path, content)

                monkeypatch.setattr(komodo_state, "_complete_pending", interrupted_pending)
            if mode in CRASHES:
                if mode.startswith("native"):
                    target, name = KomodoNative, "prerequisites"
                elif mode.startswith("health"):
                    target, name = KomodoOwner, "_ready"
                elif mode.startswith("retain"):
                    target, name = KomodoState, "retain"
                elif mode.startswith(("save", "clear", "pending")):
                    target, name = KomodoState, "save"
                elif mode.startswith("complete"):
                    target, name = AdmissionAuthority, "complete"
                else:
                    target, name = NodeJournal, "_append" if mode == "append-revoke" else "reconcile"
                original_method = getattr(target, name)

                def interrupted(instance: Any, *args: Any, **kwargs: Any) -> Any:
                    selected = not fired
                    if name == "save":
                        value = args[0]
                        selected = selected and type(value["good"]) is dict and "repair" in value["good"]
                        selected = selected and ((value["attempt"] is None) == mode.startswith("clear"))
                    if name == "_append":
                        selected = selected and args[2].to_dict()["status"] == "succeeded"
                    if selected and mode.endswith("-before"):
                        fired.append(True)
                        raise PlanError("fixture publication interrupted")
                    if selected and mode == "journal-revoke":
                        fired.append(True)
                        transition["active"] = False
                    result = (original_method(*args, **kwargs) if name == "_append"
                              else original_method(instance, *args, **kwargs))
                    if selected and mode in ("append-revoke", "save-revoke"):
                        fired.append(True)
                        transition["active"] = False
                    elif selected and (mode.endswith(("-after", "-after-health"))
                                       or mode == "pending-succeeded-replaced-network"):
                        fired.append(True)
                        raise PlanError("fixture publication interrupted")
                    return result

                monkeypatch.setattr(target, name, interrupted)

            if mode in DRIFTS:
                _, _, stage, change = mode.split("-", 3)
                target, name = ((NodeJournal, "_append") if stage == "append"
                                else (KomodoState, stage))
                original_method = getattr(target, name)

                def drifted(instance: Any, *args: Any, **kwargs: Any) -> Any:
                    selected = not fired
                    if name == "save":
                        selected = selected and type(args[0]["good"]) is dict and "repair" in args[0]["good"]
                    elif name == "_append":
                        selected = selected and args[2].to_dict()["status"] == "succeeded"
                    result = (original_method(*args, **kwargs) if name == "_append"
                              else original_method(instance, *args, **kwargs))
                    if selected:
                        fired.append(True)
                        if change == "key-during-native":
                            late_key[0] = True
                            return result
                        if change == "host-runtime":
                            shifted = rig["root"] / "shifted-runtime"
                            shifted.mkdir(mode=0o700)
                            candidate[0], executor[0] = (
                                replace(item, host_runtime=shifted) for item in (candidate[0], executor[0]))
                            return result
                        if change.startswith(("immutable-outcome-", "immutable-history-")):
                            _, scope, corruption = change.split("-")
                            reference = prior_ref if scope == "history" else (
                                result if name == "retain" else digest(args[0]["good"])
                                if name == "save" else args[2].to_dict()["result_ref"])
                            path = owner_root / "receipts" / (reference + ".json")
                            path.rename(path.with_name(path.name + ".fixture-retained"))
                            if corruption == "corrupt":
                                path.touch(mode=0o600)
                                path.write_bytes(canonical({"changed": True}))
                            return result
                        if change in ("attachment", "disconnect"):
                            identity = live()["core"]["Id"]
                            path = rig["directory"] / (
                                "endpoint-overrides.json" if change == "attachment" else "excluded-endpoints.json")
                            changed = ({identity: {"EndpointID": "changed-endpoint"}}
                                       if change == "attachment" else [identity])
                        elif change in ("network", "endpoint"):
                            path = rig["directory"] / "network.json"
                            changed = json.loads(path.read_text())
                            if change == "network":
                                changed["IPAM"]["Driver"] = "changed-network"
                            else:
                                changed["Containers"]["f" * 64] = {"Name": "foreign-endpoint"}
                        else:
                            path = rig["directory"] / "live.json"
                            changed = live()
                            if change == "stop":
                                changed["core"]["State"].update(Status="exited", Running=False)
                            elif change == "restart":
                                changed["core"]["State"].update(Pid=9999, StartedAt="changed-process")
                            else:
                                changed["core"]["Mounts"][0]["Source"] = "/unselected"
                        path.write_text(json.dumps(changed))
                    return result

                monkeypatch.setattr(target, name, drifted)

            if mode == "status-late-network":
                with pytest.raises(PlanError, match="native.*network"):
                    await asyncio.to_thread(call, status=True)
                assert fired and journal.inspect(original) == uncertain and not harness.owner._issued
            elif mode in ("status-endpoint-drift", "status-missing-network-membership",
                        "status-mismatched-network-attachment"):
                status = await asyncio.to_thread(call, status=True)
                assert status["accepted"] == "unknown" and status["unresolved_effects"]
                assert status["installed"] is status["currently_healthy"] is None
                assert "original-candidate-custody-unproved" in status["blockers"]
                assert journal.inspect(original) == uncertain and not harness.owner._issued
            elif mode in REFUSALS:
                if mode == "busy-owner":
                    with KomodoState.open(runtime, daemon="daemon-a", project=desired["project"]):
                        with pytest.raises(PlanError, match="owner is busy"):
                            await asyncio.to_thread(call)
                else:
                    with pytest.raises((PlanError, AdmissionError)):
                        await asyncio.to_thread(call)
                assert not harness.owner._issued
                if mode not in ("missing-owner", "missing-journal", "wrong-journal"):
                    assert journal.inspect(original) == uncertain
                if mode in DRIFTS:
                    assert fired and len(journal.results()) == (5 if prior_ref is not None else 2)
                    assert decode((owner_root / "selection.json").read_bytes())["attempt"] == retained
                    if mode == "publication-drift-retain-key-during-native":
                        assert decode((owner_root / "selection.json").read_bytes())["good"] is None
                if mode == "host-root-after-health" or mode.endswith("host-runtime"):
                    assert not (rig["root"] / "shifted-runtime" / candidate[0].resource).exists()
                if mode == "missing-owner":
                    assert not owner_root.exists()
                elif mode == "missing-journal":
                    assert not (owner_root / "node.sqlite").exists()
                elif mode == "missing-lock":
                    assert not (owner_root / "owner.lock").exists()
                elif mode == "missing-selection":
                    assert not (owner_root / "selection.json").exists()
            else:
                if mode in (*CRASHES, *PARTIAL_SAVES, *CLEANUP_DRIFTS):
                    with pytest.raises((PlanError, AdmissionError)):
                        await asyncio.to_thread(call)
                    assert fired and not harness.owner._issued
                    assert journal.inspect(original).to_dict()["sequence"] in (2, 3)
                    transition["active"] = True
                    if mode == "pending-succeeded-replaced-network":
                        before_health = list(health_requests)
                        original_network, original_native = replace_bridge()
                        status = await asyncio.to_thread(call, status=True)
                        assert status["accepted"] == "unknown" and status["unresolved_effects"]
                        assert status["installed"] is status["currently_healthy"] is None
                        assert "retained native network" in status["health_error"]
                        assert health_requests == before_health
                        with pytest.raises(PlanError, match="retained native network"):
                            await asyncio.to_thread(call)
                        assert journal.inspect(original) == uncertain and health_requests == before_health
                        assert decode((owner_root / "selection.json").read_bytes())["attempt"] == retained
                        (rig["directory"] / "network.json").write_bytes(original_network)
                        (rig["directory"] / "live.json").write_bytes(original_native)
                    if mode in CLEANUP_DRIFTS:
                        terminal_before_retry = journal.inspect(original)
                        terminal_rows = journal.results()
                        terminal_state = decode((owner_root / "selection.json").read_bytes())
                        assert terminal_before_retry.to_dict()["sequence"] == 3
                        assert terminal_state["attempt"] is None
                        assert terminal_state["good"]["original_attempt"] == retained
                        if mode == "cleanup-key":
                            (rig["material"].keys / "core.pub").write_text(PUBLIC_PEM["core"])
                        else:
                            flag = rig["directory"] / "endpoint-overrides.json"
                            flag.rename(flag.with_name(flag.name + ".fixture-retained"))
                    if mode in PARTIAL_SAVES:
                        before_retry = journal.inspect(original)
                        rows_before_retry = journal.results()
                        state_before_retry = decode((owner_root / "selection.json").read_bytes())
                        assert state_before_retry["attempt"] == retained
                        assert before_retry.to_dict()["sequence"] == (3 if mode.startswith("clear") else 2)
                        harness.current = policy(revision=2, enrollments=(actor(operations=(
                            OperationGrant("manager.converge", "host-a", generation, original.request_digest),
                        )),))
                        transition["revision"] = 2
                receipt = await asyncio.to_thread(call)
                assert receipt.to_dict()["status"] == ("failed" if failed else "succeeded")
                assert receipt.to_dict()["sequence"] == 3
                assert receipt.to_dict()["request_digest"] == original.request_digest
                if mode in TERMINAL_NETWORK:
                    if mode == "terminal-failed-network":
                        path = rig["directory"] / "network.json"
                        network = json.loads(path.read_text())
                        network["IPAM"]["Driver"] = "foreign-terminal-network"
                        path.write_text(json.dumps(network))
                    else:
                        (rig["directory"] / "endpoint-overrides.json").write_text(json.dumps({
                            live()["core"]["Id"]: {"EndpointID": "f" * 64},
                        }))
                    with pytest.raises(PlanError):
                        await asyncio.to_thread(call)
                    assert journal.inspect(original) == receipt
                elif mode in REPLACED_TERMINAL:
                    before_health = list(health_requests)
                    replace_bridge()
                    if mode.startswith("status"):
                        status = await asyncio.to_thread(call, status=True)
                        assert status["accepted"] == receipt.to_dict()["status"]
                        assert status["installed"] is status["currently_healthy"] is None
                        assert "original-candidate-custody-unproved" in status["blockers"]
                        assert "retained native network" in status["health_error"]
                    else:
                        with pytest.raises(PlanError, match="retained native network"):
                            await asyncio.to_thread(call)
                    assert journal.inspect(original) == receipt and health_requests == before_health
                else:
                    assert await asyncio.to_thread(call) == receipt
                if mode in CLEANUP_DRIFTS:
                    assert receipt == terminal_before_retry and journal.results() == terminal_rows
                    assert decode((owner_root / "selection.json").read_bytes()) == terminal_state
                assert len(journal.results()) == (6 if prior_ref is not None else 3)
                value = decode((owner_root / "receipts" / (
                    receipt.to_dict()["result_ref"] + ".json")).read_bytes())
                assert value["deployment"] == original.to_dict()["parameters"]["deployment"]
                assert value["original_attempt"] == retained
                assert value["selection"]["release"] == value["repair"]["candidate_release"] == "e" * 64
                assert value["repair"]["executor_release"] == "f" * 64
                assert value["repair"]["executor_runtime"] == "9" * 64
                assert value["repair"]["native_mutations_performed"] is False
                if prior_ref is not None:
                    assert value["prior_outcomes"]["failed"] == prior_ref
                    prior_value = decode((owner_root / "receipts" / (prior_ref + ".json")).read_bytes())
                    assert prior_value["deployment"] in value["selection"]["compatible_with"]
                else:
                    assert value["prior_outcomes"] == {}
                assert decode((owner_root / "selection.json").read_bytes())["attempt"] is None
                if mode in ("renewed", "save-partial-renewed"):
                    assert value["repair"]["transition_revision"] == value["repair"]["admission_revision"] == 2
                if mode in PARTIAL_SAVES:
                    if mode.startswith("clear"):
                        assert receipt == before_retry and journal.results() == rows_before_retry
                        assert value == state_before_retry["good"]
                    assert partial_save
                    path, prefix, device, inode = partial_save[0]
                    if path.exists():
                        assert path.read_bytes() == prefix and (path.stat().st_dev, path.stat().st_ino) == (device, inode)
            assert CONTRACT == "fleet-manager.komodo-substrate-owner.v1"
            assert REPAIR_CONTRACT == "fleet-manager.komodo-original-repair.v1"
            assert sentinel.read_text() == "original-data"
            assert (rig["directory"] / "effects").read_text() == expected_effects
            assert not (rig["directory"] / "retire-effects").exists()
            if mode not in ("mount-order", "changed-native", "process-after-health", "status-late-network",
                            *DRIFTS, *REPLACED_TERMINAL):
                assert live() == native_before
            if mode not in ("revoked-transition",):
                assert resolver_calls

    asyncio.run(exercise())
