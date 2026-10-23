"""Explicit retained-good authority, shared by selector and command boundaries."""

import os
from pathlib import Path
import re
import time

from . import client_admission, client_construction as construction
from .planning import PlanError, canonical, decode, digest, identifier, integer, record

CONTEXT = "retained-recovery.json"
ACKNOWLEDGED = "acknowledged.json"
WITHDRAWN = "client-withdrawn.json"
FIELDS = {
    "identity", "owner", "decision", "revision", "admitted_digest", "expected_active",
    "expected_withdrawal", "retained_active", "completion_digest", "target_digest",
    "command_directory",
}


def _decision(current_recovery, identity):
    from . import client_activation as activation

    if not callable(current_recovery):
        raise PlanError("retained client recovery requires a current attributed local decision")
    value = decode(canonical(current_recovery()))
    record(value, "fleet-manager.client-recovery-decision", FIELDS)
    for name in ("owner", "decision"):
        identifier(value[name], "client recovery " + name)
    integer(value["revision"], "client recovery revision", minimum=1)
    if value["identity"] != identity or value["owner"] != identity["selection_owner"]:
        raise PlanError("client recovery decision has a different installation or owner")
    for name in ("admitted_digest", "completion_digest", "target_digest"):
        if not isinstance(value[name], str) or re.fullmatch("[0-9a-f]{64}", value[name]) is None:
            raise PlanError("client recovery decision requires exact evidence digests")
    activation._state(value["retained_active"], identity)
    if value["expected_active"] is not None:
        activation._state(value["expected_active"], identity)
        if value["expected_withdrawal"] is not None:
            raise PlanError("client recovery must expect either active or settled withdrawn state")
    elif (
        not isinstance(value["expected_withdrawal"], str)
        or re.fullmatch("[0-9a-f]{64}", value["expected_withdrawal"]) is None
    ):
        raise PlanError("client recovery requires an exact settled withdrawal")
    directory = value["command_directory"]
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        raise PlanError("client recovery requires an explicit absolute command directory")
    return value


def _context(namespace, active, decision):
    from . import client_activation as activation

    directory = namespace / activation.HISTORY / active["publication"]
    return construction._read(directory / CONTEXT) == decision


def _historical(namespace, identity, completion, decision, publication):
    from . import client_activation as activation

    retained = activation._state(decision["retained_active"], identity)
    if (
        retained["candidate"] != decision_candidate(completion)
        or retained["runtime_sha256"] != completion["runtime_sha256"]
        or digest(completion) != decision["completion_digest"]
    ):
        raise PlanError("client recovery decision differs from the exact retained completion")
    directory, _entries, proofs, _attempts = activation._proofs(namespace, retained, publication)
    intent = construction._read(directory / "intent.json")
    record(intent, "fleet-manager.client-selector-intent", {"active", "previous", "before"})
    if (
        intent["active"] != retained
        or construction._read(directory / ACKNOWLEDGED) != retained
    ):
        raise PlanError("retained client has no exact acknowledged healthy activation")
    for target, expected in activation._expected(namespace, retained["candidate"]):
        if not any(state.digest == expected for state in proofs.get(Path(target), ())):
            raise PlanError("retained client lacks complete historical selector proofs")


def decision_candidate(completion):
    selection = completion["input"]["selection"]
    return f"client-{selection['sequence']}-{selection['release_digest']}"


def _withdrawal(namespace, identity, expected):
    from . import client_deactivation as deactivation

    value = construction._read(namespace / WITHDRAWN)
    record(value, "fleet-manager.client-deactivation-intent", deactivation.FIELDS)
    if value["identity"] != identity or not isinstance(value["removal"], str) or (
        re.fullmatch("[0-9a-f]{32}", value["removal"]) is None
    ):
        raise PlanError("client recovery has an invalid settled withdrawal")
    if digest(value) != expected or construction._read(
        namespace / deactivation.HISTORY / value["removal"] / "settled.json",
    ) != value:
        raise PlanError("client recovery expected a different settled withdrawal")


def _checkpoint(connection, identity, selection, binding, staged, current_policy,
                current_target, clock, unchanged, *, namespace, primitive, publication,
                decision, current_recovery, completion, require_active=False):
    from . import client_activation as activation

    unchanged()
    if _decision(current_recovery, identity) != decision:
        raise PlanError("client recovery decision changed or was revoked")
    admitted = client_admission._read_state(connection, identity)
    if not admitted or digest(admitted) != decision["admitted_digest"] or (
        admitted["sequence"] <= selection["sequence"]
    ):
        raise PlanError("client recovery requires its exact unchanged higher admitted floor")
    policy, target = construction._verified(
        identity, selection, staged, current_policy, current_target, clock,
    )
    if digest(target) != decision["target_digest"]:
        raise PlanError("client recovery requires its exact current compatible target")
    activation._not_removing(namespace)
    version = decision["retained_active"]["candidate"]
    construction._completed_intent(namespace, version, binding)
    if construction._read(primitive.version_dir(namespace, version) / construction.COMPLETION) != completion or (
        primitive.check_admission(namespace, version, payload_hash=digest(completion)) != primitive.ADMIT_REUSE
    ):
        raise PlanError("client retained completion changed during recovery")
    _historical(namespace, identity, completion, decision, publication)
    active = (construction._read(namespace / activation.ACTIVE)
              if os.path.lexists(namespace / activation.ACTIVE) else None)
    pending = (construction._read(namespace / activation.PENDING)
               if os.path.lexists(namespace / activation.PENDING) else None)
    if active is not None:
        activation._state(active, identity)
    if decision["expected_active"] is None:
        _withdrawal(namespace, identity, decision["expected_withdrawal"])
    recovered = active is not None and (
        active["candidate"] == version
        and active["runtime_sha256"] == completion["runtime_sha256"]
        and active["target_digest"] == decision["target_digest"]
        and os.path.lexists(namespace / activation.HISTORY / active["publication"] / CONTEXT)
        and _context(namespace, active, decision)
    )
    if active != decision["expected_active"] and not recovered:
        raise PlanError("client recovery expected a different active owner")
    if require_active and (not recovered or pending is not None):
        raise PlanError("client commands require the exact settled recovered active context")
    if pending is not None:
        activation._state(pending, identity)
        if (
            pending["candidate"] != version
            or pending["runtime_sha256"] != completion["runtime_sha256"]
            or pending["target_digest"] != decision["target_digest"]
            or not _context(namespace, pending, decision)
        ):
            raise PlanError("client recovery has a foreign pending selector owner")
        intent = construction._read(
            namespace / activation.HISTORY / pending["publication"] / "intent.json",
        )
        if intent["previous"] != decision["expected_active"]:
            raise PlanError("client recovery pending intent has a different prior owner")
    unchanged()
    return policy, target, admitted


def _command_attempt(namespace, active, decision):
    from . import client_activation as activation

    directory = namespace / activation.HISTORY / active["publication"]
    names = [f"recovery-command.{index}.json" for index in range(activation.MAX_ATTEMPTS)]
    found = {path.name for path in directory.iterdir() if path.name.startswith("recovery-command.")}
    if len(found) >= len(names) or found != set(names[:len(found)]):
        raise PlanError("client recovery command attempts are invalid or exhausted")
    for name in names[:len(found)]:
        if construction._read(directory / name) != decision:
            raise PlanError("client recovery command attempt has a different decision")
    construction._write(directory / names[len(found)], decision)


def _activation_health(handoff, ownership, active, identity, completion, target, admitted, decision):
    custody, selected, selected_active = handoff
    expected = {
        "schema": "fleet-manager.client-runtime-activation", "version": 1,
        "identity": identity, "candidate": active["candidate"], "active": True,
        "runtime_sha256": completion["runtime_sha256"], "target_digest": digest(target),
        "admitted": admitted, "recovery_decision_digest": digest(decision),
    }
    if (
        ownership is None or custody is not ownership or selected_active != active
        or not isinstance(selected, dict)
        or any(selected.get(name) != value for name, value in expected.items())
        or not isinstance(selected.get("health"), dict)
    ):
        raise PlanError("client recovery health has a different owned activation context")
    return decode(canonical(selected["health"]))


def recover_client_release(root, identity, selection, staged, *, command_directory,
                           current_recovery, current_policy, current_target,
                           clock=lambda: int(time.time())):
    """Recover selectors and commands, never admit or reconstruct an older release."""
    from . import client_activation as activation, client_frontdoor as frontdoor

    identity = client_admission._identity(identity)
    decision = _decision(current_recovery, identity)
    command_directory = Path(command_directory)
    if not command_directory.is_absolute():
        raise PlanError("client recovery requires an explicit absolute command directory")
    if str(command_directory) != decision["command_directory"]:
        raise PlanError("client recovery command directory differs from its decision")
    selected = None
    try:
        with client_admission._owned(root) as ownership:
            selected = activation._activate_client_runtime(
                root, identity, selection, staged, current_policy=current_policy,
                current_target=current_target, clock=clock, recovery=decision,
                current_recovery=current_recovery,
                ownership=ownership,
            )
            try:
                handoff = (
                    ownership, selected,
                    construction._read(ownership[0] / construction.NAMESPACE / activation.ACTIVE),
                )
                commands = frontdoor._register_client_frontdoor(
                    root, identity, selection, staged, command_directory=command_directory,
                    current_policy=current_policy, current_target=current_target, clock=clock,
                    current_recovery=current_recovery, ownership=ownership,
                    activation_health=handoff,
                )
            except PlanError as error:
                raise PlanError(
                    "retained client selectors recovered; command outcome is not settled; "
                    "preserve evidence and use exact owned command withdrawal before retry if pending: "
                    + str(error)
                ) from None
    except PlanError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise PlanError(
            "client retained recovery outcome is unknown; preserve owned lifecycle evidence"
            if selected is not None else
            "client retained recovery could not establish owned installation custody"
        ) from None
    return {
        "schema": "fleet-manager.client-retained-recovery", "version": 1,
        "identity": identity, "admitted": commands["admitted"],
        "recovered_active": decision["retained_active"]["candidate"],
        "recovery_decision_digest": digest(decision),
        "active": True, "frontdoor_registered": True,
        "commands": commands["commands"], "health": commands["health"],
        "reused": selected["reused"] and commands["reused"],
        "mutations_performed": selected["mutations_performed"] or commands["mutations_performed"],
        "admission_modified": False, "constructed": False,
        "path_modified": False, "live_readiness_verified": False,
    }
