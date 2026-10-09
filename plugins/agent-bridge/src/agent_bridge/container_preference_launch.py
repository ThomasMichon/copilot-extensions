"""Provider-owned container command assembly and optional wrapper capability."""

from __future__ import annotations

import shlex
import json
from typing import Any

from .preference_attestation import LAUNCH_TOKEN, SHELL_MARKER, shell_request, target_digest


def container_child_argv(
    target: dict[str, Any], prepared: dict[str, Any], plugin_dirs: list[str],
    *, acp_command_override: str | None = None, copilot_args: list[str] | None = None,
) -> list[str]:
    template = acp_command_override or str(prepared.get("acp_command") or target["acp_command"])
    wrapper = prepared.get("preference_wrapper")
    enabled = (
        not acp_command_override and isinstance(wrapper, dict)
        and type(wrapper.get("version")) is int
        and wrapper == {"version": 1, "launcher": LAUNCH_TOKEN}
    )
    if LAUNCH_TOKEN in template and not enabled:
        raise ValueError("target preference launcher capability is not advertised")
    if enabled and template.count(LAUNCH_TOKEN) != 1:
        raise ValueError("target preference template requires exactly one launcher token")
    if enabled:
        user, instance = prepared.get("user"), prepared.get("execution_instance")
        if (
            not isinstance(user, str) or not user or not isinstance(instance, str) or not instance
            or prepared.get("name") != target.get("name")
            or target.get("user") != user
        ):
            raise ValueError("target preference launcher lacks selected execution-space binding")
        descriptor = json.dumps({"user": user, "instance": instance}, separators=(",", ":"))
    command = template + "".join(f" --plugin-dir={shlex.quote(path)}" for path in plugin_dirs)
    if copilot_args:
        command += " " + " ".join(shlex.quote(arg) for arg in copilot_args)
    if remote_env := prepared.get("remote_env"):
        path = shlex.quote(str(remote_env))
        command = f". {path}; rm -f {path}; {command}"
    argv = shell_request(command, enabled=bool(enabled))
    return [argv[0], descriptor, *argv[1:]] if enabled else argv


def bind_launch_purpose(
    argv: list[str], preference_source: str, *, preserving: bool,
) -> list[str]:
    if not argv or argv[0] != SHELL_MARKER:
        return argv
    from .acp_preferences import _ACP_MODEL_ENV, _first_env

    preserving = preserving or bool(_first_env(_ACP_MODEL_ENV))
    mode = "defaults" if preference_source == "target-settings" and not preserving else "selection"
    return [argv[0], mode, *argv[1:]]


def verify_launch_receipt(argv: list[str], receipt: Any, expected_instance: str | None) -> None:
    if not argv or argv[0] != SHELL_MARKER:
        return
    descriptor = json.loads(argv[2])
    verify_selected_instance(argv, expected_instance)
    if (
        not isinstance(receipt, dict) or receipt.get("version") != 2
        or (receipt.get("authority") or {}).get("target") != target_digest(descriptor)
        or (receipt.get("authority") or {}).get("mode") != argv[1]
    ):
        raise ValueError("target preference Host did not confirm selected execution-space authority")


def verify_selected_instance(argv: list[str], expected_instance: str | None) -> None:
    if not argv or argv[0] != SHELL_MARKER:
        return
    descriptor = json.loads(argv[2])
    target_digest(descriptor)
    if expected_instance and descriptor["instance"] != expected_instance:
        raise ValueError("target preference execution instance changed without target refresh")
