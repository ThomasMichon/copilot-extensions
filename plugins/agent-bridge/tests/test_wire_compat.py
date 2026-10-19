"""Version-skew invariant: agent-bridge wire models are **tolerant readers**.

Part of the *version-skew-tolerant-contracts* effort (dotfiles #632): plugin
payloads land independently, so a **newer client** routinely talks to an **older
daemon** and vice-versa. For that to stay *correct while skewed*, a model that
crosses the wire must **ignore unknown fields** rather than reject them — an older
receiver must tolerate a field a newer sender added.

Pydantic v2 already defaults to ``extra="ignore"``, so today the suite is
tolerant *by accident* (no wire model sets ``extra="forbid"``; verified during the
#632 contract inventory). This test makes that property an **explicit, enforced
invariant**: a single well-meaning ``model_config = ConfigDict(extra="forbid")``
on a request/response model would silently break forward-compatibility under
version skew, and this guard fails the moment one is introduced.

Scope: every ``pydantic.BaseModel`` subclass *defined in* the ``agent_bridge``
package (discovered by walking the package so new models are covered
automatically), except the two explicitly classified local remote-endpoint
configuration models. Those reject unknown configuration rather than silently
ignoring an unsupported auth selector; they are not peer request/response
readers. ``extra="allow"`` and the default (``"ignore"``) are both fine for wire
models — only ``"forbid"`` is banned.
"""

from __future__ import annotations

import importlib
import pkgutil

from pydantic import BaseModel

import agent_bridge
from agent_bridge.remote_acp_config import RemoteAcpTarget, RemoteAuth

_LOCAL_CONFIGURATION_MODELS = frozenset({RemoteAcpTarget, RemoteAuth})


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
    """No agent-bridge wire model may reject unknown fields (forward-compat).

    ``extra="forbid"`` turns an additive field from a newer sender into a hard
    validation error against an older receiver — the exact skew failure this
    invariant exists to prevent.
    """
    offenders = [
        f"{c.__module__}.{c.__name__}"
        for c in _agent_bridge_models()
        if c not in _LOCAL_CONFIGURATION_MODELS and c.model_config.get("extra") == "forbid"
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
