"""Explicit single-role Komodo 2.3.4 execution; ordinary planning stays inert."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import re
import time
from urllib.parse import urlsplit

import aiohttp

from .komodo_evidence import native_fingerprint
from .komodo_roles import KomodoRoleProvider
from .node_journal import NodeJournal
from .operations import OperationIntent, TargetReceipt
from .planning import PlanError, canonical, decode, digest, identifier, integer
from .role_bindings import ProviderInstance, RoleBinding
from .runtime_admission import AdmissionAuthority, Permit

CONTRACT = "fleet-manager.komodo-workload.v1"
MAX_RESPONSE = 256 * 1024
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")
_IMAGE = re.compile(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def object_id(value: dict) -> str:
    """Full Komodo resources use BSON JSON, unlike resource list items."""
    if not isinstance(value, dict):
        raise PlanError("Komodo original resource identity is unavailable")
    raw = value.get("_id")
    if (not isinstance(raw, dict) or set(raw) != {"$oid"}
            or type(raw.get("$oid")) is not str
            or not re.fullmatch(r"[0-9a-f]{24}", raw["$oid"])):
        raise PlanError("Komodo original resource identity is unavailable")
    return raw["$oid"]


def server_identity(value: dict) -> dict:
    if not isinstance(value, dict):
        raise PlanError("Komodo selected Server identity is unavailable")
    config, info = value.get("config"), value.get("info")
    if (not isinstance(config, dict) or config.get("enabled") is not True
            or not isinstance(info, dict) or not info.get("public_key")
            or value.get("template") is not False):
        raise PlanError("Komodo selected Server identity is unavailable")
    return {"id": object_id(value), "name": value.get("name"),
            "config": config, "public_key": info["public_key"]}


def volume_identity(value: dict) -> dict:
    if not isinstance(value, dict):
        raise PlanError("Komodo existing persistent volume identity is unavailable")
    if (value.get("Driver") != "local" or value.get("Scope") != "local"
            or value.get("Options") != {} or value.get("ClusterVolume") is not None
            or not value.get("CreatedAt") or not value.get("Mountpoint")):
        raise PlanError("Komodo existing persistent volume identity is unavailable")
    return {key: value.get(key) for key in
            ("Name", "Driver", "Scope", "Options", "CreatedAt", "Mountpoint", "Labels")}


@dataclass(frozen=True)
class KomodoWorkloadSelection:
    """Trusted producers authenticate these inputs; constructing them grants nothing."""

    instance: ProviderInstance
    binding: RoleBinding
    fleet: str
    installation: str
    generation: int
    policy_revision: str
    valid_until: int
    endpoint: str
    controller: str
    server: bytes
    home_volume: bytes
    workspace_volume: bytes
    image: str
    image_id: str
    cpu_millis: int
    memory_bytes: int
    custody_ref: str
    stack_id: str | None = None
    previous_config: bytes | None = None
    previous_container: str | None = None
    trusted_systemd: bool = False
    seconds: int = 30

    def __post_init__(self):
        if (type(self.instance) is not ProviderInstance or type(self.binding) is not RoleBinding
                or self.instance.implementation != "komodo"
                or self.binding.provider != self.instance.name
                or self.binding.implementation not in KomodoRoleProvider.contract.role_implementations
                or self.binding.depends_on):
            raise PlanError("Komodo workload requires one exact independently admitted role")
        if set(self.binding.requires) - set(KomodoRoleProvider.contract.capabilities):
            raise PlanError("Komodo workload has unsupported provider requirements")
        for value in (self.fleet, self.installation, self.policy_revision,
                      self.controller, self.custody_ref):
            identifier(value, "Komodo workload owner reference")
        for value in (self.binding.role, self.binding.resource):
            if not _NAME.fullmatch(value):
                raise PlanError("Komodo workload requires an exact Compose name")
        if ((_IMAGE.fullmatch(self.image) is None and _DIGEST.fullmatch(self.image) is None)
                or _DIGEST.fullmatch(self.image_id) is None
                or _DIGEST.fullmatch(self.image) and self.image != self.image_id):
            raise PlanError("Komodo workload requires immutable image identities")
        integer(self.generation, "workload generation", minimum=1)
        integer(self.valid_until, "workload expiry", minimum=1)
        integer(self.seconds, "workload deadline", minimum=1, maximum=90)
        integer(self.cpu_millis, "workload CPU", minimum=100, maximum=64000)
        integer(self.memory_bytes, "workload memory", minimum=134217728, maximum=137438953472)
        if type(self.trusted_systemd) is not bool:
            raise PlanError("Komodo workload profile must be explicit")
        endpoint = urlsplit(self.endpoint)
        if (endpoint.scheme not in ("http", "https") or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                or endpoint.path not in ("", "/")
                or (endpoint.scheme == "http" and endpoint.hostname not in ("127.0.0.1", "::1"))):
            raise PlanError("Komodo requires selected TLS or authenticated loopback publication")
        server = self._record(self.server)
        server_identity(server)
        volumes = [volume_identity(self._record(raw)) for raw in
                   (self.home_volume, self.workspace_volume)]
        if (volumes[0]["Name"] == volumes[1]["Name"]
                or any(not _NAME.fullmatch(item["Name"]) for item in volumes)):
            raise PlanError("Komodo workload persistent volumes must be distinct exact names")
        if self.stack_id is None:
            if self.previous_config is not None or self.previous_container is not None:
                raise PlanError("Komodo new Stack cannot claim incumbent custody")
        else:
            object_id({"_id": {"$oid": self.stack_id}})
            if self.previous_config is None or self.previous_container is None:
                raise PlanError("Komodo incumbent requires independently authenticated custody")
            previous = self._record(self.previous_config)
            compose = decode(previous.get("file_contents", "").encode())
            expected = self.compose()
            old_image = compose.get("services", {}).get(self.binding.role, {}).get("image")
            if (not isinstance(old_image, str)
                    or not (_IMAGE.fullmatch(old_image) or _DIGEST.fullmatch(old_image))):
                raise PlanError("Komodo incumbent image is not immutable")
            expected["services"][self.binding.role]["image"] = old_image
            if (compose != expected or previous != self.config(contents=previous["file_contents"])
                    or not re.fullmatch(r"mounts-v2:[0-9a-f]{64}", self.previous_container)):
                raise PlanError("Komodo incumbent configuration or storage differs from selection")

    @staticmethod
    def _record(raw: bytes) -> dict:
        if type(raw) is not bytes:
            raise PlanError("Komodo selection requires immutable canonical owner records")
        value = decode(raw)
        if not isinstance(value, dict) or canonical(value) != raw:
            raise PlanError("Komodo selection requires canonical owner records")
        return value

    @property
    def resource(self) -> str:
        return digest({"controller": self.controller, "server": object_id(self._record(self.server)),
                       "project": self.binding.resource})

    def compose(self) -> dict:
        home, workspace = (self._record(raw)["Name"] for raw in
                           (self.home_volume, self.workspace_volume))
        service = {
            "image": self.image, "pull_policy": "never", "restart": "unless-stopped",
            "cpus": self.cpu_millis / 1000, "mem_limit": self.memory_bytes,
            "memswap_limit": self.memory_bytes,
            "volumes": [
                {"type": "volume", "source": "home", "target": "/home/vscode"},
                {"type": "volume", "source": "workspace", "target": "/workspaces"},
            ],
            "labels": {"fleet-manager.owner": self.resource},
        }
        if self.trusted_systemd:
            service.update(cap_add=["SYS_ADMIN"], tty=True,
                           environment={"container": "docker"},
                           tmpfs=["/run:rw,exec,nosuid,size=256m",
                                  "/run/lock:rw,exec,nosuid,size=64m"])
        return {"services": {self.binding.role: service},
                "volumes": {"home": {"external": True, "name": home},
                            "workspace": {"external": True, "name": workspace}}}

    def config(self, *, contents: str | None = None) -> dict:
        config = dict.fromkeys((
            "swarm_id", "linked_repo", "repo", "commit", "clone_path", "git_account",
            "webhook_secret", "run_directory", "registry_provider", "registry_account",
            "compose_cmd_wrapper", "environment",
        ), "")
        config.update(dict.fromkeys((
            "run_build", "poll_for_updates", "auto_update", "auto_update_all_services",
            "destroy_before_deploy", "reclone", "webhook_enabled", "webhook_force_deploy",
            "files_on_host", "auto_pull",
        ), False))
        for key in ("links", "auto_update_skip_services", "file_paths", "additional_env_files",
                    "config_files", "extra_args", "build_extra_args",
                    "compose_cmd_wrapper_include", "ignore_services"):
            config[key] = []
        config.update(
            server_id=object_id(self._record(self.server)), project_name=self.binding.resource,
            git_provider="github.com", git_https=True, branch="main",
            env_file_path=".env", send_alerts=True, skip_secret_interp=True,
            pre_deploy={"path": "", "command": "", "shell_mode": False},
            post_deploy={"path": "", "command": "", "shell_mode": False},
            file_contents=canonical(self.compose()).decode() + "\n" if contents is None else contents,
        )
        return config

    @property
    def deployment_digest(self) -> str:
        return digest({
            "contract": CONTRACT, "instance": self.instance.as_dict(),
            "binding": self.binding.as_dict(), "fleet": self.fleet,
            "installation": self.installation, "generation": self.generation,
            "policy_revision": self.policy_revision,
            "endpoint": self.endpoint, "controller": self.controller,
            "server": server_identity(self._record(self.server)), "custody": self.custody_ref,
            "volumes": [volume_identity(self._record(raw)) for raw in
                        (self.home_volume, self.workspace_volume)],
            "config": self.config(), "image_id": self.image_id, "stack_id": self.stack_id,
            "previous_config": None if self.previous_config is None else decode(self.previous_config),
            "previous_container": self.previous_container, "seconds": self.seconds,
        })


@dataclass(frozen=True)
class KomodoProjectCensus:
    """Existing physical owner authenticates a current native census, not Core cache."""

    server_fingerprint: str
    projects: tuple[str, ...]
    observed_at: int
    valid_until: int

    def __post_init__(self):
        if (type(self.server_fingerprint) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", self.server_fingerprint)
                or type(self.projects) is not tuple or len(self.projects) > 256
                or len(set(self.projects)) != len(self.projects)):
            raise PlanError("Komodo current native project census is unavailable")
        for name in self.projects:
            identifier(name, "Komodo native project")
        integer(self.observed_at, "Komodo project observation time")
        integer(self.valid_until, "Komodo project observation expiry")
        if not self.observed_at < self.valid_until <= self.observed_at + 5:
            raise PlanError("Komodo current native project census must have a finite freshness bound")


class KomodoWorkloadOwner:
    """Code-selected caller-owned work, never an automatic planner or API proxy."""

    def __init__(self, *, current: Callable[[], KomodoWorkloadSelection],
                 credentials: Callable[[], tuple[str, str] | str],
                 custody: Callable[[KomodoWorkloadSelection], AbstractContextManager],
                 journal: NodeJournal, admission: AdmissionAuthority,
                 authority: str, clock: Callable[[], int],
                 current_projects: Callable[[KomodoWorkloadSelection], KomodoProjectCensus] | None = None):
        if not all(map(callable, (current, credentials, custody, clock))):
            raise PlanError("Komodo workload requires existing code-registered owners")
        if current_projects is not None and not callable(current_projects):
            raise PlanError("Komodo native census requires its existing code-registered owner")
        self.current, self.credentials, self.custody = current, credentials, custody
        self.journal, self.admission, self.authority, self.clock = journal, admission, authority, clock
        self.current_projects = current_projects

    def _selected(self, request: OperationIntent, expected: str | None = None):
        selected = self.current()
        if type(selected) is not KomodoWorkloadSelection:
            raise PlanError("Komodo workload selection is unavailable")
        value = request.to_dict()
        content = selected.deployment_digest
        if (self.clock() >= selected.valid_until or value["command"] != "manager.converge"
                or value["authority"] != self.authority or value["target"] != selected.binding.target
                or value["generation"] != selected.generation
                or value["parameters"] != {"deployment": content}
                or expected is not None and content != expected
                or (self.journal.authority, self.journal.installation, self.journal.target)
                != (self.authority, selected.installation, selected.binding.target)):
            raise PlanError("Komodo workload operation differs from current selected owner")
        return selected

    def converge(self, request: OperationIntent, permit: Permit) -> TargetReceipt:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise PlanError("Komodo workload requires a caller-owned synchronous worker")
        permits = [permit]
        try:
            return asyncio.run(self._converge(request, permits))
        finally:
            self.admission.discard(permits[0])

    async def _converge(self, request: OperationIntent, permits: list[Permit]) -> TargetReceipt:
        permit = permits[0]
        selected = self._selected(request)
        content = selected.deployment_digest
        deadline = time.monotonic() + selected.seconds
        with self.custody(selected) as custody_check:
            if not callable(custody_check):
                raise PlanError("Komodo workload requires an active exact-resource custody checkpoint")

            def checkpoint():
                nonlocal permit
                self._selected(request, content)
                if custody_check() is not None:
                    raise PlanError("Komodo workload custody checkpoint returned unsupported evidence")
                permit = self.admission.revalidate(permit, request)
                permits[0] = permit
                if permit.fleet != selected.fleet:
                    raise PlanError("Komodo workload permit fleet differs from selection")
                if time.monotonic() >= deadline:
                    raise PlanError("Komodo workload response deadline expired")

            def project_absent():
                checkpoint()
                if self.current_projects is None:
                    raise PlanError("Komodo creation requires current authenticated native project absence")
                census = self.current_projects(selected)
                now = self.clock()
                if (type(census) is not KomodoProjectCensus
                        or census.server_fingerprint != digest(server_identity(decode(selected.server)))
                        or not census.observed_at <= now < census.valid_until
                        or selected.binding.resource in census.projects):
                    raise PlanError("Komodo current authenticated project absence is unproved")
                checkpoint()

            async with aiohttp.ClientSession(
                trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
                connector=aiohttp.TCPConnector(limit=1),
            ) as client:
                async def call(path, payload):
                    checkpoint()
                    supplied = self.credentials()
                    if type(supplied) is str:
                        headers = {"authorization": supplied}
                    elif type(supplied) is tuple and len(supplied) == 2:
                        headers = {"x-api-key": supplied[0], "x-api-secret": supplied[1]}
                    else:
                        raise PlanError("Komodo existing API credentials are unavailable")
                    if any(type(value) is not str or not 0 < len(value) <= 8192
                           or any(ord(char) < 33 or ord(char) > 126 for char in value)
                           for value in headers.values()):
                        raise PlanError("Komodo existing API credentials are unavailable")
                    try:
                        async with client.post(
                            selected.endpoint.rstrip("/") + path, json=payload, headers=headers,
                            allow_redirects=False, timeout=aiohttp.ClientTimeout(
                                total=min(5, max(0.001, deadline - time.monotonic()))),
                        ) as response:
                            if response.status != 200:
                                raise PlanError("Komodo selected API request was refused")
                            data = bytearray()
                            async for chunk in response.content.iter_chunked(8192):
                                data.extend(chunk)
                                if len(data) > MAX_RESPONSE:
                                    raise PlanError("Komodo workload response exceeds byte bound")
                            result = decode(bytes(data))
                            expected_type = list if path in (
                                "/read/ListStacks", "/read/ListComposeProjects", "/read/ListStackServices",
                            ) else dict
                            if type(result) is not expected_type:
                                raise PlanError("Komodo workload response has an unsupported shape")
                    except (aiohttp.ClientError, asyncio.TimeoutError):
                        raise PlanError("Komodo workload response is unavailable or uncertain") from None
                    finally:
                        del supplied, headers
                    checkpoint()
                    return result

                async def prerequisites():
                    if await call("/read/GetVersion", {}) != {"version": "2.3.4"}:
                        raise PlanError("Komodo workload requires the qualified 2.3.4 API")
                    server = await call("/read/GetServer", {"server": server_id})
                    if server_identity(server) != server_identity(decode(selected.server)):
                        raise PlanError("Komodo selected physical Server changed")
                    periphery = await call("/read/GetPeripheryInformation", {"server": server_id})
                    if (periphery.get("version") != "2.3.4"
                            or periphery.get("docker_connected") is not True
                            or periphery.get("public_key") != server["info"]["public_key"]):
                        raise PlanError("Komodo current Periphery identity differs from selection")
                    for raw in (selected.home_volume, selected.workspace_volume):
                        volume = decode(raw)
                        actual = await call("/read/InspectVolume",
                                            {"server": server_id, "volume": volume["Name"]})
                        if volume_identity(actual) != volume_identity(volume):
                            raise PlanError("Komodo original persistent volume changed")
                    image = await call("/read/InspectImage", {"server": server_id, "image": selected.image})
                    if (image.get("Id") != selected.image_id
                            or selected.image != selected.image_id
                            and selected.image not in image.get("RepoDigests", [])):
                        raise PlanError("Komodo selected image is unavailable on the exact Server")
                    return image

                async def stack_read(stack_id):
                    stack = await call("/read/GetStack", {"stack": stack_id})
                    if (object_id(stack) != stack_id or stack.get("name") != selected.binding.resource
                            or stack.get("template") is not False
                            or stack.get("config", {}).get("server_id") != server_id
                            or stack["config"].get("swarm_id") != ""
                            or stack["config"].get("project_name") != selected.binding.resource
                            or (stack.get("info") or {}).get("deployed_project_name")
                            not in (None, selected.binding.resource)):
                        raise PlanError("Komodo exact Stack or target custody changed")
                    actions = await call("/read/GetStackActionState", {"stack": stack_id})
                    if (not isinstance(actions, dict) or set(actions) != {
                        "pulling", "deploying", "starting", "restarting", "pausing",
                        "unpausing", "stopping", "destroying",
                    } or any(value is not False for value in actions.values())):
                        raise PlanError("Komodo original Stack still has unfinished backend work")
                    return stack

                async def readback(stack_id, *, expected_native=None):
                    stack = await stack_read(stack_id)
                    if stack["config"] != selected.config():
                        raise PlanError("Komodo Stack configuration differs from admitted content")
                    services = await call("/read/ListStackServices", {"stack": stack_id})
                    if (not isinstance(services, list) or len(services) != 1
                            or services[0].get("stack_id") != stack_id
                            or services[0].get("service") != selected.binding.role):
                        raise PlanError("Komodo Stack service set differs from admitted workload")
                    container = await call("/read/InspectStackContainer",
                                           {"stack": stack_id, "service": selected.binding.role})
                    if expected_native is not None and native_fingerprint(container) != expected_native:
                        raise PlanError("Komodo original no-deploy container was replaced or changed")
                    image = await prerequisites()
                    _verify_container(container, selected, image=image)
                    if (await stack_read(stack_id))["config"] != selected.config():
                        raise PlanError("Komodo Stack changed during current readback")
                    after = await call("/read/InspectStackContainer",
                                       {"stack": stack_id, "service": selected.binding.role})
                    if (native_fingerprint(after) != native_fingerprint(container)
                            or any(after.get("State", {}).get(key) != container["State"].get(key)
                                   for key in ("Running", "Status", "Paused", "Restarting",
                                               "Dead", "Pid", "StartedAt"))):
                        raise PlanError("Komodo current container changed during readback")
                    return {"deployment": content, "stack": stack_id, "container": container["Id"],
                            "config": digest(stack["config"]), "image": selected.image_id}

                server_id = object_id(decode(selected.server))
                checkpoint()
                held = self.journal.inspect_named_external(resource=selected.resource)
                if (held is not None and held.to_dict()["request_digest"] != request.request_digest
                        and held.to_dict()["status"] in ("accepted", "unknown")):
                    raise PlanError("Komodo physical workload has an unsettled original operation")
                image = await prerequisites()
                prior = self.journal.inspect(request)
                needs_deploy = True
                if prior is None or prior.to_dict()["status"] == "accepted":
                    if selected.stack_id is None:
                        project_absent()
                        stacks = await call("/read/ListStacks", {
                            "query": {"names": [selected.binding.resource]}, "page": 0, "limit": 2})
                        if stacks != []:
                            raise PlanError("Komodo matching names do not establish Stack or project custody")
                    else:
                        stack = await stack_read(selected.stack_id)
                        if stack["config"] != decode(selected.previous_config):
                            raise PlanError("Komodo independently admitted incumbent configuration changed")
                        before = await call("/read/InspectStackContainer", {
                            "stack": selected.stack_id, "service": selected.binding.role})
                        if native_fingerprint(before) != selected.previous_container:
                            raise PlanError("Komodo independently admitted incumbent container changed")
                        if stack["config"] == selected.config():
                            _verify_container(before, selected, image=image, ready=False)
                            needs_deploy = before["State"]["Running"] is not True
                    checkpoint()
                receipt = self.journal.accept_named_external(request, resource=selected.resource)
                facts = self.journal.external_evidence(request)
                status = receipt.to_dict()["status"]
                if status in ("succeeded", "failed"):
                    outcome = facts.get("outcome")
                    if outcome is None or digest(outcome) != receipt.to_dict()["result_ref"]:
                        raise PlanError("Komodo original terminal evidence is unavailable")
                    if status == "succeeded" and await readback(outcome["stack"]) != outcome:
                        raise PlanError("Komodo current workload differs from its original outcome")
                    checkpoint()
                    return receipt
                if status == "accepted":
                    self.journal.retain_external_evidence(
                        request, name="selection", evidence={"deployment": content})
                    self.journal.retain_external_evidence(
                        request, name="decision", evidence={
                            "mode": "mutation" if needs_deploy else "read-only",
                            "stack": selected.stack_id})
                    if not self.journal.begin_external(request):
                        raise PlanError("Komodo original workload attempt already began")
                    stack_id = selected.stack_id
                    if stack_id is None:
                        project_absent()
                        created = await call("/write/CreateStack", {
                            "name": selected.binding.resource, "config": selected.config()})
                        stack_id = object_id(created)
                        if (created.get("name") != selected.binding.resource
                                or created.get("template") is not False
                                or created.get("config") != selected.config()):
                            raise PlanError("Komodo Stack creation response differs from selected content")
                    elif decode(selected.previous_config) != selected.config():
                        updated = await call("/write/UpdateStack",
                                             {"id": stack_id, "config": selected.config()})
                        if (object_id(updated) != stack_id
                                or updated.get("name") != selected.binding.resource
                                or updated.get("template") is not False
                                or updated.get("config") != selected.config()):
                            raise PlanError("Komodo Stack update response differs from selected content")
                    self.journal.retain_external_evidence(request, name="stack", evidence={"id": stack_id})
                    if (await stack_read(stack_id))["config"] != selected.config():
                        raise PlanError("Komodo write did not retain the exact selected configuration")
                    if needs_deploy:
                        await prerequisites()
                        if selected.stack_id is None:
                            project_absent()
                        deployment = await call("/execute/DeployStack", {
                            "stack": stack_id, "services": [], "stop_time": None})
                        _verify_update(deployment, stack_id)
                        self.journal.retain_external_evidence(
                            request, name="deployment", evidence={"id": object_id(deployment), "stack": stack_id})
                    else:
                        outcome = await readback(stack_id, expected_native=selected.previous_container)
                        self.journal.retain_external_evidence(request, name="outcome", evidence=outcome)
                    facts = self.journal.external_evidence(request)
                if facts.get("selection") != {"deployment": content}:
                    raise PlanError("Komodo original workload selection evidence changed")
                if "outcome" not in facts:
                    deployment = facts.get("deployment")
                    if deployment is None:
                        if facts.get("decision") != {"mode": "read-only", "stack": selected.stack_id}:
                            return self.journal.inspect(request)
                        outcome = await readback(
                            selected.stack_id, expected_native=selected.previous_container)
                        self.journal.retain_external_evidence(request, name="outcome", evidence=outcome)
                        facts = self.journal.external_evidence(request)
                if "outcome" not in facts:
                    deployment = facts["deployment"]
                    stack_id = deployment["stack"]
                    while True:
                        update = await call("/read/GetUpdate", {"id": deployment["id"]})
                        _verify_update(update, stack_id)
                        if object_id(update) != deployment["id"]:
                            raise PlanError("Komodo original deployment update identity changed")
                        if update["status"] == "Complete":
                            break
                        await asyncio.sleep(min(0.25, max(0, deadline - time.monotonic())))
                    if update.get("success") is not True:
                        # A completed failed action is evidence of failure, not convergence.
                        await stack_read(stack_id)
                        await prerequisites()
                        outcome = {"deployment": content, "stack": stack_id,
                                   "failed_update": deployment["id"]}
                        terminal = "failed"
                    else:
                        outcome = await readback(stack_id)
                        terminal = "succeeded"
                    self.journal.retain_external_evidence(request, name="outcome", evidence=outcome)
                else:
                    outcome = facts["outcome"]
                    terminal = "failed" if "failed_update" in outcome else "succeeded"
                    if terminal == "succeeded" and await readback(outcome["stack"]) != outcome:
                        raise PlanError("Komodo retained outcome lacks current authenticated readback")
                    if terminal == "failed":
                        update = await call("/read/GetUpdate", {"id": outcome["failed_update"]})
                        _verify_update(update, outcome["stack"])
                        if (object_id(update) != outcome["failed_update"]
                                or update["status"] != "Complete" or update["success"] is not False
                                or (await stack_read(outcome["stack"]))["config"] != selected.config()):
                            raise PlanError("Komodo original failed outcome remains unconfirmed")
                checkpoint()
                value = request.to_dict()
                del value["parameters"]
                value.update(schema="fleet-manager.target-receipt", status=terminal,
                             sequence=self.journal.inspect(request).to_dict()["sequence"] + 1,
                             request_digest=request.request_digest, result_ref=digest(outcome))
                return self.journal.reconcile(
                    request, TargetReceipt.from_dict(value), checkpoint=checkpoint)


def _verify_update(value: dict, stack: str) -> None:
    object_id(value)
    if (value.get("operation") != "DeployStack"
            or value.get("target") != {"type": "Stack", "id": stack}
            or value.get("status") not in ("Queued", "InProgress", "Complete")
            or type(value.get("success")) is not bool
            or value.get("status") == "Complete" and (
                type(value.get("end_ts")) not in (int, str)
                or not str(value["end_ts"]).isdigit())):
        raise PlanError("Komodo deployment response is not the original Stack operation")


def _verify_container(value: dict, selected: KomodoWorkloadSelection, *,
                      image: dict, ready: bool = True) -> None:
    if (not isinstance(value, dict) or any(
        not isinstance(value.get(key), dict) for key in ("State", "Config", "HostConfig")
    )):
        raise PlanError("Komodo current container inspection is incomplete")
    state, config, host = (value.get(key) or {} for key in ("State", "Config", "HostConfig"))
    labels = config.get("Labels") or {}
    allowed_caps = (["SYS_ADMIN"], ["CAP_SYS_ADMIN"]) if selected.trusted_systemd else (None, [])
    if (not value.get("Id") or value.get("Image") != selected.image_id
            or config.get("Image") != selected.image
            or ready and (state.get("Running") is not True or state.get("Status") != "running")
            or ready and (type(state.get("Pid")) is not int or state["Pid"] <= 0
                          or not state.get("StartedAt"))
            or any(state.get(key) is not False for key in ("Paused", "Restarting", "Dead"))
            or labels.get("fleet-manager.owner") != selected.resource
            or labels.get("com.docker.compose.project") != selected.binding.resource
            or labels.get("com.docker.compose.service") != selected.binding.role
            or host.get("Privileged") is not False
            or host.get("RestartPolicy") != {"Name": "unless-stopped", "MaximumRetryCount": 0}
            or host.get("Memory") != selected.memory_bytes
            or host.get("MemorySwap") != selected.memory_bytes
            or host.get("CapAdd") not in allowed_caps
            or host.get("PidMode", "") != "" or host.get("PortBindings") not in (None, {})
            or host.get("NetworkMode") != selected.binding.resource + "_default"
            or host.get("NanoCpus") != selected.cpu_millis * 1000000):
        raise PlanError("Komodo current container does not satisfy the selected workload")
    image_config = image.get("Config")
    if not isinstance(image_config, dict):
        raise PlanError("Komodo selected image configuration is unavailable")

    def environment(items):
        result = {}
        if not isinstance(items, list):
            raise PlanError("Komodo current image environment is unavailable")
        for item in items:
            if not isinstance(item, str):
                raise PlanError("Komodo current image environment is unavailable")
            key, separator, value = item.partition("=")
            if not separator or key in result:
                raise PlanError("Komodo current image environment is ambiguous")
            result[key] = value
        return result

    expected_environment = environment(image_config.get("Env") or [])
    if selected.trusted_systemd:
        expected_environment["container"] = "docker"
    if (environment(config.get("Env") or []) != expected_environment
            or any((config.get(key) or []) != (image_config.get(key) or [])
                   for key in ("Cmd", "Entrypoint"))
            or config.get("User", "") != image_config.get("User", "")
            or config.get("Tty") is not selected.trusted_systemd):
        raise PlanError("Komodo current container command or image configuration changed")
    tmpfs = host.get("Tmpfs") or {}
    if not selected.trusted_systemd:
        if tmpfs:
            raise PlanError("Komodo current workload has unselected volatile storage")
    else:
        if set(tmpfs) != {"/run", "/run/lock"}:
            raise PlanError("Komodo trusted systemd volatile storage is incomplete")
        for path, size in (("/run", 268435456), ("/run/lock", 67108864)):
            options = set(tmpfs[path].split(","))
            sizes = {f"size={size}", f"size={size // 1048576}m"}
            if (len(options & sizes) != 1 or not {"exec", "nosuid"} <= options
                    or options - sizes - {"rw", "exec", "nosuid"}):
                raise PlanError("Komodo trusted systemd volatile storage differs from selection")
    expected = {target: decode(raw)["Name"] for target, raw in (
        ("/home/vscode", selected.home_volume), ("/workspaces", selected.workspace_volume))}
    found = {}
    seen = set()
    for mount in value.get("Mounts", []):
        destination = mount.get("Destination")
        if destination in seen:
            raise PlanError("Komodo current workload mounts are ambiguous")
        seen.add(destination)
        if mount.get("Type") == "tmpfs" and selected.trusted_systemd:
            if mount.get("Destination") in ("/run", "/run/lock"):
                continue
        target = mount.get("Destination")
        if (target in found or target not in expected or mount.get("Type") != "volume"
                or mount.get("Name") != expected[target] or mount.get("RW") is not True
                or mount.get("Source") != decode(
                    selected.home_volume if target == "/home/vscode" else selected.workspace_volume
                )["Mountpoint"]):
            raise PlanError("Komodo current workload storage differs from original volumes")
        found[target] = mount["Name"]
    if found != expected:
        raise PlanError("Komodo current workload persistent storage is incomplete")
