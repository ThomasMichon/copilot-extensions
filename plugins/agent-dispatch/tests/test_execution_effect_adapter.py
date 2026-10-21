"""Deterministic adopter-boundary contracts; no processes or external effects."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_dispatch.execution_effect_adapter import (
    EffectAdapterError,
    EffectObservation,
    EffectRevocation,
    EffectState,
    ExecutionScope,
    observe_effect_revocation,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def revocation() -> EffectRevocation:
    return EffectRevocation(
        ExecutionScope("installation-a", "coordinator-a", "example/project", "producer-a"),
        "operation-a",
        3,
        "automatic",
        "manual",
    )


class Adapter:
    def __init__(self, observation: EffectObservation, version: int = 1) -> None:
        self.observation = observation
        self.version = version
        self.requests: list[EffectRevocation] = []
        self.scopes: list[ExecutionScope] = []

    async def protocol_version(self, scope: ExecutionScope) -> int:
        self.scopes.append(scope)
        return self.version

    async def revoke(self, request: EffectRevocation) -> EffectObservation:
        self.requests.append(request)
        return self.observation


@pytest.mark.anyio
async def test_same_operation_retry_observes_without_changing_generation(
    revocation: EffectRevocation,
) -> None:
    observation = EffectObservation(revocation, "acknowledged", True, True)
    adapter = Adapter(observation)
    assert await observe_effect_revocation(adapter, revocation) is observation
    assert await observe_effect_revocation(adapter, revocation) is observation
    assert adapter.requests == [revocation, revocation]
    assert adapter.scopes == [revocation.scope, revocation.scope]
    assert revocation.generation == 3


@pytest.mark.anyio
@pytest.mark.parametrize("version", [0, 2, 999])
async def test_unsupported_protocol_never_invokes_revocation(
    revocation: EffectRevocation,
    version: int,
) -> None:
    adapter = Adapter(EffectObservation(revocation, "acknowledged", True, True), version)
    result = await observe_effect_revocation(adapter, revocation)
    assert result.state == "unsupported"
    assert result.admission_closed is None
    assert result.enumeration_complete is False
    assert adapter.requests == []


@pytest.mark.anyio
async def test_missing_adapter_is_explicitly_unsupported(revocation: EffectRevocation) -> None:
    result = await observe_effect_revocation(None, revocation)
    assert result.state == "unsupported"
    assert result.detail
    assert result.request == revocation


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_id", "operation-b"),
        ("generation", 2),
        ("retiring_mode", "manual"),
    ],
)
async def test_stale_operation_or_generation_cannot_acknowledge(
    revocation: EffectRevocation,
    field: str,
    value: object,
) -> None:
    updates = {field: value}
    if field == "retiring_mode":
        updates["destination_mode"] = "automatic"
    stale = replace(revocation, **updates)
    adapter = Adapter(EffectObservation(stale, "acknowledged", True, True))
    with pytest.raises(EffectAdapterError, match="another scope, operation or generation"):
        await observe_effect_revocation(adapter, revocation)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field",
    ["installation", "coordinator", "repository", "producer_registration"],
)
async def test_other_scope_cannot_acknowledge(revocation: EffectRevocation, field: str) -> None:
    foreign = replace(revocation, scope=replace(revocation.scope, **{field: "other"}))
    adapter = Adapter(EffectObservation(foreign, "acknowledged", True, True))
    with pytest.raises(EffectAdapterError, match="another scope"):
        await observe_effect_revocation(adapter, revocation)


@pytest.mark.parametrize(
    "updates",
    [
        {"admission_closed": False},
        {"admission_closed": None},
        {"enumeration_complete": False},
        {"unresolved_effects": ("accepted-effect",)},
        {"unresolved_effects": ("ambiguous-effect",)},
        {"legacy_writers": ("legacy-writer",)},
    ],
)
def test_incomplete_barrier_cannot_be_acknowledged(
    revocation: EffectRevocation,
    updates: dict[str, object],
) -> None:
    settled = EffectObservation(revocation, "acknowledged", True, True)
    with pytest.raises(EffectAdapterError, match="complete, settled"):
        replace(settled, **updates)


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["pending", "blocked", "unsupported"])
async def test_unsettled_result_retains_references_and_reason(
    revocation: EffectRevocation,
    state: EffectState,
) -> None:
    observation = EffectObservation(
        revocation,
        state,
        True,
        False,
        unresolved_effects=("remote-outcome-unknown",),
        legacy_writers=("legacy-writer",),
        detail="reconciliation required",
    )
    assert await observe_effect_revocation(Adapter(observation), revocation) is observation


@pytest.mark.anyio
async def test_transport_timeout_is_not_an_acknowledgement(revocation: EffectRevocation) -> None:
    class Unreachable(Adapter):
        async def revoke(self, request: EffectRevocation) -> EffectObservation:
            raise TimeoutError("adopter unavailable")

    with pytest.raises(TimeoutError, match="adopter unavailable"):
        await observe_effect_revocation(
            Unreachable(EffectObservation(revocation, "acknowledged", True, True)),
            revocation,
        )


@pytest.mark.parametrize("generation", [True, False, 0, -1, 1.0, "1"])
def test_invalid_generation_is_rejected(
    revocation: EffectRevocation,
    generation: object,
) -> None:
    with pytest.raises(EffectAdapterError, match="positive integer"):
        replace(revocation, generation=generation)


@pytest.mark.parametrize("value", ["", " leading", "trailing ", "two tokens", "\x00", "x" * 513])
def test_scope_tokens_are_exact(value: str) -> None:
    with pytest.raises(EffectAdapterError, match="ASCII token"):
        ExecutionScope(value, "coordinator-a", "example/project", "producer-a")


@pytest.mark.parametrize("state", ["pending", "blocked", "unsupported"])
def test_blocker_requires_explanation(revocation: EffectRevocation, state: EffectState) -> None:
    with pytest.raises(EffectAdapterError, match="explain"):
        EffectObservation(revocation, state, None, False)


def test_reverse_handback_uses_same_contract_with_a_new_generation(
    revocation: EffectRevocation,
) -> None:
    reverse = replace(
        revocation,
        generation=4,
        operation_id="operation-b",
        retiring_mode="manual",
        destination_mode="automatic",
    )
    assert EffectObservation(reverse, "acknowledged", True, True).request == reverse
