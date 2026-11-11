"""Registrar standing-charter declarations: validated records, never process registrations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .registrar import ProfileDeclaration, RegistrarError, _load_title

STANDING_DECLARATION_KIND = "standing-charter"
STANDING_SPEC_FIELDS = (
    "charter_key", "lane_key", "effort_ref", "materialization_ref",
    "source_revision", "expected_materialization_revision",
)
_ALLOWED_KEYS = frozenset({"name", "kind", "repos", "title", "description", "spec"})


def _bounded_text(value: object, *, key: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > 1024 or any(ord(char) < 32 for char in value)
    ):
        raise RegistrarError(f"{key}: expected bounded nonempty single-line text")
    return value


def load_standing_declaration(data: Mapping) -> ProfileDeclaration:
    """Validate one standing-charter document; its namespace comes only from discovery."""
    extras = sorted(set(data) - _ALLOWED_KEYS)
    if extras:
        raise RegistrarError(f"standing-charter declarations do not accept fields: {extras}")
    name = data.get("name")
    if not isinstance(name, str) or not name or not all(c.isalnum() or c in "-_" for c in name):
        raise RegistrarError("name: standing-charter declarations need a letters/digits/'-'/'_' name")
    repos = data.get("repos")
    if repos == "all":
        raise RegistrarError("repos: standing-charter declarations need one explicit repository lane")
    _bounded_text(repos, key="repos")
    description = data.get("description")
    if description is not None and not isinstance(description, str):
        raise RegistrarError(f"description: expected a string, got {description!r}")
    spec = data.get("spec")
    if not isinstance(spec, Mapping):
        raise RegistrarError("standing-charter declarations need a 'spec' mapping")
    unknown = sorted(set(spec) - {*STANDING_SPEC_FIELDS, "enabled"})
    if unknown:
        raise RegistrarError(f"spec: unknown standing-charter fields {unknown}")
    normalized: dict[str, object] = {
        key: _bounded_text(spec.get(key), key=f"spec.{key}") for key in STANDING_SPEC_FIELDS
    }
    enabled = spec.get("enabled", True)
    if not isinstance(enabled, bool):
        raise RegistrarError("spec.enabled: expected true or false")
    normalized["enabled"] = enabled
    return ProfileDeclaration(
        name=name, kind=STANDING_DECLARATION_KIND, spec=normalized, repos=repos,
        title=_load_title(data.get("title")), description=description,
    )


def is_standing_declaration(declaration: ProfileDeclaration) -> bool:
    return declaration.kind == STANDING_DECLARATION_KIND


@dataclass(frozen=True)
class StandingSourceEntry:
    """One discovered declaration, keyed by the discovery owner's namespace."""

    repo: str
    namespace: str
    title: str
    declaration: Mapping[str, str]
    enabled: bool
    source_path: str | None = None


def standing_source_entries(declarations: Iterable[ProfileDeclaration]) -> list[StandingSourceEntry]:
    """Partition standing records out of a discovered set without inventing ownership."""
    entries: list[StandingSourceEntry] = []
    for declaration in declarations:
        if not is_standing_declaration(declaration):
            continue
        if not declaration.owner:
            raise RegistrarError(
                f"{declaration.name}: standing-charter declarations require discovery owner provenance"
            )
        spec = declaration.spec
        entries.append(StandingSourceEntry(
            repo=declaration.repos, namespace=declaration.owner,
            title=declaration.title or declaration.name,
            declaration={
                "namespace": declaration.owner,
                **{key: str(spec[key]) for key in STANDING_SPEC_FIELDS},
            },
            enabled=bool(spec.get("enabled", True)), source_path=declaration.source_path,
        ))
    return entries
