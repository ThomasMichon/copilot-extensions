"""One independently runnable Komodo substrate owner, composed with existing admission."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
import time
from typing import Any

from .komodo_declaration import SERVICES
from .komodo_health import current_health, identity_digest
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
                  owned: bool, ready: bool = True) -> None:
    if set(containers) != set(SERVICES):
        raise PlanError("Komodo native substrate is incomplete")
    desired = decode(profile)["services"]
    for name in SERVICES:
        item, expected, image = containers[name], desired[name], images[name]
        config, host = item["Config"], item["HostConfig"]
        if (item.get("Image") != image.identity
                or (ready and item["State"].get("Running") is not True)
                or item["State"].get("Status") not in ("running", "exited", "restarting")
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
            if (_environment(config.get("Env") or []) != environment
                    or tuple(config.get("Entrypoint") or ()) != image.entrypoint
                    or tuple(config.get("Cmd") or ()) != tuple(expected.get("command", image.command))):
                raise PlanError("Komodo native fixed configuration differs from selection")


def verify_candidate(containers: dict[str, dict[str, Any]], selected: KomodoSelection,
                     images: dict[str, KomodoImage], profile: bytes, attempt: dict[str, Any], *,
                     ready: bool = True) -> None:
    if not attempt["fixed_profile"] and attempt["before"] != {
        name: native_fingerprint(item) for name, item in containers.items()
    }:
        raise PlanError("Komodo adopted native candidate was replaced or changed")
    verify_native(containers, selected, images, profile,
                  owned=attempt["fixed_profile"], ready=ready)


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

    @staticmethod
    def _baseline(state: KomodoState, selected: KomodoSelection,
                  containers: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if (selected.adoption
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
    def _preserved_material(record: dict[str, Any], selected: KomodoSelection) -> None:
        if (selected.adoption
                and identity_digest(selected.material) != selected.adopted_public_identity):
            raise PlanError("Komodo adopted native identity changed")
        retained = record["failed"] or record["good"]
        if retained is not None and (
            retained["material"] != selected.material_evidence()
            or retained["public_identity"] != identity_digest(selected.material)
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
                    verify_network(network, selected, containers, retained_network(record, selected))
                receipt = journal.accept_named_external(request, resource=selected.resource)
                status = receipt.to_dict()["status"]
                profile = selected.profile()
                if status in ("succeeded", "failed"):
                    self._preserved(record, selected, containers)
                    verify_network(network, selected, containers, retained_network(record, selected))
                    if status == "succeeded":
                        verify_native(containers, selected, images, profile,
                                      owned=record["good"]["fixed_profile"])
                        asyncio.run(current_health(selected.declaration, selected.material, deadline=deadline))
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
                    slot = state.profile(profile)
                    needs_apply = (not containers or record["good"] is not None and (
                        record["good"]["deployment"] != content or set(containers) != set(SERVICES)
                        or any(item["State"].get("Running") is not True for item in containers.values()))
                        or record["failed"] is not None)
                    retained = record["failed"] or record["good"]
                    record["attempt"] = {"request": request.to_dict(), "deployment": content,
                                         "profile": str(slot),
                                         "native_settled": False,
                                         "network_before": verify_network(
                                             network, selected, containers, retained_network(record, selected)),
                                         "fixed_profile": bool(needs_apply or retained and retained["fixed_profile"]),
                                         "before": {name: native_fingerprint(item)
                                                    for name, item in containers.items()}}
                    state.save(record)
                    self._selected(request, content)
                    permit = self.admission.revalidate(permit, request)
                    state.check()
                    if not journal.begin_external(request):
                        raise PlanError("Komodo effect was already accepted by another owner")
                    self.admission.complete(permit, request)
                    verify_network(self.native.network(
                        selected.declaration, selected.material, deadline=deadline),
                        selected, containers, record["attempt"]["network_before"])
                    if needs_apply:
                        try:
                            self.native._apply(selected.declaration, selected.material, profile=slot,
                                               expected=profile, deadline=deadline)
                        finally:
                            if self.native.native_failure is None:
                                record["attempt"]["native_settled"] = True
                                state.save(record)
                    else:
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
                self._preserved_material(record, selected)
                attempt = record["attempt"]
                network = verify_network(self.native.network(
                    selected.declaration, selected.material, deadline=deadline),
                    selected, containers, attempt["network_before"],
                    created=attempt["fixed_profile"] and attempt["native_settled"])
                verify_candidate(containers, selected, images, profile, attempt)
                health = asyncio.run(current_health(selected.declaration, selected.material, deadline=deadline))
                after = self.native.inspect(selected.declaration, selected.material, deadline=deadline)
                if ({name: native_fingerprint(item) for name, item in containers.items()}
                        != {name: native_fingerprint(item) for name, item in after.items()}):
                    raise PlanError("Komodo native candidate changed during readiness inspection")
                verify_network(self.native.network(
                    selected.declaration, selected.material, deadline=deadline), selected, after, network)
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
                self._preserved_material(record, selected)
                profile = selected.profile()
                verify_candidate(containers, selected, images, profile, attempt, ready=False)
                network = verify_network(self.native.network(
                    selected.declaration, selected.material, deadline=deadline),
                    selected, containers, attempt["network_before"], created=attempt["fixed_profile"])
                journal = NodeJournal(
                    state.root / "node.sqlite", authority=self.authority, installation=selected.installation,
                    target=selected.declaration.to_dict()["target"])
                receipt = journal.accept_named_external(request, resource=selected.resource)
                if receipt.to_dict()["status"] != "unknown":
                    raise PlanError("Komodo failed settlement requires the original uncertain attempt")
                permit = self.admission.revalidate(permit, request)
                self.admission.complete(permit, request)
                remaining = deadline - time.monotonic()
                if remaining < 2:
                    raise PlanError("Komodo failure inspection requires remaining native deadline")
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
                    selected.declaration, selected.material, deadline=deadline), selected, after, network)
                if ({name: native_fingerprint(item) for name, item in containers.items()}
                        != {name: native_fingerprint(item) for name, item in after.items()}):
                    raise PlanError("Komodo failed native candidate changed during reconciliation")
                failed = {
                    "deployment": content, "profile": attempt["profile"],
                    "material": selected.material_evidence(), "public_identity": identity_digest(selected.material),
                    "native": {name: native_fingerprint(item) for name, item in containers.items()},
                    "reason": "authenticated-readiness-not-established",
                    "selection": selected.receipt_context(),
                    "network": network, "fixed_profile": attempt["fixed_profile"],
                }
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
