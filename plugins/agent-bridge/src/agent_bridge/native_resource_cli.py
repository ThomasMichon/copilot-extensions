"""Native-child helper; requests host resources without a reverse shell/bearer."""

from __future__ import annotations

import os
import time
import uuid

from .native_resources import (
    CAPABILITY, DESCRIPTOR_SCHEMA, ResourceMailbox, read_json_file,
    validate_input, validate_timeout, descriptor as expected_descriptor,
)
from .native_store import NativeError, identifier


def command(args):
    from .client import BridgeClient
    from .native_runtime import NativeRuntime, descendant_of, process_matches

    path = os.environ.get("AGENT_BRIDGE_NATIVE_RESOURCES")
    if not path:
        raise NativeError("resource_capability_unavailable", "No native host resource capability is present", 409)
    descriptor = read_json_file(path)
    execution = identifier(os.environ.get("AGENT_BRIDGE_NATIVE_EXECUTION_ID"))
    generation = identifier(os.environ.get("AGENT_BRIDGE_NATIVE_GENERATION"))
    if (
        descriptor.get("schema") != DESCRIPTOR_SCHEMA or type(descriptor.get("version")) is not int or descriptor.get("version") != 1
        or descriptor.get("capability") != CAPABILITY
        or descriptor.get("executionId") != execution or descriptor.get("generation") != generation
    ):
        raise NativeError("identity_mismatch", "Native resource capability identity is stale or foreign")
    runtime = NativeRuntime()
    current = runtime.status(execution, generation, db=BridgeClient.from_config())
    host = current.get("host") or {}
    if (
        current["state"] != "ready" or not current.get("represented") or not current.get("sessionId")
        or not current.get("activated")
        or process_matches(host.get("child_pid"), host.get("child_start_ticks", ""), host.get("boot_id", "")) is not True
        or not descendant_of(os.getpid(), host["child_pid"])
    ):
        raise NativeError("identity_mismatch", "Host resource caller is not the current registered native child")
    row = runtime.store.get(execution, generation)
    definitions = row["data"].get("resourceDefinitions", {})
    if descriptor != expected_descriptor(execution, generation, definitions):
        raise NativeError("identity_mismatch", "Native resource descriptor differs from registered capability")
    if args.resource_action == "descriptor":
        return descriptor
    name = identifier(args.resource)
    if name not in definitions:
        raise NativeError("resource_forbidden", "Resource is not registered for this execution", 403)
    inputs = read_json_file(args.input_file) if args.input_file else {}
    validate_input(inputs, definitions[name])
    wait = validate_timeout(args.timeout)
    request = {
        "executionId": execution, "generation": generation, "sessionId": current["sessionId"],
        "requestId": identifier(args.request_id or uuid.uuid4().hex), "resource": name, "input": inputs,
    }
    mailbox = ResourceMailbox(runtime.store)
    mailbox.submit(request)
    deadline = time.monotonic() + wait
    while True:
        row = runtime.store.get(execution, generation)
        if row["state"] in {"stopped", "stopping", "rejected"} or row["data"].get("sessionId") != request["sessionId"]:
            raise NativeError("identity_mismatch", "Native execution changed while the resource request was pending")
        result = mailbox.result(execution, generation, request["requestId"])
        if result is not None:
            if result["state"] == "failed":
                error = result["error"]
                raise NativeError(error["code"], error["detail"], 503)
            return result
        if time.monotonic() >= deadline:
            raise NativeError("resource_timeout", f"Host resource request remains recorded; retry --request-id {request['requestId']}", 503)
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))
