"""Optional normal setup consumer of the selected Fleet runtime, not an installer."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import sys

from agent_procutil.owned_process import run_caller_owned

from . import repository_setup as protocol

CAPABILITY = "delegated-machine-enrollment-v1"


def _invoke(arguments: list[str]) -> dict:
    try:
        completed = run_caller_owned(
            [sys.executable, "-I", "-m", "fleet_manager", *arguments],
            timeout=30, max_stdout_bytes=16384, max_stderr_bytes=16384,
            env=protocol._environment(attended=False))
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError):
        raise protocol.SetupError(
            "Continuity runtime execution is unsupported, refused or unsettled; retained state was preserved."
        ) from None
    if completed.returncode:
        raise protocol.SetupError(
            "Continuity runtime refused or has an unknown outcome; original machine state was retained.")
    try:
        value = json.loads(completed.stdout, object_pairs_hook=protocol._object)
    except (UnicodeError, json.JSONDecodeError):
        raise protocol.SetupError("Continuity runtime returned an invalid result.") from None
    if not isinstance(value, dict):
        raise protocol.SetupError("Continuity runtime returned an invalid result.")
    return value


def selected(setup: protocol.Setup, name: str) -> protocol.ContinuityContext:
    context = next((item for item in setup.contexts if item.name == name), None)
    if context is None:
        raise protocol.SetupError("Selected context is not declared by this exact harness revision.")
    return context


def _state_root(setup: protocol.Setup, name: str) -> Path:
    identity = json.dumps([setup.source.url, name], separators=(",", ":")).encode("utf-8")
    return Path.home() / (".fleet-continuity-" + hashlib.sha256(identity).hexdigest())


def run(setup: protocol.Setup, name: str, *, apply: bool) -> protocol.Outcome:
    """Verify the trusted selection and actual executable before source/key access."""
    context = selected(setup, name)
    setup.verify()
    capability = _invoke(["capabilities"])
    if (set(capability) != {"schema", "version", "capabilities"}
            or capability["schema"] != "fleet-manager.capabilities"
            or type(capability["version"]) is not int or capability["version"] != 1
            or not isinstance(capability["capabilities"], list)
            or not all(isinstance(item, str) for item in capability["capabilities"])
            or not {CAPABILITY, context.provider} <= set(capability["capabilities"])):
        raise protocol.SetupError("Installed Fleet executable does not support this selected context.")
    command = [
        "bootstrap", "--source", str(Path(context.source).expanduser()),
        "--harness", context.name, "--state-root", str(_state_root(setup, name)),
    ]
    result = _invoke(command)
    protocol._fields(result, {"status", "stage"})
    needed = result in (
        {"status": "needed", "stage": "enrollment"}, {"status": "needed", "stage": "connection"})
    if needed and not apply:
        return protocol.Outcome("needed", "Selected context needs machine enrollment.")
    if needed and apply:
        setup.verify()
        result = _invoke(command + ["--apply"])
    setup.verify()
    if result != {"status": "ready", "stage": "connected"}:
        raise protocol.SetupError("Selected context has not established authenticated outbound access.")
    return protocol.Outcome(
        "ready", "Selected context has authenticated outbound Gateway access; SSH/lifecycle not changed.")
