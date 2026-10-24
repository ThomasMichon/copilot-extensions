"""Plugin preparation context over the existing authenticated privileged ACP relay."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .spawn_target import REQUEST_OVERRIDES_KEY, SpawnTarget

META_KEY = "agent-bridge/privileged-launch"
CONTEXT_KEY = "privileged_plugin_context"
_MAX_CONTEXT_BYTES = 256 * 1024


class PrivilegedLaunchRefused(RuntimeError):
    """A required privileged preparation contract cannot be honored safely."""


class PrivilegedContext(BaseModel):
    """Optional versioned wire metadata; unknown additive peer fields are ignored."""

    version: Literal[1]
    agent: str = Field(min_length=1, max_length=1024)
    controller_cwd: str | None
    controller_origin: Literal["explicit", "affinity"]
    caller_worktree: str | None = None
    caller_owner_ref: str | None = None
    caller_id: str | None = None
    caller_session_id: str | None = None
    copilot_args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    mcp_servers: list[dict[str, Any]] | None = None
    model: str | None = None
    effort: str | None = None

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported privileged context version")
        return value

    def digest(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_CONTEXT_BYTES:
            raise ValueError("Privileged launch context is too large")
        return hashlib.sha256(encoded).hexdigest()


def relay_agent(target: SpawnTarget) -> str | None:
    """Recognize only the existing Windows privileged relay, not opaque commands."""
    from . import elevated

    if target.type != "command" or not target.elevated:
        return None
    return elevated.relay_agent_for(target.spawn_command)


def capabilities() -> dict[str, Any]:
    """Only the actual privileged receiver advertises this preparation owner."""
    from . import elevated

    return {
        "field_meta": {META_KEY: {"version": 1, "repository_hosting_version": 1}},
    } if elevated.is_subdaemon() else {}


async def start_client(client: Any, process: Any, session: Any) -> None:
    """Bind verified source context before session creation, not ambient state."""
    agent = relay_agent(session.target)
    await client.start(process)
    if agent is not None:
        _require_capability(client)
        from .venue_plugins import resolve_controller_cwd
        from .session_preferences import CONTEXT_ENV, SOURCE_ENV

        target = session.target
        request = (target.venue or {}).get(REQUEST_OVERRIDES_KEY, {})
        request_environment = {
            **request.get("env", {}),
            **{key: target.env[key] for key in (SOURCE_ENV, CONTEXT_ENV) if key in target.env},
        }
        context = PrivilegedContext(
            version=1,
            agent=agent,
            controller_cwd=await resolve_controller_cwd(
                target, caller_session_id=session.caller_session_id,
            ),
            controller_origin=request.get(
                "controller_worktree_origin",
                "explicit" if target.caller_worktree is not None else "affinity",
            ),
            caller_worktree=target.caller_worktree,
            caller_owner_ref=target.caller_owner_ref,
            caller_id=session.caller_id,
            caller_session_id=session.caller_session_id,
            copilot_args=request.get("copilot_args", []),
            env=request_environment,
            mcp_servers=request.get("mcp_servers"),
            model=session.model_override,
            effort=session.effort_override,
        )
        context.digest()
        client._privileged_launch_context = context


def _require_capability(client: Any) -> None:
    result = getattr(client, "_initialize_result", None)
    metadata = getattr(result, "field_meta", None)
    capability = metadata.get(META_KEY) if isinstance(metadata, dict) else None
    if not isinstance(capability, dict) or type(capability.get("version")) is not int or capability["version"] != 1:
        raise PrivilegedLaunchRefused(
            "Privileged receiver lacks plugin preparation capability; no session was requested"
        )
    if type(capability.get("repository_hosting_version")) is not int or capability["repository_hosting_version"] != 1:
        raise PrivilegedLaunchRefused(
            "Privileged receiver lacks repository hosting enforcement; no session was requested"
        )


async def lifecycle_rpc(client: Any, method: Any, **params: Any) -> Any:
    """Keep ordinary ACP packets unchanged and require an exact privileged receipt."""
    context = getattr(client, "_privileged_launch_context", None)
    if context is None:
        return await client._lifecycle_rpc(method, **params)
    _require_capability(client)
    from acp.exceptions import RequestError

    try:
        result = await client._lifecycle_rpc(
            method, **params, **{META_KEY: context.model_dump(mode="json")},
        )
    except RequestError as exc:
        raise PrivilegedLaunchRefused("Privileged receiver refused the required launch context") from exc
    metadata = getattr(result, "field_meta", None)
    receipt = metadata.get(META_KEY) if isinstance(metadata, dict) else None
    if (
        not isinstance(receipt, dict) or receipt.get("digest") != context.digest()
        or type(receipt.get("version")) is not int or receipt["version"] != 1
    ):
        raise PrivilegedLaunchRefused("Privileged receiver did not confirm the required preparation")
    from .acp_preferences import AcpPreferencesMixin
    from .session_preferences import SOURCE_ENV

    if context.env.get(SOURCE_ENV) == "target-settings":
        selected = AcpPreferencesMixin._selected_preferences(getattr(result, "config_options", None))
        if not selected.get("model") or receipt.get("preferences") != selected:
            raise PrivilegedLaunchRefused("Privileged receiver did not confirm its verified preferences")
        # The receiver already applied the request in its actual execution
        # profile. Standard ACP readback must preserve that selection rather
        # than resolving unprivileged defaults a second time.
        client.confirmed_preferences = selected
    return result


def read_context(metadata: dict[str, Any], agent: str | None) -> PrivilegedContext | None:
    """Validate only our namespace, keeping unrelated ACP metadata additive."""
    if META_KEY not in metadata:
        return None
    from acp.exceptions import RequestError

    from . import elevated

    try:
        if not elevated.is_subdaemon():
            raise ValueError("not a privileged receiver")
        raw = metadata[META_KEY]
        if len(json.dumps(raw).encode("utf-8")) > _MAX_CONTEXT_BYTES:
            raise ValueError("context too large")
        context = PrivilegedContext.model_validate(raw)
        context.digest()
        if context.agent != agent or (context.controller_origin == "explicit" and context.controller_cwd is None):
            raise ValueError("context identity differs")
        if context.controller_cwd is not None:
            path = Path(context.controller_cwd)
            if not path.is_absolute() or not path.is_dir():
                raise ValueError("controller source unavailable")
        return context
    except (OSError, TypeError, ValueError) as exc:
        raise RequestError.invalid_params("Privileged launch context is invalid or unavailable") from exc


def apply_context(target: SpawnTarget, context: PrivilegedContext) -> SpawnTarget:
    """Retain receiver defaults and apply only explicitly requested overrides."""
    require_local_receiver(target)
    return replace(
        target, env={**target.env, **context.env}, local_plugin_preparation=True,
        caller_worktree=context.caller_worktree, caller_owner_ref=context.caller_owner_ref,
        venue={
            **(target.venue or {}),
            REQUEST_OVERRIDES_KEY: {
                **(target.venue or {}).get(REQUEST_OVERRIDES_KEY, {}),
                "controller_worktree_origin": context.controller_origin,
                "controller_session_id": context.caller_session_id,
            },
            CONTEXT_KEY: {
                "digest": context.digest(), "controller_cwd": context.controller_cwd,
            },
        },
    )


def require_local_receiver(target: SpawnTarget) -> None:
    """A requires-admin label alone never establishes execution identity."""
    from . import elevated

    if (
        not elevated.is_process_elevated() or not target.elevated or target.type != "local"
        or target.static_plugin_args is None or target.spawn_command
        or target.codespace or target.container
    ):
        raise PrivilegedLaunchRefused("Privileged plugin preparation requires the actual local elevated owner")


def controller_source(target: SpawnTarget) -> tuple[bool, str | None]:
    """Producer filesystem authority is separate from receiver-owned allocation."""
    metadata = (target.venue or {}).get(CONTEXT_KEY)
    if metadata is None:
        return False, None
    require_local_receiver(target)
    path = metadata.get("controller_cwd")
    if path is not None and (not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_dir()):
        raise PrivilegedLaunchRefused("Privileged controller source is no longer accessible")
    return True, path


def receipt(session: Any, context: PrivilegedContext) -> dict[str, Any]:
    """An incumbent is reusable only with this context and actual preparation."""
    require_local_receiver(session.target)
    bound = (session.target.venue or {}).get(CONTEXT_KEY, {})
    prepared = session.target.prepared_plugins
    if (
        bound.get("digest") != context.digest() or not isinstance(prepared, dict)
        or prepared.get("applicable") is False or not isinstance(prepared.get("cwd"), str)
        or not isinstance(prepared.get("plugins"), list) or not isinstance(prepared.get("logical_payloads"), dict)
        or not isinstance(prepared.get("execution_cwd"), str)
        or Path(prepared["execution_cwd"]).resolve() != Path(session.target.cwd or "").resolve()
    ):
        raise PrivilegedLaunchRefused("Privileged session has no matching prepared launch; refusing adoption")
    result: dict[str, Any] = {"version": 1, "digest": context.digest()}
    options = preference_options(session, context)
    if options is not None:
        from .acp_preferences import AcpPreferencesMixin

        result["preferences"] = AcpPreferencesMixin._selected_preferences(options)
    return {META_KEY: result}


def preference_options(session: Any, context: PrivilegedContext) -> list[Any] | None:
    """Expose only the receiver's completed target-settings readback."""
    from .session_preferences import SOURCE_ENV

    if context.env.get(SOURCE_ENV) != "target-settings":
        return None
    return _verified_receiver_options(session.client)


def _verified_receiver_options(client: Any) -> list[Any]:
    from .acp_preferences import AcpPreferencesMixin

    if (
        client is None or client.preference_source != "target-settings"
        or client._preferences_ready is not True
    ):
        raise PrivilegedLaunchRefused("Privileged receiver preferences are not verified")
    options = client._verified_options
    if not isinstance(options, list):
        raise PrivilegedLaunchRefused("Privileged receiver preference readback is incomplete")
    selected = AcpPreferencesMixin._selected_preferences(options)
    if not selected.get("model") or selected != client.confirmed_preferences:
        raise PrivilegedLaunchRefused("Privileged receiver preference readback is incomplete")
    return options


def require_bound_session(session: Any, context: PrivilegedContext) -> None:
    """Refuse legacy/mismatched incumbents before resume can start anything."""
    require_local_receiver(session.target)
    bound = (session.target.venue or {}).get(CONTEXT_KEY, {})
    if bound.get("digest") != context.digest():
        raise PrivilegedLaunchRefused("Privileged session belongs to a different or legacy launch context")


def _host_identity(host: Any) -> dict[str, Any]:
    return {
        "session_id": host.session_id, "host_pid": host.host_pid, "child_pid": host.child_pid,
        "nonce_sha256": hashlib.sha256(host.nonce.encode()).hexdigest(),
    }


def _wire_options(client: Any) -> list[dict[str, Any]]:
    return [
        option.model_dump(mode="json", by_alias=True) if isinstance(option, BaseModel) else deepcopy(option)
        for option in _verified_receiver_options(client)
    ]


def remember_host_launch(db: Any, target: SpawnTarget, client: Any, host: Any, acp_id: str) -> None:
    """Retain prior launch evidence, bound to the host that actually started."""
    from .session_preferences import SOURCE_ENV

    bound = (target.venue or {}).get(CONTEXT_KEY)
    if bound is None:
        return
    require_local_receiver(target)
    if not host.nonce or host.boundary != "local" or not isinstance(target.prepared_plugins, dict):
        raise PrivilegedLaunchRefused("Privileged host launch evidence is unavailable")
    bound["prepared_launch"] = {
        "digest": bound["digest"], "host": _host_identity(host), "acp_session_id": acp_id,
        "prepared": deepcopy(target.prepared_plugins),
        "config_options": _wire_options(client) if target.env.get(SOURCE_ENV) == "target-settings" else None,
    }
    db.update_session_target(host.session_id, target.to_json(), target.cwd)


def remember_host_preferences(db: Any, session: Any) -> None:
    """Keep the host witness in step with actual verified selection updates."""
    from .session_preferences import SOURCE_ENV

    bound = (session.target.venue or {}).get(CONTEXT_KEY, {})
    evidence = bound.get("prepared_launch")
    if (
        not isinstance(evidence, dict) or session.client is None
        or session.target.env.get(SOURCE_ENV) != "target-settings"
        or evidence.get("acp_session_id") != session.acp_session_id
    ):
        return
    evidence["config_options"] = _wire_options(session.client)
    db.update_session_target(session.session_id, session.target.to_json(), session.target.cwd)


def restore_host_launch(session: Any, host: Any, client: Any) -> None:
    """Recover evidence only after authenticated reattachment to that same child."""
    from .acp_preferences import AcpPreferencesMixin
    from .session_preferences import SOURCE_ENV

    bound = (session.target.venue or {}).get(CONTEXT_KEY)
    if bound is None:
        return
    require_local_receiver(session.target)
    evidence = bound.get("prepared_launch")
    if (
        not isinstance(evidence, dict) or evidence.get("digest") != bound.get("digest")
        or host.boundary != "local" or not host.nonce or evidence.get("host") != _host_identity(host)
        or evidence.get("acp_session_id") != session.acp_session_id
        or not isinstance(evidence.get("prepared"), dict)
        or evidence["prepared"].get("execution_cwd") != session.target.cwd
    ):
        raise PrivilegedLaunchRefused("Surviving privileged host has no matching preparation evidence")
    if session.target.env.get(SOURCE_ENV) == "target-settings":
        options = evidence.get("config_options")
        if not isinstance(options, list):
            raise PrivilegedLaunchRefused("Surviving privileged host preference evidence is incomplete")
        selected = AcpPreferencesMixin._selected_preferences(options)
        if not selected.get("model") or selected != client.confirmed_preferences:
            raise PrivilegedLaunchRefused("Surviving privileged host has no matching preference evidence")
        client._verified_options = deepcopy(options)
        client._preferences_ready = True
    session.target.prepared_plugins = deepcopy(evidence["prepared"])
