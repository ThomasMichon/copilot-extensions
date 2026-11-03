"""Finite real HTTP consumer tests; no Docker, credentials issuer or live deployment."""

from contextlib import contextmanager
import asyncio
from copy import deepcopy
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time

import pytest

from fleet_manager.komodo_evidence import native_fingerprint
from fleet_manager.komodo_roles import KomodoRoleProvider
from fleet_manager.komodo_workload import (
    MAX_RESPONSE, KomodoProjectCensus, KomodoWorkloadOwner, KomodoWorkloadSelection, server_identity,
)
from fleet_manager.node_journal import NodeJournal
from fleet_manager.planning import PlanError, canonical, digest
from fleet_manager.role_bindings import ProviderInstance, RoleBinding
from fleet_manager.runtime_admission import AdmissionAuthority, AdmissionError, OperationGrant
from test_runtime_admission import harness as harness, intent, policy, actor
from test_runtime_identity import keys as keys
from test_gateway_enrolled_identity import EnrolledRig
from test_gateway_local_handler import local_binding

SERVER = "a" * 24
STACK = "b" * 24
UPDATE = "c" * 24
IMAGE = "example.invalid/harness@sha256:" + "d" * 64
IMAGE_ID = "sha256:" + "e" * 64
ACTIONS = dict.fromkeys((
    "pulling", "deploying", "starting", "restarting", "pausing",
    "unpausing", "stopping", "destroying",
), False)


def volume(name):
    return {"Name": name, "Driver": "local", "Scope": "local", "Options": {},
            "CreatedAt": "2026-01-01T00:00:00Z", "Mountpoint": "/data/" + name,
            "Labels": {}, "ClusterVolume": None}


class Backend:
    def __init__(self):
        self.calls, self.writes, self.headers = [], [], []
        self.stack, self.container, self.selected = None, None, None
        self.server = {"_id": {"$oid": SERVER}, "name": "server-a", "template": False,
                       "config": {"enabled": True, "address": "https://periphery.invalid"},
                       "info": {"public_key": "existing-physical-server-key"}}
        self.image = {"Id": IMAGE_ID, "RepoDigests": [IMAGE],
                      "Config": {"Env": ["PATH=/usr/bin"], "User": "", "Cmd": ["init"],
                                 "Entrypoint": ["/entrypoint"]}}
        self.volumes = {name: volume(name) for name in ("home-a", "workspace-a")}
        self.lost = self.large = self.slow = self.hook = None
        self.failed, self.polls = False, 0
        self.jwt = False
        self.native_projects = ()

    def runtime(self):
        selected = self.selected
        service = selected.compose()["services"]["worker"]
        return {
            "Id": "f" * 64, "Image": selected.image_id,
            "Config": {
                "Image": selected.image, "User": "", "Env": (
                    ["PATH=/usr/bin", "container=docker"] if selected.trusted_systemd else ["PATH=/usr/bin"]),
                "Cmd": ["init"], "Entrypoint": ["/entrypoint"], "Tty": selected.trusted_systemd,
                "Labels": {**service["labels"], "com.docker.compose.project": "worker-stack",
                           "com.docker.compose.service": "worker"},
            },
            "State": {"Running": True, "Status": "running", "Paused": False,
                      "Restarting": False, "Dead": False, "Pid": 123,
                      "StartedAt": "2026-01-01T00:00:00Z"},
            "HostConfig": {"Privileged": False, "Memory": selected.memory_bytes,
                           "MemorySwap": selected.memory_bytes,
                           "NanoCpus": selected.cpu_millis * 1000000,
                           "CapAdd": ["SYS_ADMIN"] if selected.trusted_systemd else None,
                           "PidMode": "", "PortBindings": {},
                           "Tmpfs": ({"/run": "rw,exec,nosuid,size=256m",
                                      "/run/lock": "rw,exec,nosuid,size=64m"}
                                     if selected.trusted_systemd else {}),
                           "NetworkMode": "worker-stack_default"},
            "Mounts": [
                {"Type": "volume", "Name": name, "Destination": target, "RW": True,
                 "Source": self.volumes[name]["Mountpoint"]}
                for name, target in (("home-a", "/home/vscode"), ("workspace-a", "/workspaces"))
            ],
        }

    def update(self, status="Queued"):
        return {"_id": {"$oid": UPDATE}, "operation": "DeployStack",
                "target": {"type": "Stack", "id": STACK}, "status": status,
                "success": status == "Complete" and not self.failed, "end_ts": 100}

    def response(self, path, body):
        self.calls.append((path, body))
        if path.startswith(("/write/", "/execute/")):
            self.writes.append((path, deepcopy(body)))
        if path == "/read/GetVersion":
            result = {"version": "2.3.4"}
        elif path == "/read/GetServer":
            assert body == {"server": SERVER}
            result = self.server
        elif path == "/read/GetPeripheryInformation":
            result = {"version": "2.3.4", "docker_connected": True,
                      "public_key": self.server["info"]["public_key"]}
        elif path == "/read/InspectVolume":
            result = self.volumes[body["volume"]]
        elif path == "/read/InspectImage":
            assert body == {"server": SERVER, "image": self.selected.image}
            result = self.image
        elif path == "/read/ListStacks":
            result = [] if self.stack is None else [{"id": STACK, "name": "worker-stack"}]
        elif path == "/read/ListComposeProjects":
            result = []
        elif path == "/write/CreateStack":
            self.stack = {"_id": {"$oid": STACK}, "name": body["name"], "template": False,
                          "config": deepcopy(body["config"]), "info": {}}
            self.stack["config"]["file_contents"] = self.stack["config"]["file_contents"].rstrip() + "\n"
            result = self.stack
        elif path == "/write/UpdateStack":
            assert body["id"] == STACK
            self.stack["config"] = deepcopy(body["config"])
            self.stack["config"]["file_contents"] = self.stack["config"]["file_contents"].rstrip() + "\n"
            result = self.stack
        elif path == "/read/GetStack":
            assert body == {"stack": STACK}
            result = self.stack
        elif path == "/read/GetStackActionState":
            result = ACTIONS
        elif path == "/read/ListStackServices":
            result = [{"stack_id": STACK, "service": "worker"}] if self.container else []
        elif path == "/read/InspectStackContainer":
            result = self.container
        elif path == "/execute/DeployStack":
            assert body == {"stack": STACK, "services": [], "stop_time": None}
            self.container = self.runtime()
            self.native_projects = ("worker-stack",)
            result = self.update()
        elif path == "/read/GetUpdate":
            assert body == {"id": UPDATE}
            self.polls += 1
            result = self.update("InProgress" if self.polls == 1 else "Complete")
        else:
            raise AssertionError(path)
        if self.hook:
            self.hook(path)
        return result


@contextmanager
def protocol(backend):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if backend.jwt:
                    backend.headers.append(self.headers.get("authorization"))
                    assert backend.headers[-1] == "fixture-existing-jwt"
                    assert self.headers.get("x-api-key") is None
                else:
                    backend.headers.append((self.headers.get("x-api-key"), self.headers.get("x-api-secret")))
                    assert backend.headers[-1] == ("fixture-existing-key", "fixture-existing-secret")
                    assert self.headers.get("authorization") is None
                result = backend.response(self.path, body)
                if backend.lost == self.path:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                if backend.slow == self.path:
                    time.sleep(1.2)
                raw = canonical(result)
                if backend.large == self.path:
                    raw = b" " * (MAX_RESPONSE + 1)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@contextmanager
def custody(selected):
    assert selected.custody_ref == "exclusive-worker-project"
    yield lambda: None


class WorkloadRig:
    def __init__(self, path, harness, backend, endpoint):
        self.harness, self.backend = harness, backend
        self.selected = KomodoWorkloadSelection(
            ProviderInstance("manager", "komodo", "core-a", "node-a", "core-config", "bootstrap-a"),
            RoleBinding("worker", "container.service", "manager", "node-a", "worker-stack",
                        "artifact-a", "worker-config"),
            "fleet-a", "install-a", 7, "policy-1", harness.clock.value + 600,
            endpoint, "core-physical-a", canonical(backend.server),
            canonical(backend.volumes["home-a"]), canonical(backend.volumes["workspace-a"]),
            IMAGE, IMAGE_ID, 1000, 536870912, "exclusive-worker-project",
        )
        self.journal = NodeJournal(path / "node.sqlite", authority="gateway-a",
                                   installation="install-a", target="node-a")
        self.owner = KomodoWorkloadOwner(
            current=lambda: self.selected, credentials=lambda: ("fixture-existing-key", "fixture-existing-secret"),
            custody=custody, journal=self.journal, admission=harness.owner,
            authority="gateway-a", clock=harness.clock,
            current_projects=lambda selected: KomodoProjectCensus(
                digest(server_identity(json.loads(selected.server))),
                backend.native_projects, harness.clock.value, harness.clock.value + 5),
        )
        self.admit_selection()

    def admit_selection(self, **changes):
        self.backend.selected = self.selected
        self.request = intent(command="manager.converge",
                              parameters={"deployment": self.selected.deployment_digest}, **changes)
        self.harness.current = policy(revision=self.harness.current.revision + 1, enrollments=(actor(
            operations=(OperationGrant("manager.converge", "node-a", 7, self.request.request_digest),)),))

    def run(self):
        return self.owner.converge(self.request, self.harness.admit(self.request))

    def incumbent(self, *, changed=False):
        old = self.selected
        self.backend.stack = {"_id": {"$oid": STACK}, "name": "worker-stack",
                              "template": False, "config": old.config(), "info": {}}
        self.backend.container = self.backend.runtime()
        if changed:
            prior_image = "example.invalid/old@sha256:" + "1" * 64
            contents = old.compose()
            contents["services"]["worker"]["image"] = prior_image
            self.backend.stack["config"]["file_contents"] = canonical(contents).decode() + "\n"
            self.backend.container["Config"]["Image"] = prior_image
            self.backend.container["Image"] = "sha256:" + "2" * 64
        self.selected = replace(
            old, stack_id=STACK, previous_config=canonical(self.backend.stack["config"]),
            previous_container=native_fingerprint(self.backend.container),
        )
        self.admit_selection()


@pytest.mark.parametrize("trusted", [False, True])
def test_real_create_deploy_poll_readback_and_unchanged_replay(tmp_path, harness, trusted):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        rig.selected = replace(rig.selected, trusted_systemd=trusted)
        rig.admit_selection()
        receipt = rig.run()
        assert receipt.to_dict()["status"] == "succeeded"
        assert [path for path, _ in backend.writes] == ["/write/CreateStack", "/execute/DeployStack"]
        assert backend.polls == 2
        count = len(backend.writes)
        assert rig.run() == receipt
        assert len(backend.writes) == count
        assert len(backend.headers) > 10
        backend.container["Image"] = "sha256:" + "0" * 64
        with pytest.raises(PlanError, match="container"):
            rig.run()
        assert len(backend.writes) == count


@pytest.mark.parametrize("changed", [False, True])
def test_exact_existing_stack_update_and_noop(tmp_path, harness, changed):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        rig.incumbent(changed=changed)
        assert rig.run().to_dict()["status"] == "succeeded"
        assert [path for path, _ in backend.writes] == (
            ["/write/UpdateStack", "/execute/DeployStack"] if changed else [])


@pytest.mark.parametrize("foreign", ["server", "stack", "stack-id", "container", "image", "volume"])
def test_foreign_target_stack_image_storage_refuse_before_effect(tmp_path, harness, foreign):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        if foreign == "server":
            backend.server["config"]["address"] = "https://foreign.invalid"
        elif foreign == "stack":
            backend.stack = {"name": "worker-stack"}
        elif foreign == "image":
            backend.image["Id"] = "sha256:" + "0" * 64
        elif foreign == "volume":
            backend.volumes["home-a"]["CreatedAt"] = "replaced"
        elif foreign == "stack-id":
            rig.incumbent()
            backend.stack["_id"]["$oid"] = "0" * 24
        else:
            rig.incumbent()
            backend.container["Mounts"][0]["Name"] = "foreign-home"
        with pytest.raises(PlanError):
            rig.run()
        assert backend.writes == []
        assert rig.journal.inspect(rig.request) is None


@pytest.mark.parametrize("lost", ["/write/CreateStack", "/write/UpdateStack", "/execute/DeployStack"])
def test_lost_mutation_response_keeps_original_unknown_without_fresh_replay(tmp_path, harness, lost):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        if lost == "/write/UpdateStack":
            rig.incumbent(changed=True)
        backend.lost = lost
        with pytest.raises(PlanError, match="uncertain"):
            rig.run()
        assert rig.journal.inspect(rig.request).to_dict()["status"] == "unknown"
        count = len(backend.writes)
        backend.lost = None
        assert rig.run().to_dict()["status"] == "unknown"
        assert len(backend.writes) == count
        original = rig.request
        rig.admit_selection(operation="fresh-operation")
        with pytest.raises(PlanError, match="unsettled"):
            rig.run()
        assert len(backend.writes) == count
        assert rig.journal.inspect(original).to_dict()["status"] == "unknown"


@pytest.mark.parametrize("change", ["selection", "policy"])
def test_changed_selection_policy_refuse_at_mutation_checkpoint(tmp_path, harness, change):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)

        def changed(path):
            if path == "/write/CreateStack":
                if change == "selection":
                    rig.selected = replace(rig.selected, policy_revision="policy-2")
                else:
                    harness.current = replace(
                        harness.current, revision=harness.current.revision + 1, enrollments=())
        backend.hook = changed
        with pytest.raises((PlanError, AdmissionError)):
            rig.run()
        assert [path for path, _ in backend.writes] == ["/write/CreateStack"]
        assert rig.journal.inspect(rig.request).to_dict()["status"] == "unknown"
        assert not harness.owner._issued


def test_completed_failed_deploy_is_not_success(tmp_path, harness):
    backend = Backend()
    backend.failed = True
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        assert rig.run().to_dict()["status"] == "failed"
        count = len(backend.writes)
        assert rig.run().to_dict()["status"] == "failed"
        assert len(backend.writes) == count


def test_lost_terminal_publication_reconciles_original_without_redeploy(tmp_path, harness, monkeypatch):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        original = rig.journal.reconcile

        def lost(*args, **kwargs):
            raise PlanError("fixture lost terminal publication")
        monkeypatch.setattr(rig.journal, "reconcile", lost)
        with pytest.raises(PlanError, match="publication"):
            rig.run()
        assert rig.journal.inspect(rig.request).to_dict()["status"] == "unknown"
        count = len(backend.writes)
        monkeypatch.setattr(rig.journal, "reconcile", original)
        assert rig.run().to_dict()["status"] == "succeeded"
        assert len(backend.writes) == count


@pytest.mark.parametrize("bound", ["large", "slow"])
def test_actual_response_bounds_leave_no_effect_or_service(tmp_path, harness, bound):
    backend = Backend()
    setattr(backend, bound, "/read/GetVersion")
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        rig.selected = replace(rig.selected, seconds=1)
        rig.admit_selection()
        start = time.monotonic()
        with pytest.raises(PlanError, match="bound|uncertain|deadline"):
            rig.run()
        assert time.monotonic() - start < 2
        assert not backend.writes


def test_projection_stays_inert_and_execution_is_explicit(tmp_path, harness):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        provider = KomodoRoleProvider()
        assert provider.project(rig.selected.instance, rig.selected.binding).resource_type == "Stack"
        assert backend.calls == []
        with pytest.raises(PlanError, match="selected workload"):
            provider.execution_binding(object(), None, role="client", installation="client-a")


def test_local_oci_image_id_needs_no_registry_selection(tmp_path, harness):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        rig.selected = replace(rig.selected, image=IMAGE_ID)
        backend.image["RepoDigests"] = []
        rig.admit_selection()
        assert rig.run().to_dict()["status"] == "succeeded"
        assert backend.stack["config"] == rig.selected.config()


def test_existing_owner_jwt_is_ephemeral_and_reaches_real_http_header(tmp_path, harness):
    backend = Backend()
    backend.jwt = True
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        rig.owner.credentials = lambda: "fixture-existing-jwt"
        assert rig.run().to_dict()["status"] == "succeeded"
        assert set(backend.headers) == {"fixture-existing-jwt"}
        assert b"fixture-existing-jwt" not in rig.journal.path.read_bytes()


@pytest.mark.parametrize("evidence", ["foreign-project", "missing-owner", "stale", "wrong-server"])
def test_current_native_absence_never_comes_from_a_stale_empty_core_cache(tmp_path, harness, evidence):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        if evidence == "foreign-project":
            backend.native_projects = ("worker-stack",)
        elif evidence == "missing-owner":
            rig.owner.current_projects = None
        else:
            rig.owner.current_projects = lambda selected: KomodoProjectCensus(
                digest(server_identity(json.loads(selected.server))) if evidence == "stale" else "0" * 64,
                (), harness.clock.value - 5, harness.clock.value)
        with pytest.raises(PlanError, match="absence"):
            rig.run()
        assert not backend.writes
        assert not any(path == "/read/ListComposeProjects" for path, _ in backend.calls)
        assert rig.journal.inspect(rig.request) is None


@pytest.mark.parametrize("upgrade", [False, True])
def test_local_digest_incumbent_can_converge_and_upgrade(tmp_path, harness, upgrade):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        rig.selected = replace(rig.selected, image=IMAGE_ID)
        backend.image["RepoDigests"] = []
        rig.admit_selection()
        rig.incumbent()
        if upgrade:
            contents = json.loads(backend.stack["config"]["file_contents"])
            contents["services"]["worker"]["image"] = "sha256:" + "2" * 64
            backend.stack["config"]["file_contents"] = canonical(contents).decode() + "\n"
            backend.container["Config"]["Image"] = "sha256:" + "2" * 64
            backend.container["Image"] = "sha256:" + "2" * 64
            rig.selected = replace(
                rig.selected, previous_config=canonical(backend.stack["config"]),
                previous_container=native_fingerprint(backend.container))
            rig.admit_selection()
        assert rig.run().to_dict()["status"] == "succeeded"
        assert [path for path, _ in backend.writes] == (
            ["/write/UpdateStack", "/execute/DeployStack"] if upgrade else [])


def test_original_no_deploy_decision_recovers_readback_without_mutating(tmp_path, harness):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        rig.incumbent()
        backend.lost = "/read/ListStackServices"
        with pytest.raises(PlanError, match="uncertain"):
            rig.run()
        assert rig.journal.inspect(rig.request).to_dict()["status"] == "unknown"
        facts = rig.journal.external_evidence(rig.request)
        assert facts["decision"] == {"mode": "read-only", "stack": STACK}
        assert "deployment" not in facts
        backend.lost = None
        assert rig.run().to_dict()["status"] == "succeeded"
        assert not backend.writes


def test_retained_original_async_update_resumes_only_reads_after_deadline(tmp_path, harness):
    backend = Backend()
    with protocol(backend) as endpoint:
        rig = WorkloadRig(tmp_path, harness, backend, endpoint)
        rig.selected = replace(rig.selected, seconds=1)
        rig.admit_selection()
        backend.slow = "/read/GetUpdate"
        with pytest.raises(PlanError, match="uncertain|deadline"):
            rig.run()
        facts = rig.journal.external_evidence(rig.request)
        assert facts["deployment"] == {"id": UPDATE, "stack": STACK}
        assert rig.journal.inspect(rig.request).to_dict()["status"] == "unknown"
        count = len(backend.writes)
        backend.slow = None
        assert rig.run().to_dict()["status"] == "succeeded"
        assert len(backend.writes) == count


def test_role_provider_binding_carries_actual_tls_caller_to_workload_http_owner(tmp_path, harness):
    backend = Backend()
    with protocol(backend) as endpoint:
        async def exercise():
            directory = tmp_path / "gateway"
            directory.mkdir()
            gateway = EnrolledRig(directory)
            harness.clock = gateway.h.clock
            rig = WorkloadRig(tmp_path, harness, backend, endpoint)
            now = gateway.h.clock.value
            current = [policy(
                identity_profile="enrolled-pop-v1",
                valid_from=now - 1, valid_until=now + 600, identity_trust_until=now + 600,
                enrollments=(actor(subject="caller-a", operations=(OperationGrant(
                    "manager.converge", "node-a", 7, rig.request.request_digest),)),),
            )]
            admission = AdmissionAuthority(snapshot=lambda: current[0], clock=gateway.h.clock)
            rig.owner.admission = admission
            binding = KomodoRoleProvider().execution_binding(
                rig.owner, local_binding(gateway, lambda _: None).slot,
                role="client", installation="client-a")
            try:
                await gateway.start(identity_provider=gateway.h.provider, local_handlers=(binding,))
                async with await gateway.request(
                    "/api/converge", "POST", data=rig.request.encoded,
                    headers={"X-Identity": "forged"},
                ) as response:
                    result = await response.json()
                    assert response.status == 200 and result["status"] == "succeeded", result
                current[0] = replace(current[0], revision=2, enrollments=())
                async with await gateway.request(
                    "/api/converge", "POST", data=rig.request.encoded,
                ) as response:
                    assert response.status == 403
                assert not admission._issued and not gateway.connectors
            finally:
                await gateway.close()
        asyncio.run(exercise())
