"""Trusted embedding inputs and atomic, body-free acquisition projection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

from .attention import Cursor, Facts, Handled, Interest, PRKey
from .attention_state import AttentionState, MAX_INTERESTS, _scoped
from .conversation_journal import Observation
from .review_intent import identifier


@dataclass(frozen=True)
class AttentionInputs:
    """The real selector/policy owner's complete interest set for this generation.

    Neither these recommendations nor an interest grants an effect or assignment.
    Unknown selection stays ``Interest.matches=None``; incomplete policy must not
    be represented as complete negative recommendations.
    """

    generation_id: str
    interests: tuple[Interest, ...]
    complete: bool
    conflict: bool
    ci_rekick: bool
    merge_nudge: bool
    final_iteration: bool

    def __post_init__(self) -> None:
        identifier(self.generation_id)
        if (
            type(self.interests) is not tuple
            or len(self.interests) > MAX_INTERESTS
            or any(type(interest) is not Interest for interest in self.interests)
            or len({(value.consumer, value.role) for value in self.interests}) != len(self.interests)
            or any(type(value) is not bool for value in (
                self.complete, self.conflict, self.ci_rekick, self.merge_nudge,
                self.final_iteration,
            ))
        ):
            raise ValueError("bounded unique interests and explicit policy evidence required")


def unavailable_attention(
    state: AttentionState,
    key: PRKey,
    generation: dict,
    inputs: AttentionInputs,
    observed_at: int,
) -> dict:
    """Invalidate retained work without inventing a missing observation or cursor."""
    if type(inputs) is not AttentionInputs or inputs.generation_id != generation["id"]:
        raise ValueError("attention inputs differ from the acquired generation")
    current = {(value.consumer, value.role): value for value in inputs.interests}
    scoped_key = asdict(key)
    updated = 0
    for row in state.records:
        if row["facts"]["key"] != scoped_key:
            continue
        old_interest = Interest(**row["interest"])
        interest = current.get(
            (old_interest.consumer, old_interest.role), replace(old_interest, matches=None),
        )
        row["interest"] = asdict(interest)
        row["facts"]["complete"] = False
        row["facts"]["observed_at"] = observed_at
        updated += 1
    return {"status": "unavailable", "interests": updated}


def observe_attention(
    state: AttentionState,
    observation: Observation,
    cursor: Cursor,
    generation: dict,
    inputs: AttentionInputs,
) -> dict:
    """Mutate only a transaction-local value; publication remains the owner's job."""
    if type(inputs) is not AttentionInputs or inputs.generation_id != generation["id"]:
        raise ValueError("attention inputs differ from the acquired generation")
    draft = (generation["sections"]["pull_request"]["data"] or {}).get("isDraft")
    complete = observation.complete and inputs.complete and type(draft) is bool
    key = asdict(observation.key)
    scoped = {
        (row["interest"]["consumer"], row["interest"]["role"]): row
        for row in state.records if row["facts"]["key"] == key
    }
    current = {(value.consumer, value.role): value for value in inputs.interests}
    for row in scoped.values():
        old_interest = Interest(**row["interest"])
        identity = (old_interest.consumer, old_interest.role)
        if identity not in current:
            current[identity] = replace(old_interest, matches=None)
    if len(state.records) - len(scoped) + len(current) > MAX_INTERESTS:
        raise ValueError("attention interest capacity exhausted")
    updated = []
    for identity, interest in current.items():
        old = scoped.get(identity)
        handled, provenance = None, None
        pending_since = observation.observed_to
        if old is not None:
            facts = Facts(**_scoped(old["facts"]))
            if facts.cursor.epoch > cursor.epoch:
                raise ValueError("attention acquisition cannot regress its journal epoch")
            if facts.cursor.epoch == cursor.epoch:
                if cursor.sequence < facts.cursor.sequence:
                    raise ValueError("attention acquisition cannot regress its journal position")
                if old["handled"] is not None:
                    handled = Handled(**_scoped(old["handled"]))
                    provenance = old["provenance"]
                was_handled = handled is not None and handled.cursor == facts.cursor
                newly_selected = old["interest"]["matches"] is False and interest.matches is True
                if not newly_selected and (cursor == facts.cursor or not was_handled):
                    pending_since = facts.pending_since
        facts = Facts(
            observation.key, observation.candidate, cursor, observation.observed_to,
            pending_since, complete, state=observation.state, draft=draft is True,
            conflict=inputs.conflict, ci_rekick=inputs.ci_rekick,
            merge_nudge=inputs.merge_nudge, final_iteration=inputs.final_iteration,
        )
        updated.append({
            "facts": asdict(facts), "interest": asdict(interest),
            "handled": asdict(handled) if handled is not None else None,
            "provenance": provenance,
        })
    retained = [row for row in state.records if row["facts"]["key"] != key]
    replacement = AttentionState({
        "protocol": state.snapshot()["protocol"], "records": [*retained, *updated],
    })
    state.records = replacement.records
    return {"status": "observed", "interests": len(updated)}
