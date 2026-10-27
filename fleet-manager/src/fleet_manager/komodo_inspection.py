"""Current status separates accepted work, installed objects and authenticated readiness."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .komodo_declaration import KomodoDeclaration, SERVICES
from .komodo_health import identity_digest
from .komodo_native import KomodoNative
from .komodo_network import retained_network, verify_network
from .komodo_owner import native_fingerprint, native_running, verify_candidate
from .komodo_state import KomodoState
from .node_journal import NodeJournal
from .operations import OperationIntent
from .planning import PlanError
from .private_state import validate_private_file
from .runtime_admission import Permit

if TYPE_CHECKING:
    from .komodo_owner import KomodoOwner


def inspect_owner(owner: KomodoOwner, request: OperationIntent, permit: Permit) -> dict[str, Any]:
    try:
        owner._worker()
        permit = owner.admission.revalidate(permit, request)
        selected = owner._selected(request)
        content = selected.deployment_digest
        if permit.fleet != selected.fleet:
            raise PlanError("Komodo permit fleet differs from selected owner")
        if owner.native is None:
            owner.native = KomodoNative(selected.tooling)
        if owner.native.tooling != selected.tooling:
            raise PlanError("Komodo native tooling changed within its owner")
        deadline = time.monotonic() + min(15, selected.declaration.to_dict()["outfitting_seconds"])
        owner.native.verify_host(selected.declaration, selected.material, deadline=deadline)
        with KomodoState.open(
            selected.host_runtime, daemon=selected.tooling.daemon_id,
            project=selected.declaration.to_dict()["project"],
        ) as state:
            containers = owner.native.inspect(selected.declaration, selected.material, deadline=deadline)
            record = owner._baseline(state, selected, containers, check_identity=False)
            path = state.root / "node.sqlite"
            receipt = None
            attempt = record["attempt"]
            original_receipt = None
            resource_receipt = None
            if path.exists():
                validate_private_file(path)
                journal = NodeJournal(
                    path, authority=owner.authority, installation=selected.installation,
                    target=selected.declaration.to_dict()["target"])
                receipt = journal.inspect(request)
                resource_receipt = journal.inspect_named_external(resource=selected.resource)
                if attempt is not None:
                    original_receipt = journal.inspect(OperationIntent.from_dict(attempt["request"]))
            accepted = receipt.to_dict()["status"] if receipt is not None else None
            retained = record["failed"] or record["good"]
            blockers = []
            installed, healthy = None, None
            observed = None
            observed_declaration = None
            drift_error = None
            fingerprints = {name: native_fingerprint(item) for name, item in containers.items()}
            candidate = (accepted == "unknown" and attempt is not None
                         and attempt["request"] == request.to_dict()
                         and attempt["deployment"] == content and "before" in attempt)
            identity = None
            if set(containers) == set(SERVICES):
                try:
                    identity = identity_digest(selected.material)
                except PlanError as error:
                    blockers.append("retained-public-identity-unavailable")
                    drift_error = str(error)
                if (identity is not None and selected.adoption
                        and identity != selected.adopted_public_identity):
                    blockers.append("retained-storage-credentials-or-identity-changed")
                    identity = None
            if retained is not None and all(
                retained["native"].get(name) == value for name, value in fingerprints.items()
            ):
                installed = set(containers) == set(SERVICES)
                observed = retained["deployment"]
                if installed and retained.get("selected_native") is False:
                    blockers.append("failed-native-candidate-incomplete-or-mixed")
                elif installed and identity is not None:
                    if (retained["material"] != selected.material_evidence()
                            or retained["public_identity"] != identity):
                        blockers.append("retained-storage-credentials-or-identity-changed")
                    else:
                        observed_declaration = KomodoDeclaration.from_dict(
                            retained["selection"]["declaration"])
            elif containers and candidate:
                try:
                    images = owner.native.selected_images(
                        selected.declaration, selected.material, deadline=deadline)
                    verify_candidate(containers, selected, images, selected.profile(),
                                     attempt, ready=False)
                except PlanError as error:
                    if owner.native.native_failure is not None:
                        raise
                    blockers.append("native-resources-differ-from-current-or-retained-owner")
                    drift_error = str(error)
                else:
                    installed, observed = True, content
                    if identity is not None:
                        observed_declaration = selected.declaration
            elif not containers:
                installed = False
            else:
                blockers.append("native-resources-differ-from-current-or-retained-owner")
            network = None
            try:
                network = verify_network(owner.native.network(
                    selected.declaration, selected.material, deadline=deadline),
                    selected, containers,
                    attempt["network_before"] if candidate else retained_network(record, selected),
                    created=bool(candidate and attempt["fixed_profile"]),
                    pending=bool(retained is not None and "partial_public_keys" in retained))
            except PlanError as error:
                if owner.native.native_failure is not None:
                    raise
                blockers.append("native-network-custody-changed-or-unproved")
                drift_error = str(error)
                observed_declaration = None
            detail = None
            absent = selected.declaration.to_dict().get("desired_state", "present") == "absent"
            if installed is False and not absent:
                blockers.append("owned-substrate-resources-absent-or-incomplete")
            if observed_declaration is not None and not native_running(containers["core"]):
                blockers.append("owned-core-not-running")
            elif observed_declaration is not None and not all(map(native_running, containers.values())):
                blockers.append("owned-substrate-not-running")
            elif observed_declaration is not None:
                try:
                    owner._ready(selected, containers, network, deadline=deadline,
                                 declaration=observed_declaration, identity=identity)
                except PlanError as error:
                    healthy, detail = False, str(error)
                    blockers.append("authenticated-current-health-unavailable")
                else:
                    healthy = True
            unresolved = (resource_receipt is not None and resource_receipt.to_dict()["status"]
                          not in ("succeeded", "failed")) or accepted == "unknown" or (attempt is not None and (
                original_receipt is None
                or original_receipt.to_dict()["status"] not in ("succeeded", "failed")))
            retired = (absent and accepted == "succeeded" and not containers
                       and record["retirement"] is not None
                       and record["retirement"]["deployment"] == content and not unresolved and not blockers)
            converged = retired or (
                not absent and accepted == "succeeded" and observed == content
                and healthy is True and not blockers and not unresolved)
            owner._selected(request, content)
            owner.admission.complete(permit, request)
            return {
                "schema": "fleet-manager.komodo-status", "version": 1,
                "desired": content, "accepted": accepted,
                "state": ("retired" if retired else "converged" if converged else "unresolved" if unresolved
                          else "drift" if blockers else "not-converged"),
                "observed": observed, "installed": installed, "currently_healthy": healthy,
                "converged": converged, "unresolved_effects": unresolved,
                "retained_good": record["good"]["deployment"] if record["good"] else None,
                "previous_good": record["previous_good"]["deployment"] if record["previous_good"] else None,
                "failed_candidate": record["failed"]["deployment"] if record["failed"] else None,
                "blockers": blockers, "health_error": detail, "drift_error": drift_error,
                "native_mutations_performed": False,
            }
    finally:
        owner.admission.discard(permit)
