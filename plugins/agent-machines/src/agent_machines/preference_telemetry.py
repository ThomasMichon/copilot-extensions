"""Accepted defaults reach only a capable, pinned native configuration owner."""

from __future__ import annotations

import hmac
import os
import re
from pathlib import Path

from agent_procutil.owned_process import run_caller_owned
from configuration_context import context_from_owner

from . import _peer_launch, configuration, preferences as codec
from .preference_exchange import _admitted, _distinct, _plain_path, _state

PRODUCERS = ("agent-bridge", "agent-dispatch")
NATIVE_SCHEMA = "process-telemetry.retained-configuration/v1"


def _prefix(producer: str) -> list[str]:
    root = Path(os.environ.get("AGENT_RT_ROOT") or (Path.home() / ".agent-machines"))
    raw = os.environ.get(_peer_launch.CONTEXT_ENV, "")
    if not raw:
        raise codec.PreferenceError("native-producer-unavailable")
    try:
        prefix, _python = _peer_launch.pinned_launch_prefix("agent-machines", root, raw, producer)
    except ValueError as error:
        raise codec.PreferenceError("native-producer-unavailable") from error
    return prefix


def _call(prefix: list[str], arguments: list[str], *, capability=False) -> dict:
    result = run_caller_owned(
        [*prefix, "telemetry", *arguments], timeout=25,
        max_stdout_bytes=64 * 1024, max_stderr_bytes=64 * 1024,
        env=dict(os.environ),
    )
    try:
        value = codec.decode(result.stdout.encode("utf-8"))
    except (codec.PreferenceError, AttributeError) as error:
        raise codec.PreferenceError("native-producer-unavailable") from error
    if not isinstance(value, dict) or value.get("schema") != NATIVE_SCHEMA:
        raise codec.PreferenceError("native-contract-unsupported")
    try:
        codec.digest(value)
    except (ValueError, TypeError) as error:
        raise codec.PreferenceError("native-response-invalid") from error
    if capability:
        if (
            result.returncode != 0
            or value.get("configuration_only") is not True
            or value.get("custom_sink_overwrite") is not False
            or type(value.get("input_fence")) is not int or value["input_fence"] != 1
            or not isinstance(value.get("operations"), list)
            or any(not isinstance(item, str) for item in value["operations"])
            or not {"preview", "apply"} <= set(value["operations"])
        ):
            raise codec.PreferenceError("native-contract-unsupported")
    elif (
        result.returncode not in (0, 2)
        or not isinstance(value.get("status"), str)
        or value["status"] not in {"preview", "refused", "error", "configured"}
        or value.get("activation") != "not-requested"
        or (value["status"] in {"preview", "configured"}) != (result.returncode == 0)
    ):
        raise codec.PreferenceError("native-response-invalid")
    return value


def _plan(state: Path, local: Path | None, producer: str, directory: Path) -> tuple[dict, list[str], list[str]]:
    if producer not in PRODUCERS:
        raise codec.PreferenceError("native-producer-unsupported")
    policy, paths = _state(state)
    if "telemetry" not in policy["domains"]:
        raise codec.PreferenceError("domain-not-permitted")
    fenced_policy, policy_hash = codec.read_snapshot(paths[0], validator=codec.validate_policy)
    accepted, accepted_hash = codec.read_snapshot(paths[2])
    if policy != fenced_policy:
        raise codec.PreferenceError("preview-changed")
    if accepted is None:
        raise codec.PreferenceError("accepted-state-required")
    if not _admitted(accepted, policy):
        raise codec.PreferenceError("domain-not-permitted")
    inputs = [[str(paths[0]), policy_hash], [str(paths[2]), accepted_hash]]
    overrides = None
    if local is not None:
        local = _plain_path(local)
        overrides, local_hash = codec.read_snapshot(local, local=True)
        inputs.append([str(local), local_hash])
    _distinct([*paths, *([local] if local is not None else [])])
    report = configuration.owner_report(Path.cwd())
    if report is None:
        raise codec.PreferenceError("native-context-unavailable")
    context = context_from_owner(report)
    projection = codec.preview(
        None, accepted, publisher=policy["publisher"], machine=context["execution_machine"],
        harness=context["repository_id"], local=overrides,
    )
    choices = projection["values"].get("telemetry", {})
    arguments = ["retained", "--directory", str(directory)]
    for path, digest in inputs:
        arguments.extend(["--if-input", path, digest])
    result = {
        "schema": "agent-machines.preferences.telemetry", "version": 1,
        "producer": producer, "accepted_revision": accepted["revision"], "choices": choices,
        "scope": {"machine": context["execution_machine"], "harness_repository_id": context["repository_id"]},
        "provenance": {
            key: value for key, value in projection["provenance"].items() if key.startswith("telemetry.")
        },
        "eligible": True, "status": "no-contribution", "configuration_applied": False,
        "activation": "not-requested", "reporting_adoption": "unsupported",
        "running_capture": "not-observed",
    }
    prefix = []
    if choices.get("capture") == "retained-local":
        prefix = _prefix(producer)
        _call(prefix, ["capabilities"], capability=True)
        native = _call(prefix, arguments)
        result.update(native=native, eligible=native["status"] == "preview", status=native["status"])
        if result["eligible"]:
            token = native.get("confirmation")
            if not isinstance(token, str) or re.fullmatch(r"[a-f0-9]{64}", token) is None:
                raise codec.PreferenceError("native-response-invalid")
    result["preview_token"] = codec.digest({
        "policy": policy, "accepted": accepted, "local": overrides, "context": context,
        "prefix": prefix, "arguments": arguments, "result": result,
    })
    return result, prefix, arguments


def adopt(
    state: Path, *, producer: str, directory: Path, local: Path | None = None,
    apply=False, expected="",
) -> dict:
    if apply and re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise codec.PreferenceError("preview-token-required")
    result, prefix, arguments = _plan(state, local, producer, directory)
    if not apply:
        return result
    if not hmac.compare_digest(expected, result["preview_token"]):
        raise codec.PreferenceError("preview-changed")
    if not result["eligible"] or result["status"] == "no-contribution":
        return result
    native = _call(prefix, [
        *arguments, "--apply", "--confirm", result["native"]["confirmation"],
    ])
    if native["status"] != "configured":
        known_refusal = native["status"] == "refused" or native.get("write_outcome") == "not-applied"
        return {
            **result, "eligible": False, "status": "refused" if known_refusal else "unknown",
            "configuration_applied": False if known_refusal else None, "native": native,
        }
    if type(native.get("changed")) is not bool:
        raise codec.PreferenceError("native-response-invalid")
    return {
        **result, "native": native, "configuration_applied": True,
        "status": "configured", "changed": native["changed"],
    }
