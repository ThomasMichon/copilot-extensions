"""Negotiate configured routing with the exact engine selected for execution."""

from __future__ import annotations

from collections.abc import Mapping


def require_context(
    project: str | None, arguments: list[str], command: list[str],
    environment: Mapping[str, str],
) -> None:
    if arguments[:1] != ["update"] or "--auth-report" in arguments:
        return
    if not environment.get("COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST"):
        return
    from . import engine_client

    if (
        environment["COPILOT_EXTENSIONS_TOOLCHAIN_REQUEST"] != "1"
        or not environment.get("COPILOT_EXTENSIONS_TOOLCHAIN_CONFIG")
    ):
        raise engine_client.EngineError("Unsupported or incomplete toolchain request context")
    try:
        inventory = engine_client.run_json(
            project, ["update", "--inventory", "--json"],
            engine_command=command, environment=dict(environment),
        )
    except engine_client.EngineError as error:
        raise engine_client.EngineError(
            "Cannot establish toolchain request context v1 support from the selected engine; "
            f"no update was run. Upgrade that engine through setup/recovery first. {error}"
        ) from error
    capability = inventory.get("toolchain_request_context")
    if not isinstance(capability, dict) or (
        capability.get("schema") != "copilot-extensions.toolchain-request-context"
        or type(capability.get("version")) is not int or capability["version"] != 1
    ):
        raise engine_client.EngineError(
            "Selected engine lacks toolchain request context v1; no update was run. "
            "Upgrade that engine through setup/recovery first."
        )
