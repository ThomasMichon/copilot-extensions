"""One independently runnable Komodo substrate owner, composed with existing admission."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
import time
from typing import Any

from .komodo_declaration import KomodoDeclaration, SERVICES, fingerprint
from .komodo_health import current_health, identity_digest, public_key_fingerprints, public_keys
from .komodo_native import KomodoImage, KomodoNative
from .komodo_network import retained_network, verify_network
from .komodo_selection import KomodoSelection
from .komodo_state import KomodoState
from .node_journal import NodeJournal
from .operations import OperationIntent, TargetReceipt
from .planning import PlanError, decode, digest, integer
from .private_state import create_private_file, validate_private_file
from .runtime_admission import AdmissionAuthority, Permit


def native_fingerprint(container: dict[str, Any]) -> str:
    """Keep secret-bearing native config in memory, never in a receipt or state file."""
    return digest({name: container[name] for name in ("Id", "Image", "Config", "HostConfig", "Mounts")})


def native_running(container: dict[str, Any]) -> bool:
    state = container["State"]
    return (state.get("Running") is True and state.get("Status") == "running"
            and all(state.get(key) is False for key in ("Paused", "Restarting", "Dead"))
            and type(state.get("Pid")) is int and state["Pid"] > 0
            and type(state.get("StartedAt")) is str and bool(state["StartedAt"]))


def verify_attempt_keys(attempt: dict[str, Any], selected: KomodoSelection, *,
                        allow_new: bool = True, actual: dict[str, str] | None = None) -> None:
    before = attempt.get("public_keys_before")
    if not isinstance(before, dict) or not set(before) <= {"core", "periphery"}:
        raise PlanError("Komodo original native identity evidence is unavailable")
    for value in before.values():
        fingerprint(value)
    actual = public_key_fingerprints(selected.material) if actual is None else actual
    if (any(actual.get(name) != value for name, value in before.items())
            or (not allow_new and actual != before)):
        raise PlanError("Komodo original native identity changed")


def _environment(raw: list[str] | tuple[str, ...]) -> dict[str, str]:
    result = {}
    for item in raw:
        key, separator, value = item.partition("=")
        if not separator or key in result:
            raise PlanError("Komodo native environment is ambiguous")
        result[key] = value
    return result


def verify_native(containers: dict[str, dict[str, Any]], selected: KomodoSelection,
                  images: dict[str, KomodoImage], profile: bytes, *,
                  owned: bool, ready: bool = True, complete: bool = True) -> None:
    if not set(containers) <= set(SERVICES) or (complete and set(containers) != set(SERVICES)):
        raise PlanError("Komodo native substrate is incomplete")
    if not complete and (not owned or ready):
        raise PlanError("Komodo partial evidence requires fixed-profile failure inspection")
    desired = decode(profile)["services"]
    for name in containers:
        item, expected, image = containers[name], desired[name], images[name]
        config, host = item["Config"], item["HostConfig"]
        if (item.get("Image") != image.identity
                or (ready and not native_running(item))
                or item["State"].get("Status") not in ("created", "running", "exited", "restarting", "paused")
                or config.get("User", "") != image.user
                or host.get("Privileged") is not False or host.get("CapAdd") not in (None, [])
                or host.get("PidMode", "") != "" or host.get("NetworkMode") != (
                    selected.declaration.to_dict()["project"] + "_default")
                or host.get("Memory") != expected["mem_limit"]
                or host.get("NanoCpus") != (
                    selected.declaration.to_dict()["resources"][name]["cpu_millis"] * 1000000)):
            raise PlanError("Komodo native runtime differs from selected images or isolation")
        required_mounts = {
            entry["target"]: (entry["source"], not entry["read_only"])
            for entry in expected["volumes"]
        }
        for secret in expected.get("secrets", []):
            required_mounts["/run/secrets/" + secret] = (
                str(getattr(selected.material, secret)), False)
        actual_mounts = {}
        for mount in item["Mounts"]:
            destination = mount.get("Destination")
            if (destination in actual_mounts or mount.get("Type") != "bind"
                    or type(mount.get("RW")) is not bool):
                raise PlanError("Komodo native mount custody is ambiguous")
            actual_mounts[destination] = (mount.get("Source"), mount["RW"])
        if actual_mounts != required_mounts:
            raise PlanError("Komodo native storage or credential mounts differ from selection")
        ports = host.get("PortBindings") or {}
        expected_ports = ({"9120/tcp": [{"HostIp": "127.0.0.1",
                                        "HostPort": str(selected.declaration.to_dict()["core_port"])}]}
                          if name == "core" else {})
        if ports != expected_ports:
            raise PlanError("Komodo native exposure differs from selected loopback publication")
        if owned:
            labels = config.get("Labels") or {}
            if any(labels.get(key) != value for key, value in expected["labels"].items()):
                raise PlanError("Komodo native owner or selected deployment changed")
            environment = _environment(image.environment)
            environment.update(expected["environment"])
            command = (tuple(argument.replace("$$", "$") for argument in expected["command"])
                       if "command" in expected else image.command)
            if (_environment(config.get("Env") or []) != environment
                    or tuple(config.get("Entrypoint") or ()) != image.entrypoint
                    or tuple(config.get("Cmd") or ()) != command):
                raise PlanError("Komodo native fixed configuration differs from selection")


def verify_candidate(containers: dict[str, dict[str, Any]], selected: KomodoSelection,
                     images: dict[str, KomodoImage], profile: bytes, attempt: dict[str, Any], *,
                     ready: bool = True) -> None:
    verify_attempt_keys(attempt, selected)
    if not attempt["fixed_profile"] and attempt["before"] != {
        name: native_fingerprint(item) for name, item in containers.items()
    }:
        raise PlanError("Komodo adopted native candidate was replaced or changed")
    verify_native(containers, selected, images, profile,
                  owned=attempt["fixed_profile"], ready=ready)


def verify_failed_candidate(containers: dict[str, dict[str, Any]], selected: KomodoSelection,
                            images: dict[str, KomodoImage], profile: bytes,
                            attempt: dict[str, Any]) -> bool:
    verify_attempt_keys(attempt, selected)
    if not attempt["fixed_profile"]:
        verify_candidate(containers, selected, images, profile, attempt, ready=False)
        return True
    replacements = {name: item for name, item in containers.items()
                    if attempt["before"].get(name) != native_fingerprint(item)}
    verify_native(replacements, selected, images, profile, owned=True, ready=False, complete=False)
    try:
        verify_native(containers, selected, images, profile, owned=True, ready=False)
    except PlanError:
        return False
    return True


class KomodoOwner:
    """Synchronous caller-owned work; async gateways use their existing settled worker."""

    def __init__(self, *, current: Callable[[], KomodoSelection],
                 admission: AdmissionAuthority, authority: str, clock: Callable[[], int]):
        if not callable(current) or not callable(clock):
            raise PlanError("Komodo requires current authenticated owner resolution")
        self.current, self.admission, self.authority, self.clock = current, admission, authority, clock
        self.native: KomodoNative | None = None

    @staticmethod
    def _worker() -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise PlanError("Komodo synchronous owner requires a caller-owned worker")

    def _selected(self, request: OperationIntent, expected: str | None = None) -> KomodoSelection:
        selected = self.current()
        if type(selected) is not KomodoSelection:
            raise PlanError("Komodo current owner resolution is unavailable")
        value = request.to_dict()
        now = self.clock()
        integer(now, "Komodo current owner time")
        content = selected.deployment_digest
        if (now >= selected.valid_until or value["command"] != "manager.converge"
                or value["authority"] != self.authority
                or value["target"] != selected.declaration.to_dict()["target"]
                or value["generation"] != selected.generation
                or value["parameters"] != {"deployment": content}
                or (expected is not None and content != expected)):
            raise PlanError("Komodo operation differs from current selected owner content")
        return selected

    def _ready(self, selected: KomodoSelection, containers: dict[str, dict[str, Any]],
               network: str | None, *, deadline: float,
               declaration: KomodoDeclaration | None = None,
               identity: str | None = None) -> dict[str, str]:
        declaration = selected.declaration if declaration is None else declaration
        native = self.native
        if native is None:
            raise PlanError("Komodo native readiness owner is unavailable")
        if set(containers) != set(SERVICES) or not all(map(native_running, containers.values())):
            raise PlanError("Komodo native runtime is not ready")
        health = asyncio.run(current_health(declaration, selected.material, deadline=deadline))
        after = native.inspect(declaration, selected.material, deadline=deadline)
        if set(after) != set(SERVICES) or not all(map(native_running, after.values())):
            raise PlanError("Komodo native runtime changed during readiness inspection")
        for name, item in containers.items():
            observed = after[name]
            if (native_fingerprint(item) != native_fingerprint(observed)
                    or item.get("RestartCount") != observed.get("RestartCount")
                    or any(item["State"].get(key) != observed["State"].get(key)
                           for key in ("Pid", "StartedAt"))):
                raise PlanError("Komodo native process changed during readiness inspection")
        verify_network(native.network(declaration, selected.material, deadline=deadline),
                       selected, after, network)
        if (identity_digest(selected.material) != health["public_identity"]
                or (identity is not None and health["public_identity"] != identity)):
            raise PlanError("Komodo native public identity changed during readiness inspection")
        return health

    @staticmethod
    def _baseline(state: KomodoState, selected: KomodoSelection,
                  containers: dict[str, dict[str, Any]], *, check_identity: bool = True) -> dict[str, Any]:
        if (check_identity and selected.adoption
                and identity_digest(selected.material) != selected.adopted_public_identity):
            raise PlanError("Komodo adopted native identity changed")
        record = state.read()
        if record is None:
            adoption = dict(selected.adoption)
            if containers and (
                set(containers) != set(SERVICES)
                or adoption != {name: native_fingerprint(item) for name, item in containers.items()}
            ):
                raise PlanError("Komodo incumbent requires exact existing-owner adoption")
            if not containers and adoption:
                raise PlanError("Komodo adopted resources are missing")
            return {"schema": "fleet-manager.komodo-owner", "version": 1,
                    "fleet": selected.fleet, "installation": selected.installation,
                    "resource": selected.resource, "good": None, "previous_good": None,
                    "failed": None, "retirement": None, "attempt": None}
        if (set(record) != {"schema", "version", "fleet", "installation", "resource",
                            "good", "previous_good", "failed", "retirement", "attempt"}
                or record["schema"] != "fleet-manager.komodo-owner" or record["version"] != 1
                or (record["fleet"], record["installation"], record["resource"]) != (
                    selected.fleet, selected.installation, selected.resource)):
            raise PlanError("Komodo physical resource belongs to another installation")
        return record

    @staticmethod
    def _preserved(record: dict[str, Any], selected: KomodoSelection,
                   containers: dict[str, dict[str, Any]]) -> None:
        retained = record["failed"] or record["good"]
        if retained is None:
            return
        KomodoOwner._preserved_material(record, selected)
        if any(name not in retained["native"] or native_fingerprint(item) != retained["native"][name]
               for name, item in containers.items()):
            raise PlanError("Komodo retained native resource was replaced or changed")
        if (retained["deployment"] != selected.deployment_digest
                and retained["deployment"] not in selected.compatible_with):
            raise PlanError("Komodo selected update lacks current database compatibility")

    @staticmethod
    def _preserved_material(record: dict[str, Any], selected: KomodoSelection, *,
                            allow_new_identity: bool = False) -> None:
        if (selected.adoption
                and identity_digest(selected.material) != selected.adopted_public_identity):
            raise PlanError("Komodo adopted native identity changed")
        retained = record["failed"] or record["good"]
        if retained is not None and "partial_public_keys" in retained:
            before, actual = retained["partial_public_keys"], public_key_fingerprints(selected.material)
            identity_changed = (any(actual.get(name) != value for name, value in before.items())
                                if allow_new_identity else before != actual)
        else:
            identity_changed = retained is not None and (
                retained["public_identity"] != identity_digest(selected.material))
        if retained is not None and (
            retained["material"] != selected.material_evidence()
            or identity_changed
            or any(retained["selection"][key] != selected.receipt_context()[key]
                   for key in ("adoption", "adopted_public_identity", "adopted_network"))
        ):
            raise PlanError("Komodo retained storage, credentials or identity changed")

    @staticmethod
    def _clear_settled_attempt(state: KomodoState, record: dict[str, Any],
                               journal: NodeJournal, request: OperationIntent) -> None:
        if record["attempt"] is not None and record["attempt"]["request"] != request.to_dict():
            original = OperationIntent.from_dict(record["attempt"]["request"])
            settled = journal.inspect(original)
            if settled is None or settled.to_dict()["status"] not in ("succeeded", "failed"):
                raise PlanError("Komodo retained attempt requires original-operation reconciliation")
            record["attempt"] = None
            state.save(record)

    def converge(self, request: OperationIntent, permit: Permit) -> TargetReceipt:
        """UNKNOWN is persisted before mutation; restart only inspects that original attempt."""
        try:
            self._worker()
            permit = self.admission.revalidate(permit, request)
            selected = self._selected(request)
            if selected.declaration.to_dict().get("desired_state", "present") == "absent":
                from .komodo_retirement import retire_owner

                return retire_owner(self, request, permit)
            content = selected.deployment_digest
            if permit.fleet != selected.fleet:
                raise PlanError("Komodo permit fleet differs from selected owner")
            deadline = time.monotonic() + selected.declaration.to_dict()["outfitting_seconds"]
            if self.native is None:
                self.native = KomodoNative(selected.tooling)
            elif self.native.tooling != selected.tooling:
                raise PlanError("Komodo native tooling changed within its owner")
            with KomodoState.open(
                selected.host_runtime, daemon=selected.tooling.daemon_id,
                project=selected.declaration.to_dict()["project"],
            ) as state:
                images = self.native.prerequisites(selected.declaration, selected.material, deadline=deadline)
                containers = self.native.inspect(selected.declaration, selected.material, deadline=deadline)
                record = self._baseline(state, selected, containers)
                pending = bool(record["failed"] is not None
                               and "partial_public_keys" in record["failed"])
                network = self.native.network(selected.declaration, selected.material, deadline=deadline)
                path = state.root / "node.sqlite"
                if not path.exists():
                    os.close(create_private_file(path))
                validate_private_file(path)
                journal = NodeJournal(path, authority=self.authority, installation=selected.installation,
                                      target=selected.declaration.to_dict()["target"])
                self._clear_settled_attempt(state, record, journal, request)
                self._selected(request, content)
                permit = self.admission.revalidate(permit, request)
                prior = journal.inspect(request)
                if prior is None or prior.to_dict()["status"] == "accepted":
                    self._preserved(record, selected, containers)
                    verify_network(network, selected, containers,
                                   retained_network(record, selected), pending=pending)
                receipt = journal.accept_named_external(request, resource=selected.resource)
                status = receipt.to_dict()["status"]
                profile = selected.profile()
                if status in ("succeeded", "failed"):
                    self._preserved(record, selected, containers)
                    expected_network = verify_network(
                        network, selected, containers, retained_network(record, selected), pending=pending)
                    if status == "succeeded":
                        verify_native(containers, selected, images, profile,
                                      owned=record["good"]["fixed_profile"])
                        self._ready(selected, containers, expected_network, deadline=deadline,
                                    identity=record["good"]["public_identity"])
                        self._preserved_material(record, selected)
                        self._selected(request, content)
                    self.admission.complete(permit, request)
                    if record["attempt"] is not None and record["attempt"]["request"] == request.to_dict():
                        record["attempt"] = None
                        state.save(record)
                    return receipt
                if status == "accepted":
                    self._preserved(record, selected, containers)
                    attempt = record["attempt"]
                    if attempt is not None and attempt["request"] != request.to_dict():
                        raise PlanError("Komodo retained attempt requires original-operation reconciliation")
                    if attempt is not None:
                        verify_attempt_keys(attempt, selected, allow_new=False)
                        if (attempt.get("deployment") != content or attempt.get("before") != {
                                name: native_fingerprint(item) for name, item in containers.items()}):
                            raise PlanError("Komodo accepted native attempt was replaced or changed")
                        verify_network(network, selected, containers,
                                       attempt["network_before"], pending=pending)
                    slot = state.profile(profile)
                    needs_apply = (not containers or record["good"] is not None and (
                        record["good"]["deployment"] != content or set(containers) != set(SERVICES)
                        or not all(map(native_running, containers.values())))
                        or record["failed"] is not None)
                    retained = record["failed"] or record["good"]
                    record["attempt"] = {"request": request.to_dict(), "deployment": content,
                                         "profile": str(slot),
                                         "native_settled": False,
                                         "network_before": verify_network(
                                             network, selected, containers,
                                             retained_network(record, selected), pending=pending),
                                         "fixed_profile": bool(needs_apply or retained and retained["fixed_profile"]),
                                         "public_keys_before": public_key_fingerprints(selected.material),
                                         "before": {name: native_fingerprint(item)
                                                    for name, item in containers.items()}}
                    state.save(record)
                    self._selected(request, content)
                    permit = self.admission.revalidate(permit, request)
                    state.check()
                    if not journal.begin_external(request):
                        raise PlanError("Komodo effect was already accepted by another owner")

                    def checkpoint() -> None:
                        nonlocal permit
                        self._selected(request, content)
                        self._preserved_material(record, selected)
                        verify_attempt_keys(record["attempt"], selected, allow_new=False)
                        permit = self.admission.revalidate(permit, request)
                        state.check()

                    try:
                        verify_network(self.native.network(
                            selected.declaration, selected.material, deadline=deadline),
                            selected, containers, record["attempt"]["network_before"], pending=pending)
                        if needs_apply:
                            self.native._apply(selected.declaration, selected.material, profile=slot,
                                               expected=profile, deadline=deadline, checkpoint=checkpoint)
                        else:
                            checkpoint()
                        self.admission.complete(permit, request)
                    finally:
                        if self.native.native_failure is None:
                            record["attempt"]["native_settled"] = True
                            state.save(record)
                else:
                    self.admission.complete(permit, request)
                    attempt = record["attempt"]
                    if (attempt is None or attempt["request"] != request.to_dict()
                            or attempt["deployment"] != content):
                        raise PlanError("Komodo uncertain native effect lacks original retained custody")
                    if attempt["native_settled"] is not True:
                        raise PlanError("Komodo original native process custody remains unsettled")
                containers = self.native.inspect(selected.declaration, selected.material, deadline=deadline)
                self._preserved_material(record, selected, allow_new_identity=True)
                attempt = record["attempt"]
                network = verify_network(self.native.network(
                    selected.declaration, selected.material, deadline=deadline),
                    selected, containers, attempt["network_before"],
                    created=attempt["fixed_profile"] and attempt["native_settled"])
                verify_candidate(containers, selected, images, profile, attempt)
                health = self._ready(selected, containers, network, deadline=deadline)
                verify_attempt_keys(attempt, selected)
                self._preserved_material(record, selected, allow_new_identity=True)
                self._selected(request, content)
                good = {
                    "deployment": content, "profile": record["attempt"]["profile"],
                    "material": selected.material_evidence(), "public_identity": health["public_identity"],
                    "native": {name: native_fingerprint(item) for name, item in containers.items()},
                    "health": health, "selection": selected.receipt_context(),
                    "network": network, "fixed_profile": attempt["fixed_profile"],
                }
                if record["good"] is not None and record["good"]["deployment"] != content:
                    record["previous_good"] = record["good"]
                record["good"] = good
                record["failed"] = None
                record["retirement"] = None
                result_ref = state.retain(good)
                state.save(record)
                value = request.to_dict()
                del value["parameters"]
                value.update(schema="fleet-manager.target-receipt", sequence=3, status="succeeded",
                             request_digest=request.request_digest, result_ref=result_ref)
                settled = journal.reconcile(request, TargetReceipt.from_dict(value))
                record["attempt"] = None
                state.save(record)
                return settled
        finally:
            self.admission.discard(permit)

    def settle_failed(self, request: OperationIntent, permit: Permit) -> TargetReceipt:
        """Explicitly classify a fully identified, settled candidate; never roll back here."""
        try:
            self._worker()
            permit = self.admission.revalidate(permit, request)
            selected = self._selected(request)
            if selected.declaration.to_dict().get("desired_state", "present") != "present":
                raise PlanError("Komodo failure classification requires a selected present candidate")
            content = selected.deployment_digest
            if permit.fleet != selected.fleet:
                raise PlanError("Komodo permit fleet differs from selected owner")
            deadline = time.monotonic() + selected.declaration.to_dict()["outfitting_seconds"]
            if self.native is None:
                self.native = KomodoNative(selected.tooling)
            if self.native.native_failure is not None or self.native.tooling != selected.tooling:
                raise PlanError("Komodo original native process custody remains unsettled")
            with KomodoState.open(
                selected.host_runtime, daemon=selected.tooling.daemon_id,
                project=selected.declaration.to_dict()["project"],
            ) as state:
                record = state.read()
                if record is None:
                    raise PlanError("Komodo failed candidate has no retained attempt")
                attempt = record["attempt"]
                if (attempt is None or attempt["request"] != request.to_dict()
                        or attempt["deployment"] != content or attempt["native_settled"] is not True):
                    raise PlanError("Komodo failed candidate lacks settled original native custody")
                images = self.native.prerequisites(selected.declaration, selected.material, deadline=deadline)
                containers = self.native.inspect(selected.declaration, selected.material, deadline=deadline)
                record = self._baseline(state, selected, containers)
                self._preserved_material(record, selected, allow_new_identity=True)
                profile = selected.profile()
                complete = set(containers) == set(SERVICES)
                selected_candidate = verify_failed_candidate(containers, selected, images, profile, attempt)
                network = verify_network(self.native.network(
                    selected.declaration, selected.material, deadline=deadline),
                    selected, containers, attempt["network_before"],
                    created=attempt["fixed_profile"], pending=True)
                journal = NodeJournal(
                    state.root / "node.sqlite", authority=self.authority, installation=selected.installation,
                    target=selected.declaration.to_dict()["target"])
                receipt = journal.accept_named_external(request, resource=selected.resource)
                if receipt.to_dict()["status"] != "unknown":
                    raise PlanError("Komodo failed settlement requires the original uncertain attempt")
                permit = self.admission.revalidate(permit, request)
                self.admission.complete(permit, request)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PlanError("Komodo failure inspection requires remaining native deadline")
                if selected_candidate and all(map(native_running, containers.values())):
                    try:
                        asyncio.run(current_health(
                            selected.declaration, selected.material,
                            deadline=deadline - min(2, remaining / 3)))
                    except PlanError:
                        self._selected(request, content)
                    else:
                        raise PlanError("Komodo candidate is currently healthy; reconcile success instead")
                after = self.native.inspect(selected.declaration, selected.material, deadline=deadline)
                verify_network(self.native.network(
                    selected.declaration, selected.material, deadline=deadline),
                    selected, after, network, pending=True)
                if ({name: native_fingerprint(item) for name, item in containers.items()}
                        != {name: native_fingerprint(item) for name, item in after.items()}):
                    raise PlanError("Komodo failed native candidate changed during reconciliation")
                keys = public_keys(selected.material, missing_ok=True)
                verify_attempt_keys(attempt, selected, actual={
                    name: digest(value) for name, value in keys.items()})
                failed = {
                    "deployment": content, "profile": attempt["profile"],
                    "material": selected.material_evidence(),
                    "public_identity": digest(keys) if len(keys) == 2 else None,
                    "native": {name: native_fingerprint(item) for name, item in containers.items()},
                    "reason": "authenticated-readiness-not-established",
                    "selection": selected.receipt_context(),
                    "network": network, "fixed_profile": attempt["fixed_profile"],
                    "selected_native": selected_candidate,
                }
                if not complete or len(keys) != 2:
                    failed["partial_public_keys"] = {name: digest(value) for name, value in keys.items()}
                record["failed"] = failed
                result_ref = state.retain(failed)
                state.save(record)
                value = request.to_dict()
                del value["parameters"]
                value.update(schema="fleet-manager.target-receipt", sequence=3, status="failed",
                             request_digest=request.request_digest, result_ref=result_ref)
                settled = journal.reconcile(request, TargetReceipt.from_dict(value))
                record["attempt"] = None
                state.save(record)
                return settled
        finally:
            self.admission.discard(permit)

    def inspect(self, request: OperationIntent, permit: Permit) -> dict[str, Any]:
        from .komodo_inspection import inspect_owner

        return inspect_owner(self, request, permit)
