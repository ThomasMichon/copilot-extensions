"""Bounded current observations; these local records authenticate no caller."""

from dataclasses import asdict, dataclass
import json
import math

from .planning import PlanError, canonical, decode, identifier, integer, record

MAX_UPDATE_BYTES = 64 * 1024
MAX_TEXT_BYTES = 4096
STATE_FIELDS = frozenset({
    "status", "title", "branch", "codename", "platform", "checkout_present", "summary",
})


@dataclass(frozen=True)
class OriginBinding:
    origin: str
    installation: str
    generation: int
    producer: str

    def __post_init__(self):
        for field in ("origin", "installation", "producer"):
            identifier(getattr(self, field), f"origin {field}")
        integer(self.generation, "origin generation", minimum=1)


@dataclass(frozen=True)
class ObservationCursor:
    store: str
    origin: str
    project: str
    position: int

    def __post_init__(self):
        for field in ("store", "origin", "project"):
            identifier(getattr(self, field), f"cursor {field}")
        integer(self.position, "observation position")


@dataclass(frozen=True)
class CurrentUpdate:
    """Canonical bytes detach the accepted content from caller-owned objects."""

    encoded: bytes

    def __post_init__(self):
        if type(self.encoded) is not bytes or len(self.encoded) > MAX_UPDATE_BYTES:
            raise PlanError("current update exceeds its byte bound")
        value = decode(self.encoded)
        record(value, "fleet-manager.current-update", {
            "origin", "installation", "generation", "producer", "project", "entity",
            "sequence", "source_revision", "observed_at", "availability", "reason", "state",
        })
        OriginBinding(**{field: value[field] for field in (
            "origin", "installation", "generation", "producer",
        )})
        for field in ("project", "entity"):
            identifier(value[field], f"current {field}")
        for field in ("sequence", "source_revision"):
            integer(value[field], f"current {field}", minimum=1)
        observed = value["observed_at"]
        if (type(observed) not in (int, float) or not 0 <= observed < 2**63
                or not math.isfinite(observed)):
            raise PlanError("invalid observation timestamp")
        if value["availability"] not in ("current", "stale", "advisory", "unknown"):
            raise PlanError("unsupported observation availability")
        reason = value["reason"]
        if reason is not None:
            identifier(reason, "observation reason")
        if value["availability"] != "current" and reason is None:
            raise PlanError("degraded observation requires a reason")
        state = value["state"]
        if state is None:
            if reason != "projection-removed":
                raise PlanError("current-view removal requires an explicit reason")
        elif not isinstance(state, dict) or not state or state.keys() - STATE_FIELDS:
            raise PlanError("unsupported current-state fields")
        else:
            for field, item in state.items():
                if item is None:
                    continue
                if field == "checkout_present":
                    if type(item) is not bool:
                        raise PlanError("invalid current checkout presence")
                elif not isinstance(item, str) or len(item.encode("utf-8")) > MAX_TEXT_BYTES:
                    raise PlanError("current-state text exceeds its bound")
        if canonical(value) != self.encoded:
            raise PlanError("current update encoding must be canonical")

    @classmethod
    def from_dict(cls, value):
        return cls(canonical(value))

    def to_dict(self):
        return json.loads(self.encoded)

    @property
    def binding(self):
        value = self.to_dict()
        return OriginBinding(**{field: value[field] for field in (
            "origin", "installation", "generation", "producer",
        )})


def binding_dict(binding):
    if not isinstance(binding, OriginBinding):
        raise PlanError("an explicit origin binding is required")
    return asdict(binding)
