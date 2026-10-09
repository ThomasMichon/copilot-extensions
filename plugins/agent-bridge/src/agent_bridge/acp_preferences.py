"""ACP preference application, readback and selected-state preservation."""

from __future__ import annotations

import logging
import os
from typing import Any

from .session_preferences import PreferenceApplicationError, SOURCES, clean

log = logging.getLogger("agent-bridge")

_ACP_MODEL_CONFIG_ID = "model"

_ACP_EFFORT_CONFIG_ID = "reasoning_effort"

_ACP_MODEL_ENV = ("AGENT_BRIDGE_ACP_MODEL", "AGENT_CODESPACES_ACP_MODEL")

_ACP_EFFORT_ENV = ("AGENT_BRIDGE_ACP_EFFORT", "AGENT_CODESPACES_ACP_EFFORT")

_ACP_PROPAGATE_OFF_ENV = ("AGENT_BRIDGE_MODEL_PROPAGATE", "AGENT_CODESPACES_MODEL_PROPAGATE")

def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return None

def _cfg_attr(obj: Any, attr: str, key: str) -> Any:
    """Read a field from an ACP config-option that may be a pydantic model or a
    plain dict. Prefers the model attribute (snake_case), falls back to the dict
    key (camelCase wire form). Returns ``None`` when absent.
    """
    value = getattr(obj, attr, None)
    if value is None and isinstance(obj, dict):
        value = obj.get(key)
    return value

def _config_index(
    options: Any, *, grouped: bool = False,
) -> dict[str, tuple[str | None, set[str]]]:
    advertised: dict[str, tuple[str | None, set[str]]] = {}
    for option in options or []:
        key = _cfg_attr(option, "id", "id")
        if key:
            values: set[str] = set()
            choices = list(_cfg_attr(option, "options", "options") or [])
            while choices:
                choice = choices.pop()
                value = _cfg_attr(choice, "value", "value")
                if isinstance(value, str):
                    values.add(value)
                elif grouped:
                    choices.extend(_cfg_attr(choice, "options", "options") or [])
            advertised[key] = (_cfg_attr(option, "current_value", "currentValue"), values)
    return advertised


class AcpPreferencesMixin:
    def _initialize_preferences(
        self, source: str, context: str | None, target: dict[str, Any] | None,
        launch: dict[str, str] | None, confirmed: dict[str, str] | None,
        provider_intent: bool,
    ) -> None:
        if source not in SOURCES:
            raise ValueError("unsupported preference_source")
        self.preference_source = source
        self.context_override = context
        self.target_preferences = target
        self.launch_preferences = dict(launch or {})
        self.confirmed_preferences = dict(confirmed or {})
        self.provider_intent = provider_intent
        self._verified_options = None
        self._preferences_ready = source == "caller-settings"

    def _resolve_caller_preferences(self) -> dict[str, str]:
        raise NotImplementedError

    async def _apply_model_config(self, config_options: Any, *, resuming: bool = False) -> None:
        """Set the session's ``model`` / ``reasoning_effort`` via ACP.

        Copilot ignores ``--model`` / ``--reasoning-effort`` in ``--acp`` mode;
        the model is chosen here, per-session, by ``session/set_config_option``
        against the *select* options the agent advertised in its
        ``session/new`` / ``session/load`` response. Only options the agent
        actually offers (with the desired value among their choices) are set,
        and only when they differ from the current value. Caller-settings mode
        retains its degrade-safe fallback. Target-settings mode requires actual
        readback of model/effort and fails startup if that intent is unverified;
        unsupported context is reported without a false effectiveness claim.
        """
        if not self._connection or not self._acp_session_id:
            return
        target_mode = self.preference_source == "target-settings"
        if target_mode:
            self._preferences_ready = False
            self._verified_options = config_options
        preserving = target_mode and (resuming or bool(self.confirmed_preferences))
        if target_mode:
            receipt = self.target_preferences or {}
            self._emit("preference_resolution", {
                "source": "session-selection" if preserving else self.preference_source,
                "status": (
                    "resolved" if self.confirmed_preferences else "unconfirmed"
                ) if preserving else receipt.get("status", "unsupported"),
                "reason": receipt.get("reason", "authority-unavailable" if not receipt else None),
            })
            if preserving:
                # Old persisted sessions with no snapshot retain the values
                # advertised by load. Never replace them with fresh defaults.
                desired = dict(self.confirmed_preferences)
            else:
                desired = dict(receipt.get("values") or {})
                off = _first_env(_ACP_PROPAGATE_OFF_ENV)
                disabled = off is not None and off.lower() in ("0", "false", "no", "off")
                sources = receipt.get("sources") or {}
                if disabled:
                    desired = {
                        key: value for key, value in desired.items()
                        if sources.get(key) == "launch-profile"
                    }
                for key, names in (
                    ("model", _ACP_MODEL_ENV),
                    ("reasoning_effort", _ACP_EFFORT_ENV),
                    ("context", ("AGENT_BRIDGE_ACP_CONTEXT", "AGENT_CODESPACES_ACP_CONTEXT")),
                ):
                    if value := _first_env(names):
                        if sources.get(key) != "launch-profile":
                            desired[key] = value
                desired.update(self.launch_preferences)
        else:
            try:
                desired = self._resolve_caller_preferences()
            except Exception as exc:
                log.debug("ACP model-config resolution failed: %s", exc)
                desired = {}
        # Explicit requests precede inherited defaults and profile selections.
        if self.model_override and not preserving:
            desired[_ACP_MODEL_CONFIG_ID] = self.model_override
        if self.effort_override and not preserving:
            desired[_ACP_EFFORT_CONFIG_ID] = self.effort_override
        if target_mode and self.context_override and not preserving:
            desired["context"] = self.context_override
        if not desired and not target_mode:
            return

        # Index the advertised options by id -> (current_value, {allowed values}).
        advertised = _config_index(config_options, grouped=target_mode)

        if (
            target_mode and not preserving
            and (self.provider_intent or receipt.get("provider_selected"))
            and (receipt.get("sources") or {}).get("model") != "launch-profile"
            and "model" not in self.launch_preferences and not self.model_override
        ):
            native_model = clean((advertised.get("model") or (None, set()))[0])
            if native_model:
                desired["model"] = native_model
            else:
                desired.pop("model", None)

        applied: dict[str, str] = {}
        fallbacks: list[dict[str, Any]] = []
        unconfirmed: set[str] = set()
        config_ids = [_ACP_MODEL_CONFIG_ID, _ACP_EFFORT_CONFIG_ID]
        if target_mode:
            config_ids.append("context")
        for config_id in config_ids:
            key = config_id
            if target_mode and key == "context":
                config_id = "context" if "context" in advertised else "context_tier"
            value = desired.get(key)
            if not value:
                continue
            entry = advertised.get(config_id)
            if entry is None:
                log.warning(
                    "ACP agent does not advertise config option %r; the dispatched "
                    "agent keeps its default (requested %s=%s)",
                    config_id, config_id, value,
                )
                fallbacks.append(
                    {"config": config_id, "requested": value, "reason": "not-advertised"}
                )
                continue
            current, allowed = entry
            if (allowed or target_mode) and value not in allowed:
                log.warning(
                    "ACP config %s=%r not offered by agent (%d options); the "
                    "dispatched agent keeps its default",
                    config_id, value, len(allowed),
                )
                fallbacks.append(
                    {"config": config_id, "requested": value, "reason": "not-offered",
                     "offered": sorted(allowed)}
                )
                continue
            if current == value:
                applied[config_id] = current
                continue
            try:
                result = await self._connection.set_config_option(
                    config_id=config_id,
                    session_id=self._acp_session_id,
                    value=value,
                )
                if target_mode:
                    readback = _cfg_attr(result, "config_options", "configOptions")
                    if not isinstance(readback, (list, tuple)):
                        unconfirmed.add(key)
                        fallbacks.append({"config": config_id, "requested": value,
                                          "reason": "readback-unavailable"})
                        continue
                    self._verified_options = readback
                    advertised = _config_index(readback, grouped=True)
                    unconfirmed.clear()
                    actual = next((
                        _cfg_attr(option, "current_value", "currentValue")
                        for option in readback
                        if _cfg_attr(option, "id", "id") == config_id
                    ), None)
                    if actual != value:
                        fallbacks.append({"config": config_id, "requested": value,
                                          "reason": "not-confirmed", "actual": actual})
                        continue
                    applied[config_id] = actual
                else:
                    applied[config_id] = value
                log.info(
                    "ACP session %s: set %s=%s", self._acp_session_id, config_id, value,
                )
            except Exception as exc:
                unconfirmed.add(key)
                log.warning("ACP set_config_option %s=%s failed: %s", config_id, value, exc)
                fallbacks.append(
                    {"config": config_id, "requested": value, "reason": "rpc-failed",
                     "error": str(exc)}
                )

        if target_mode:
            effective = self._selected_preferences(self._verified_options)
            final_options = _config_index(self._verified_options, grouped=True)
            for key in unconfirmed:
                effective.pop(key, None)
            for key in ("model", "reasoning_effort", "context"):
                option_id = (
                    "context" if "context" in final_options else "context_tier"
                ) if key == "context" else key
                final_allowed = (final_options.get(option_id) or (None, set()))[1]
                if desired.get(key) and (
                    desired[key] not in final_allowed or effective.get(key) != desired[key]
                ):
                    if not any(
                        item["config"] in (
                            {"context", "context_tier"} if key == "context" else {key}
                        ) for item in fallbacks
                    ):
                        fallbacks.append({"config": key, "requested": desired[key],
                                          "reason": "not-offered" if desired[key] not in final_allowed
                                          else "not-confirmed",
                                          "actual": effective.get(key)})
            failed_keys = {
                "context" if item["config"] == "context_tier" else item["config"]
                for item in fallbacks
            }
            applied = {
                key: effective[key] for key in desired
                if key in effective and effective[key] == desired[key] and key not in failed_keys
            }

        # Publish verified application outcomes and any refusal.
        applied_model = applied.get(_ACP_MODEL_CONFIG_ID)
        if applied_model:
            self._emit("usage_update", {"model": applied_model})
        if applied:
            self._emit("model_applied", dict(applied))
        if fallbacks:
            self._emit("model_fallback", {
                "requested": dict(desired),
                "applied": dict(applied),
                "fallbacks": fallbacks,
            })
        if target_mode:
            required_failures = [
                item for item in fallbacks
                if item["config"] in {"model", "reasoning_effort"}
            ]
            if not desired.get("model"):
                required_failures.append({"config": "model", "reason": "intent-unavailable"})
            if required_failures:
                status = "error" if any(
                    item["reason"] in {"rpc-failed", "readback-unavailable", "not-confirmed"}
                    for item in required_failures
                ) else "unsupported"
                self._emit("preference_application", {
                    "status": status, "requested": desired, "effective": effective,
                    "failures": required_failures,
                })
                raise PreferenceApplicationError(
                    f"target-settings {status}: required model/effort was not verified"
                )
            self._emit("preference_application", {
                "status": "applied", "requested": desired, "effective": effective,
                "context_failures": [
                    item for item in fallbacks
                    if item["config"] in {"context", "context_tier"}
                ],
            })
            self._preferences_ready = True
            self.confirmed_preferences = effective
            self._emit("preference_selected", dict(effective))

    @staticmethod
    def _selected_preferences(config_options: Any) -> dict[str, str]:
        selected: dict[str, str] = {}
        for key, (current, offered) in _config_index(config_options, grouped=True).items():
            if key in {"model", "reasoning_effort", "context", "context_tier"}:
                if (value := clean(current)) and value in offered:
                    selected["context" if key == "context_tier" else key] = value
        return selected

    def _remember_preferences(
        self, config_options: Any,
    ) -> None:
        self.confirmed_preferences = self._selected_preferences(config_options)
        self._emit("preference_selected", dict(self.confirmed_preferences))
