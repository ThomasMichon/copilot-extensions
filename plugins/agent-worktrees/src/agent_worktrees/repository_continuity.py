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


def _invoke(
    arguments: list[str], *, python: Path | None = None,
    environment: dict[str, str] | None = None, timeout: int = 30,
) -> dict:
    try:
        completed = run_caller_owned(
            [str(python) if python is not None else sys.executable, "-I", "-m", "fleet_manager", *arguments],
            timeout=timeout, max_stdout_bytes=16384, max_stderr_bytes=16384,
            env=protocol._environment(attended=False) | (environment or {}))
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
    ssh_arguments, description = None, None
    vault_invocation = None
    if context.ssh_setup is not None:
        from . import repository_ssh_setup as ssh_setup

        if not {
            ssh_setup.CAPABILITY, ssh_setup.PIN_CAPABILITY, "continuity-machine-reference-v1",
        } | ({ssh_setup.KEY_REFERENCE_CAPABILITY, ssh_setup.KEY_PIN_CAPABILITY}
             if context.ssh_key is not None else set()) <= set(capability["capabilities"]):
            raise protocol.SetupError("Installed Fleet executable lacks selected SSH setup support.")
        ssh_arguments = [
            "ssh-setup", "--request", str(Path(context.ssh_setup).expanduser()),
            "--source", str(Path(context.source).expanduser()),
            "--harness", context.name, "--state-root", str(_state_root(setup, name)),
        ]
        if context.ssh_key is not None:
            ssh_arguments.append("--key-references-only")
        description = ssh_setup._description(_invoke(ssh_arguments))
        if context.ssh_key is not None:
            ssh_setup.key_binding(context, description)
        with ssh_setup.selected_inputs(description):
            setup.verify()
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
    if context.ssh_key is not None and (needed or result == {"status": "ready", "stage": "connected"}):
        key_arguments = [
            *[item for item in ssh_arguments if item != "--key-references-only"],
            "--expected-request-digest", description["request_digest"],
            "--expected-key-fingerprint", context.ssh_key.fingerprint,
        ]
        with ssh_setup.selected_inputs(description) as (module, _selected):
            setup.verify()
            if not needed and ssh_setup.status(description, module):
                if not ssh_setup.same_description(_invoke(key_arguments), description):
                    raise protocol.SetupError("SSH inputs changed during pinned readiness inspection.")
                setup.verify()
                return protocol.Outcome("ready", "Selected SSH application has converged its authenticated directory.")
        if not apply:
            return protocol.Outcome("needed", "Selected SSH application requires its normal installation and owning Vault key.")
        invocation_options = {}
        if context.ssh_key.owner is not None:
            from . import repository_vault

            vault_invocation = repository_vault.selected()
            invocation_options["invocation"] = vault_invocation
        waiting = ssh_setup.prepare_key(setup, context, description, **invocation_options)
        if waiting is not None:
            return waiting
        current = _invoke(key_arguments)
        if not ssh_setup.same_description(current, description):
            raise protocol.SetupError("SSH inputs changed after pinned key installation; preserve the original selection.")
        setup.verify()
    if needed and apply:
        setup.verify()
        if vault_invocation is not None:
            vault_invocation.verify()
        result = _invoke(command + ["--apply"])
    setup.verify()
    if result != {"status": "ready", "stage": "connected"}:
        raise protocol.SetupError("Selected context has not established authenticated outbound access.")
    if ssh_arguments is not None:
        return ssh_setup.run(
            setup, context, ssh_arguments, description, apply=apply,
            **({"invocation": vault_invocation} if vault_invocation is not None else {}),
        )
    return protocol.Outcome(
        "ready", "Selected context has authenticated outbound Gateway access; SSH/lifecycle not changed.")
