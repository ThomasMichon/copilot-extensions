"""Local effective-source observations, not catalog publication authority."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
import hashlib
import json
from typing import TYPE_CHECKING, Literal

from dropin_registry import ScanAuthority

from .pool_instances import PoolDefinitionError, PoolInstanceDefinition, ordered_pool_instances
from .registrar import ProfileDeclaration
from .registrations import RegistrationKind

if TYPE_CHECKING:
    from .registrar_discovery import RegistrarSources

SourceState = Literal["fresh", "pending", "cached", "delayed", "unavailable", "unconfirmed"]


@dataclass(frozen=True)
class DeclarationRead:
    """One source call's declarations and completeness, captured together."""

    declarations: tuple[ProfileDeclaration, ...]
    complete: bool
    reason: str = ""

    def __iter__(self) -> Iterator[ProfileDeclaration]:
        return iter(self.declarations)


def read_supervisor_declarations(
    sources: RegistrarSources, *, legacy: bool = False,
) -> DeclarationRead:
    """Use the existing registrar owner; do not qualify its retained fallback."""
    if legacy:
        # The legacy env reader has no complete-versus-unreadable observation.
        return DeclarationRead(
            tuple(sources.discover_with_legacy()), False, "legacy-source-unconfirmed",
        )
    report = sources.refresh()
    complete = (
        report.trusted_authority is not ScanAuthority.INDETERMINATE
        and report.combined.plugins.snapshot.authority is not ScanAuthority.INDETERMINATE
        and not report.combined.plugins.findings
    )
    return DeclarationRead(
        report.combined.declarations, complete,
        "" if complete else "registrar-source-unavailable",
    )


@dataclass(frozen=True)
class SourceObservation:
    """A source state in one collection attempt; no freshness renewal by replay."""

    source: str
    state: SourceState
    reason: str = ""


@dataclass(frozen=True)
class PoolSourceSnapshot:
    """All effective worker definitions or an explicit unavailable observation."""

    attempt: int
    observed_at: float
    definitions: tuple[PoolInstanceDefinition, ...]
    observations: tuple[SourceObservation, ...]
    reasons: tuple[str, ...]
    input_revision: str | None = None
    observation_revision: str | None = None

    @property
    def complete(self) -> bool:
        return not self.reasons


def local_input_revision(declarations: object, overrides: object) -> str:
    """Correlate exact observed local inputs; a digest alone proves no freshness."""
    encoded = json.dumps(
        [declarations, overrides], sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def collect_pool_sources(
    registrations: Mapping[str, dict], *, machine: str | None, env: str,
    attempt: int, observed_at: float, observations: Iterable[SourceObservation],
    reasons: Iterable[str] = (),
) -> PoolSourceSnapshot:
    """Normalize the owner's already-merged/vetoed set; never return a subset."""
    inputs = tuple(observations)
    unavailable = list(reasons)
    if len(inputs) != 3 or {item.source for item in inputs} != {"declarations", "registrations", "overrides"}:
        unavailable.append("source-observations-incomplete")
    unavailable.extend(
        f"{item.source}:{item.reason or item.state}"
        for item in inputs if item.state != "fresh"
    )
    if not machine or not machine.strip() or not env or not env.strip():
        unavailable.append("pool-scope-unidentified")
    definitions = []
    for rid, registration in registrations.items():
        if registration.get("kind") != RegistrationKind.SUPERVISED_LANE:
            continue
        try:
            definition = PoolInstanceDefinition.from_registration(registration)
            if definition.registration_id != rid or (definition.machine, definition.env) != (machine, env):
                raise PoolDefinitionError("effective registration has foreign scope")
            definitions.append(definition)
        except PoolDefinitionError:
            unavailable.append("effective-worker-definition-invalid")
    if unavailable:
        definitions = []
    return PoolSourceSnapshot(
        attempt, observed_at, ordered_pool_instances(definitions), inputs,
        tuple(sorted(set(unavailable))),
    )
