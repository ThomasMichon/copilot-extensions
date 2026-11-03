"""Bounded descriptors for allocated worktree data, not domain-store validation."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

SCHEMA = "copilot-extensions.worktree-data"
VERSION = 1
MAX_DESCRIPTOR_BYTES = 64 * 1024
MAX_PLACEMENTS = 32
_SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\Z")
_LANE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_STORE = re.compile(r"[0-9a-f]{32}\Z")
_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class WorktreeDataError(ValueError):
    """A stable refusal code and human-readable detail for the data boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def segment(value: Any, label: str) -> str:
    """Validate one portable, exact path component without normalizing aliases."""
    if (
        not isinstance(value, str)
        or not _SEGMENT.fullmatch(value)
        or value.endswith(".")
        or value.split(".", 1)[0].casefold() in _RESERVED
    ):
        raise WorktreeDataError("invalid-identity", f"invalid {label}")
    return value


def store_identity(value: Any) -> str:
    """Accept only the domain owner's lowercase 32-hex store identity."""
    if not isinstance(value, str) or not _STORE.fullmatch(value):
        raise WorktreeDataError("invalid-identity", "invalid store_id")
    return value


def generation(value: Any) -> int:
    """Validate a positive placement fence, excluding JSON booleans."""
    if type(value) is not int or value < 1:
        raise WorktreeDataError("invalid-descriptor", "invalid placement_generation")
    return value


def lane_identity(domain: Any, lane: Any) -> tuple[str, str]:
    """Validate the explicitly supported domain and one portable lane."""
    if domain != "agendas" or not isinstance(domain, str):
        raise WorktreeDataError("unsupported-domain", "only agendas data is supported")
    if not isinstance(lane, str) or not _LANE.fullmatch(lane) or lane.casefold() in _RESERVED:
        raise WorktreeDataError("invalid-identity", "invalid lane")
    return domain, lane


@dataclass(frozen=True)
class DataScope:
    """Repository and physical project owner selected by existing resolution."""

    project: str
    repository: str
    repository_id: str
    marketplace_id: str | None
    project_root_device: int
    project_root_inode: int


@dataclass(frozen=True)
class PlacementRegistration:
    """One current admitted location; domain contents remain independently owned."""

    domain: str
    lane: str
    store_id: str
    placement_generation: int
    relative_home: str


@dataclass(frozen=True)
class DataDescriptor:
    """The complete bounded persistent registration for one worktree incarnation."""

    scope: DataScope
    worktree_id: str
    creation_nonce: str
    placements: tuple[PlacementRegistration, ...]
    schema: str = SCHEMA
    version: int = VERSION


@dataclass(frozen=True)
class WorktreeDataPlacement:
    """An allocated home, never proof of initialized data or execution authority."""

    scope: DataScope
    worktree_id: str
    creation_nonce: str
    domain: str
    lane: str
    store_id: str
    placement_generation: int
    state_home: str
    state_home_allocated: bool = True
    store_content_validated: bool = False
    live_authority: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-compatible fields without inferring domain-store health."""
        return asdict(self)


@dataclass(frozen=True)
class WorktreeDataInventory:
    """Passive registration evidence; missing canonical identity stays explicit."""

    scope: DataScope
    worktree_id: str
    creation_nonce: str
    placements: tuple[WorktreeDataPlacement, ...]
    canonical_record: str
    live_authority: bool = False
    schema: str = SCHEMA
    version: int = VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible inventory."""
        result = asdict(self)
        result["placements"] = [placement.to_dict() for placement in self.placements]
        return result


def _fields(value: Any, expected: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise WorktreeDataError("invalid-descriptor", "descriptor members do not match schema")
    return value


def _unique_members(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WorktreeDataError("invalid-descriptor", "duplicate descriptor member")
        result[key] = value
    return result


def decode_descriptor(raw: bytes) -> DataDescriptor:
    """Decode bounded strict JSON; unknown schemas never look like empty state."""
    if len(raw) > MAX_DESCRIPTOR_BYTES:
        raise WorktreeDataError("descriptor-limit", "descriptor exceeds byte limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_members)
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, WorktreeDataError):
            raise
        raise WorktreeDataError("invalid-descriptor", "descriptor is not valid JSON") from error
    if not isinstance(value, dict):
        raise WorktreeDataError("invalid-descriptor", "descriptor is not an object")
    if value.get("schema") != SCHEMA:
        raise WorktreeDataError("unsupported-schema", "unsupported worktree-data schema")
    if type(value.get("version")) is not int:
        raise WorktreeDataError("invalid-descriptor", "descriptor version is not an integer")
    if value["version"] != VERSION:
        raise WorktreeDataError("unsupported-version", "unsupported worktree-data version")
    _fields(value, {"schema", "version", "scope", "worktree_id", "creation_nonce", "placements"})
    scope = _fields(
        value["scope"],
        {
            "project",
            "repository",
            "repository_id",
            "marketplace_id",
            "project_root_device",
            "project_root_inode",
        },
    )
    for field in ("project", "repository", "repository_id"):
        segment(scope[field], field)
    marketplace = scope["marketplace_id"]
    if marketplace is not None:
        segment(marketplace, "marketplace_id")
    for field in ("project_root_device", "project_root_inode"):
        if type(scope[field]) is not int or scope[field] < 0:
            raise WorktreeDataError("invalid-descriptor", f"invalid {field}")
    entries = value["placements"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PLACEMENTS:
        raise WorktreeDataError("descriptor-limit", "invalid placement inventory size")
    placements: list[PlacementRegistration] = []
    lanes: set[tuple[str, str]] = set()
    stores: set[str] = set()
    for entry in entries:
        _fields(
            entry,
            {
                "domain",
                "lane",
                "store_id",
                "placement_generation",
                "relative_home",
            },
        )
        domain, lane = lane_identity(entry["domain"], entry["lane"])
        store = store_identity(entry["store_id"])
        if (domain, lane) in lanes or store in stores:
            raise WorktreeDataError("identity-conflict", "ambiguous placement inventory")
        if entry["relative_home"] != f"{domain}/{lane}":
            raise WorktreeDataError("unsafe-path", "noncanonical placement path")
        lanes.add((domain, lane))
        stores.add(store)
        placements.append(
            PlacementRegistration(
                domain,
                lane,
                store,
                generation(entry["placement_generation"]),
                entry["relative_home"],
            )
        )
    return DataDescriptor(
        DataScope(**scope),
        segment(value["worktree_id"], "worktree_id"),
        segment(value["creation_nonce"], "creation_nonce"),
        tuple(placements),
    )


def encode_descriptor(value: DataDescriptor) -> bytes:
    """Serialize strict JSON before any allocation effects, using the same codec."""
    raw = (json.dumps(asdict(value), sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    decode_descriptor(raw)
    return raw
