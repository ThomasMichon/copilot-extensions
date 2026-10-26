"""Version-1 external source-delta declarations and strict response parsing.

``content-current-commit`` may add ``source_delta`` with ``contract``
``agent-index.source-delta``, explicit integer ``versions``, canonical ``family``,
nonsecret ``account`` and stable nonsecret effective ``target``. All five fields
are required when declaring support. Missing declarations and valid future-only
versions retain the four-verb reconciliation baseline; malformed declarations
refuse negotiation. Package versions never imply support.

Only advertised v1 permits ``content-discover-changed --delta-version 1``.
Its response explicitly includes all five SourceDelta fields plus a required
``source_delta`` scope echo. The echo uses the declaration schema, still supports
v1 and exactly matches the negotiated family/account/target. It describes the
acquisition process's actual effective scope, not a stale cached scope. Changed,
missing or invalid echoes refuse acquisition without another probe or fallback.
File entries use the existing required fields and optional metadata object.
Neither a current cursor nor a partial response authorizes tombstones. This
contract does not advertise acquisition hints. Providers retain legacy syntax
through the consumer upgrade and provider rollback window; retirement requires
a separate contract revision.

Once v1 is negotiated, the legacy-shaped ``content-discover``,
``content-discover-changed`` and ``content-list-paths`` responses also require
this additive echo. Full snapshots, changed entries and path listings must not
cross the negotiated account/target fence. Their argv and baseline fields remain
unchanged; old callers ignore the echo. A direct acquisition on an unnegotiated
connector retains the legacy baseline without requiring another metadata call.

Example additive metadata (``commit`` retains its legacy meaning)::

    {"commit": "tip", "source_delta": {
        "contract": "agent-index.source-delta", "versions": [1],
        "family": "forge:repository", "account": "principal-id",
        "target": "https://forge.example.invalid/api"
    }}

Example complete response to negotiated discovery::

    {"version": 1, "upserts": [], "deletions": [
        {"source": "forge:repository", "path": "removed.md"}
    ], "commit": "delta-tip", "complete": true, "source_delta": {
        "contract": "agent-index.source-delta", "versions": [1],
        "family": "forge:repository", "account": "principal-id",
        "target": "https://forge.example.invalid/api"
    }}

The delta's own ``commit`` owns acquisition coverage; the metadata cursor is
never substituted for it. URL targets exclude user information, query strings
and fragments. Other target identifiers and accounts are provider-owned stable,
nonsecret identifiers, not labels inferred by the caller.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from agent_index.sources.base import FileEntry
from agent_index.sources.delta import DeletedFile, SourceDelta

CONTRACT = "agent-index.source-delta"
SUPPORTED_VERSIONS = (1,)


@dataclass(frozen=True)
class DeltaDeclaration:
    """Runtime-provided source/recovery scope, independent of package labels."""

    versions: tuple[int, ...]
    family: str
    account: str
    target: str

    @property
    def available(self) -> bool:
        return any(version in self.versions for version in SUPPORTED_VERSIONS)


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"source_delta {field!r} must be a nonempty canonical string")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"source_delta {field!r} must not contain control characters")
    return value


def parse_declaration(data: dict[str, Any], requested: str) -> DeltaDeclaration | None:
    """Select only explicitly declared support within the invoked source subtree."""
    if "source_delta" not in data:
        return None
    raw = data["source_delta"]
    if not isinstance(raw, dict) or raw.get("contract") != CONTRACT:
        raise ValueError(f"source_delta must declare contract {CONTRACT!r}")
    versions = raw.get("versions")
    if (
        not isinstance(versions, list) or not versions
        or any(type(version) is not int or version < 1 for version in versions)
        or len(set(versions)) != len(versions)
    ):
        raise ValueError(
            "source_delta 'versions' must be a nonempty distinct positive-integer array",
        )
    family = _identity(raw.get("family"), "family")
    if any(not segment or segment != segment.strip() for segment in family.split(":")) or not (
        family == requested or family.startswith(requested + ":")
    ):
        raise ValueError("source_delta family is outside the requested canonical subtree")
    account = _identity(raw.get("account"), "account")
    target = _identity(raw.get("target"), "target")
    if "://" in target:
        parts = urlsplit(target)
        if not parts.netloc or parts.username is not None or parts.query or parts.fragment:
            raise ValueError(
                "source_delta target must be nonsecret and exclude URL credentials/query",
            )
    return DeltaDeclaration(tuple(versions), family, account, target)


def validate_scope_echo(data: object, declaration: DeltaDeclaration) -> None:
    """Bind any negotiated acquisition response to its original effective scope."""
    if not isinstance(data, dict):
        raise ValueError("source_delta scope echo requires an acquisition response object")
    echo = parse_declaration(data, declaration.family)
    if echo is None or not echo.available or (
        echo.family, echo.account, echo.target
    ) != (
        declaration.family, declaration.account, declaration.target
    ):
        raise ValueError("source_delta scope echo must match negotiated v1 family/account/target")


def parse_delta(
    data: object,
    declaration: DeltaDeclaration,
    parse_entries: Callable[[Any, str], list[FileEntry]],
) -> SourceDelta:
    """Validate the executing acquisition's scope and coverage before any item."""
    fields = {"version", "upserts", "deletions", "commit", "complete", "source_delta"}
    if not isinstance(data, dict) or not fields.issubset(data):
        raise ValueError(
            "source delta response requires version/upserts/deletions/commit/complete/source_delta",
        )
    validate_scope_echo(data, declaration)
    raw_deletions = data["deletions"]
    if not isinstance(raw_deletions, list):
        raise ValueError("source delta deletions must be an array")
    deletions: list[DeletedFile] = []
    for raw in raw_deletions:
        if not isinstance(raw, dict) or not all(
            isinstance(raw.get(field), str) for field in ("source", "path")
        ):
            raise ValueError("source delta tombstone requires string source/path")
        deletions.append(DeletedFile(source=raw["source"], path=raw["path"]))
    delta = SourceDelta(
        version=data["version"],
        upserts=parse_entries({"entries": data["upserts"]}, "content-discover-changed"),
        deletions=deletions,
        commit=data["commit"],
        complete=data["complete"],
    )
    delta.validate(declaration.family)
    return delta
