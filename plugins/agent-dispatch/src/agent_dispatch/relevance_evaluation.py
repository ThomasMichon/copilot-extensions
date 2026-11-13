"""Opt-in periodic evaluator policy and source-judgment contract."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RelevanceJudgment:
    verdict: str
    reason: str
    observation_ref: str

    def __post_init__(self) -> None:
        if self.verdict not in ("needed", "obsolete", "unknown"):
            raise ValueError("relevance verdict must be needed, obsolete or unknown")
        for value in (self.reason, self.observation_ref):
            if (
                not isinstance(value, str) or not value.strip() or "\0" in value
                or len(value.encode("utf-8")) > 4096
            ):
                raise ValueError("relevance judgment needs bounded reason and observation_ref")

    def to_dict(self) -> dict:
        return {
            "decision": "relevance", "verdict": self.verdict,
            "reason": self.reason, "observation_ref": self.observation_ref,
        }


@dataclass(frozen=True)
class PeriodicRelevancePolicy:
    min_age_seconds: float = 3600
    interval_seconds: float = 3600
    retry_base: float = 60
    max_attempts: int = 8
    batch_size: int = 25

    @classmethod
    def from_spec(cls, spec: dict) -> PeriodicRelevancePolicy | None:
        if "periodic_relevance" not in spec:
            return None
        raw = spec["periodic_relevance"]
        if not isinstance(raw, dict) or set(raw) - cls.__dataclass_fields__.keys():
            raise ValueError("periodic_relevance must contain only periodic policy fields")
        policy = cls(**raw)
        for key in ("min_age_seconds", "interval_seconds", "retry_base"):
            value = getattr(policy, key)
            if (
                type(value) not in (int, float) or not math.isfinite(value)
                or value < 0 or (key != "min_age_seconds" and value == 0)
            ):
                raise ValueError(f"periodic_relevance.{key} must be finite and positive")
        for key, maximum in (("max_attempts", 32), ("batch_size", 100)):
            value = getattr(policy, key)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"periodic_relevance.{key} must be from 1 to {maximum}")
        return policy
