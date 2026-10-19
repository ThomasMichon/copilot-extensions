"""Read-only joined-CLI loadout observations over the existing control queue."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

READINESS_CONTROL = "control:plugin-readiness"


class ExpectedPlugin(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    projected_source: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9_.-]*$")
    logical_source: str | None = None
    version: str | None = None
    live_marketplace_root: str | None = None


class PluginReadinessRequest(BaseModel):
    """A Bridge-owned compiled requirement, not global plugin policy."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    owner: Literal["bridge"]
    expected_session_id: str | None = None
    cwd: str | None = None
    plugins: list[ExpectedPlugin] = Field(max_length=512)
    timeout_seconds: float = Field(default=120, gt=0, le=120, allow_inf_nan=False)

    @model_validator(mode="after")
    def unique_plugins(self):
        sources = [plugin.projected_source for plugin in self.plugins]
        if len(set(sources)) != len(sources):
            raise ValueError("projected plugin identities must be unique")
        return self


class PluginReadinessResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Literal["ready", "failed", "unsupported"]
    reason: str = Field(max_length=256)
    session_id: str
    cwd: str | None = None
    selection_digest: str
    missing: list[str] = Field(default_factory=list, max_length=512)
    control_id: int | None = None

    @model_validator(mode="after")
    def ready_has_context(self):
        if self.status == "ready" and (not self.cwd or self.missing):
            raise ValueError("ready requires a cwd and no missing plugins")
        return self


class PluginReadinessError(RuntimeError):
    pass


def prepared_readiness(
    context: dict[str, Any], *, cwd: str, session_id: str | None,
    assertion: PluginReadinessRequest | None = None,
) -> PluginReadinessRequest:
    """Bind the writer's effective selection, never a caller's guessed list."""
    from pathlib import Path

    if not isinstance(context, dict) or not isinstance(context.get("plugins"), list):
        raise PluginReadinessError("prepared-selection-missing")
    if not isinstance(context.get("cwd"), str) or Path(context["cwd"]).resolve() != Path(cwd).resolve():
        raise PluginReadinessError("prepared-cwd-changed")
    if assertion is not None:
        if assertion.cwd is not None and Path(assertion.cwd).resolve() != Path(cwd).resolve():
            raise PluginReadinessError("expected-cwd-changed")
        if assertion.expected_session_id not in (None, session_id):
            raise PluginReadinessError("expected-native-session-changed")
    try:
        return PluginReadinessRequest(
            owner="bridge", cwd=cwd, expected_session_id=session_id,
            plugins=context["plugins"],
            timeout_seconds=assertion.timeout_seconds if assertion is not None else 120,
        )
    except ValueError as exc:
        raise PluginReadinessError("prepared-selection-invalid") from exc


async def wait_plugin_readiness(
    db: Any, session_id: str, expectation: PluginReadinessRequest,
    *, operation_key: str | None = None, operation_digest: str | None = None,
) -> PluginReadinessResult:
    row = db.get_live_session(session_id)
    if row is None:
        raise PluginReadinessError("live-session-missing")
    current = row["session_id"]
    if expectation.expected_session_id not in (None, current):
        raise PluginReadinessError("native-session-changed")
    if not expectation.cwd:
        raise PluginReadinessError("expected-cwd-missing")
    payload = expectation.model_copy(update={"expected_session_id": current}).model_dump()
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    payload["selection_digest"] = digest
    sender = "bridge-startup"
    intent_key = None
    if operation_key is not None:
        if not operation_digest:
            raise PluginReadinessError("startup-input-digest-missing")
        sender += ":" + operation_digest
        intent_key = "startup-readiness-" + hashlib.sha256(operation_key.encode()).hexdigest()
        prior = db.execute_read(
            "SELECT sender,session_id,kind FROM live_messages WHERE idempotency_key=?", (intent_key,),
        )
        if prior:
            if (
                prior[0]["sender"] != sender or prior[0]["kind"] != READINESS_CONTROL
            ):
                raise PluginReadinessError("startup-operation-input-changed")
            intent_key = None  # Keep the original intent; this retry makes a fresh observation.
    control_id, reason = db.enqueue_live_message_if_fresh(
        current, sender=sender, body=json.dumps(payload, sort_keys=True),
        now=time.time(), kind=READINESS_CONTROL, delivery="queue", expected_session_id=current,
        **({"idempotency_key": intent_key} if intent_key is not None else {}),
    )
    if control_id is None or reason is not None:
        raise PluginReadinessError(f"readiness-admission-refused:{reason}")
    completed = False
    try:
        deadline = time.monotonic() + expectation.timeout_seconds
        while time.monotonic() < deadline:
            control = db.live_control_state(current, control_id)
            outcome = control.get("outcome") if control else None
            if outcome is not None:
                completed = True
                if not outcome.startswith("{"):
                    raise PluginReadinessError(f"readiness-control-{outcome}")
                try:
                    result = PluginReadinessResult.model_validate_json(outcome)
                except ValueError as exc:
                    raise PluginReadinessError("malformed-readiness-result") from exc
                if result.session_id != current or result.selection_digest != digest:
                    raise PluginReadinessError("readiness-result-context-changed")
                if db.resolve_live_session_id(current) != current:
                    raise PluginReadinessError("native-session-changed")
                if result.status != "ready":
                    missing = ",".join(result.missing[:8])
                    raise PluginReadinessError(
                        f"{result.status}:{result.reason}" + (f":{missing}" if missing else ""),
                    )
                return result.model_copy(update={"control_id": control_id})
            await asyncio.sleep(min(0.05, max(0, deadline - time.monotonic())))
        raise PluginReadinessError("readiness-timeout")
    finally:
        if not completed:
            if not db.withdraw_live_control(current, control_id, time.time()):
                # The claimed operation only reads native state. A late result
                # cannot authorize a task after its owning waiter has stopped.
                db.ack_live_messages(
                    current, [control_id], now=time.time(), controls=True, outcome="cancelled",
                )
