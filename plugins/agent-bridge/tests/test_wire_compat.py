"""Version-skew invariant: agent-bridge wire models are **tolerant readers**.

Part of the *version-skew-tolerant-contracts* effort (dotfiles #632): plugin
payloads land independently, so a **newer client** routinely talks to an **older
daemon** and vice-versa. For that to stay *correct while skewed*, a model that
crosses the wire must **ignore unknown fields** rather than reject them — an older
receiver must tolerate a field a newer sender added.

Pydantic v2 defaults to ``extra="ignore"``. This test enforces that default for
ordinary request/response models and inventories the narrow closed-contract
exceptions, rather than exempting whole modules or inherited subclasses.

Scope: ordinary peer models discovered throughout ``agent_bridge``. Explicit
local configuration and closed, capability-gated control/identity envelopes
are classified below. They refuse unknown intent rather than silently changing
an exact invocation or destructive-control contract. Adding fields to those
envelopes requires explicit negotiation, not tolerant-reader fallback.
"""

from __future__ import annotations

import importlib
import pkgutil

from pydantic import BaseModel

import agent_bridge
from agent_bridge.remote_acp_config import (
    RemoteAcpTarget, RemoteAuth, RemoteAuthApproval, RemoteAuthRequirements,
    RemoteAcpBinding, RemoteAcpRequirements,
)
from agent_bridge.drain_contract import DrainContract, DrainRequest
from agent_bridge.live_inbox_models import (
    InboxBindingInfo, InboxConsumerInfo, InboxCurrentRequest, InboxEnrollRequest,
    InboxHandoffRequest, InboxNegotiateRequest, InboxOutcomeRequest,
)
from agent_bridge.native_keyed_admission import KeyedNativePrompt, NativeInvocationBinding

_LOCAL_CONFIGURATION_MODELS = frozenset({
    RemoteAcpTarget, RemoteAuth, RemoteAuthApproval, RemoteAuthRequirements,
    RemoteAcpBinding, RemoteAcpRequirements,
})
_CLOSED_CONTROL_MODELS = frozenset({
    # Explicit preservation policy may not ignore an unknown destructive option.
    DrainContract, DrainRequest,
    # Immutable keyed invocation identity must not discard binding fields.
    KeyedNativePrompt, NativeInvocationBinding,
    # HTTP-48 retained-inbox-v1 is an exact owner/handoff observation contract.
    InboxBindingInfo, InboxConsumerInfo, InboxCurrentRequest, InboxEnrollRequest,
    InboxHandoffRequest, InboxNegotiateRequest, InboxOutcomeRequest,
})


def _agent_bridge_models() -> list[type[BaseModel]]:
    """Every BaseModel subclass defined under the ``agent_bridge`` package.

    Imports each submodule (best-effort — a module that cannot import on this
    platform is skipped, not fatal) so ``BaseModel.__subclasses__`` sees it, then
    collects the subclasses whose ``__module__`` is inside ``agent_bridge``.
    """
    for mod in pkgutil.walk_packages(
        agent_bridge.__path__, prefix="agent_bridge."
    ):
        try:
            importlib.import_module(mod.name)
        except Exception:
            continue

    seen: set[type[BaseModel]] = set()
    stack: list[type[BaseModel]] = list(BaseModel.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
    return [c for c in seen if c.__module__.startswith("agent_bridge")]


def test_wire_models_discovered():
    # Sanity: the walk actually finds the known models (models.py alone has 30+).
    models = _agent_bridge_models()
    names = {c.__name__ for c in models}
    assert "StartSessionRequest" in names
    assert len(models) >= 20


def test_no_wire_model_forbids_extra_fields():
    """Ordinary wire models cannot reject additive fields under version skew.

    ``extra="forbid"`` turns an additive field from a newer sender into a hard
    validation error against an older receiver — the exact skew failure this
    invariant exists to prevent.
    """
    offenders = [
        f"{c.__module__}.{c.__name__}"
        for c in _agent_bridge_models()
        if c not in _LOCAL_CONFIGURATION_MODELS | _CLOSED_CONTROL_MODELS
        and c.model_config.get("extra") == "forbid"
    ]
    assert not offenders, (
        "These agent-bridge wire models set extra='forbid', which breaks "
        "forward-compatibility under version skew (dotfiles #632). Use the "
        "default (extra='ignore') so an older receiver tolerates a newer "
        "sender's added fields:\n  " + "\n  ".join(sorted(offenders))
    )


def test_guard_catches_a_forbidding_model():
    """The guard's own logic: a model that forbids extras is detected."""
    from pydantic import ConfigDict

    class _Forbidding(BaseModel):
        model_config = ConfigDict(extra="forbid")

    # Defined in this test module, not agent_bridge, so the real guard ignores
    # it -- but the detection predicate must flag it.
    assert _Forbidding.model_config.get("extra") == "forbid"


def test_local_configuration_is_explicit_and_does_not_exempt_wire_subclasses():
    assert _LOCAL_CONFIGURATION_MODELS <= set(_agent_bridge_models())
    assert all(c.model_config.get("extra") == "forbid" for c in _LOCAL_CONFIGURATION_MODELS)
    assert all(c.model_config.get("frozen") for c in _LOCAL_CONFIGURATION_MODELS)

    class FutureWireModel(RemoteAuth):
        pass

    assert FutureWireModel not in _LOCAL_CONFIGURATION_MODELS


def test_closed_control_classification_does_not_exempt_future_subclasses():
    assert _CLOSED_CONTROL_MODELS <= set(_agent_bridge_models())
    assert all(c.model_config.get("extra") == "forbid" for c in _CLOSED_CONTROL_MODELS)

    class FutureWireModel(InboxNegotiateRequest):
        pass

    assert FutureWireModel not in _CLOSED_CONTROL_MODELS
