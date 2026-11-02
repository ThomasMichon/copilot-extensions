"""Protected allocation responsibility, distinct from absence or peer retirement."""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from . import resource_producer as producer
from .resource_relationship import valid_request_id

SCHEMA = "copilot-extensions.worktree-allocation"
VERSION = 1
METADATA_VERSION = 3
LIFETIME = "owned-worktree"
NAMESPACE = "worktree-allocation"
PHASES = frozenset({"pending", "bound", "confirmed", "closed"})
MAX_IDENTITY_BYTES = 4096


def _text(value: object, *, maximum: int = 1024) -> bool:
    return (
        isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _component(value: object) -> bool:
    return (
        isinstance(value, str) and _text(value, maximum=256)
        and not any(character.isspace() or character in "/\\#" for character in value)
    )


def qualified_ref(value: object) -> tuple[str, str, str]:
    """Return one exact worktree identity, never an anchor or session alias."""
    from . import tracking

    if not isinstance(value, str) or not _text(value):
        raise ValueError("allocation requires an exact qualified worktree reference")
    parsed = tracking.parse_claim_ref(value)
    if (
        parsed is None or not parsed.is_qualified
        or parsed.machine is None or parsed.project is None
        or parsed.session or parsed.is_anchor
        or not all(_component(part) for part in (parsed.machine, parsed.project, parsed.worktree_id))
        or parsed.canonical() != value
    ):
        raise ValueError("allocation requires an exact qualified worktree reference")
    return parsed.machine, parsed.project, parsed.worktree_id


def valid_target(value: object) -> bool:
    """Validate bounded recovery facts without inventing target-local paths."""
    if not isinstance(value, dict) or set(value) - {
        "machine", "project", "options", "authority",
    } or not {"machine", "project"}.issubset(value):
        return False
    if any(not _component(value[key]) for key in ("machine", "project")):
        return False
    if "options" in value and not isinstance(value["options"], dict):
        return False
    if "authority" in value and not _text(value["authority"]):
        return False
    try:
        producer.canonical_json(value)
    except (ValueError, TypeError, RecursionError):
        return False
    return True


@dataclass(frozen=True)
class AllocationIdentity:
    ref: str
    creation_nonce: str
    path: str
    branch: str

    @classmethod
    def from_dict(cls, value: object) -> AllocationIdentity:
        if not isinstance(value, dict) or set(value) != {
            "ref", "creation_nonce", "path", "branch",
        }:
            raise ValueError("allocation identity requires ref, creation_nonce, path and branch")
        qualified_ref(value["ref"])
        if not _text(value["creation_nonce"], maximum=128) or not _text(value["branch"]):
            raise ValueError("allocation identity has an invalid creation nonce or branch")
        path = value["path"]
        if not _text(path, maximum=4096) or not (
            PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute()
        ):
            raise ValueError("allocation identity requires an absolute receiving path")
        if len(producer.canonical_json(value)) > MAX_IDENTITY_BYTES:
            raise ValueError("allocation identity exceeds lifecycle size limit")
        return cls(value["ref"], value["creation_nonce"], path, value["branch"])

    def to_dict(self) -> dict[str, str]:
        return {
            "ref": self.ref, "creation_nonce": self.creation_nonce,
            "path": self.path, "branch": self.branch,
        }


def claim_ref(metadata: dict) -> str:
    """Bound responsibility uses the existing native worktree reference."""
    if "identity" in metadata:
        return AllocationIdentity.from_dict(metadata["identity"]).ref
    envelope = {
        "version": VERSION,
        **{key: metadata[key] for key in ("creator", "request_id", "token")},
    }
    encoded = base64.urlsafe_b64encode(
        producer.canonical_json(envelope).encode("ascii"),
    ).decode("ascii").rstrip("=")
    ref = f"{NAMESPACE}:w1.{encoded}"
    if len(ref.encode("ascii")) > producer.MAX_REF_BYTES:
        raise ValueError("pending worktree allocation reference exceeds size limit")
    return ref


def origin_response(metadata: dict, ref: str) -> dict:
    """Keep admission's wire-size reservation tied to the actual origin response."""
    return {
        "schema": SCHEMA, "version": VERSION, "ok": True, "owner": metadata["creator"],
        **{key: metadata[key] for key in ("creator", "request_id", "token", "phase")},
        "ref": ref, "producer": copy.deepcopy(metadata),
    }


def wire_json(value: dict) -> str:
    """Use one bounded encoding for admission and actual CLI transport, including LF."""
    encoded = producer.canonical_json(value) + "\n"
    if len(encoded) > producer.MAX_INPUT_BYTES:
        raise ValueError("allocation JSON wire response exceeds size limit")
    return encoded


def require_lifecycle_capacity(creator: str, request_id: str, token: str, target: dict) -> None:
    """Reserve room for both the largest admitted identity and exact closeout."""
    metadata = {
        "version": METADATA_VERSION, "lifetime": LIFETIME, "namespace": NAMESPACE,
        "creator": creator, "request_id": request_id, "token": token,
        "recovery": target, "phase": "closed",
    }
    proof = {
        "schema": SCHEMA, "version": VERSION, "origin": creator,
        "request_id": request_id, "token": token, "target": target,
        "identity": None, "status": "cancelled", "allocation_finished": True,
    }
    cancelled = {**metadata, "closeout": proof}
    # These are serialization stand-ins, not receiving identities. Their
    # encoded lengths bound every identity/ref admitted by the real validators.
    largest_identity = "x" * (MAX_IDENTITY_BYTES - 2)
    ref_size = min(MAX_IDENTITY_BYTES, len(producer.canonical_json(
        f"{target['machine']}/{target['project']}/" + "\U0010ffff" * 256,
    )))
    closed = {
        **metadata, "identity": largest_identity,
        "closeout": {**proof, "identity": largest_identity, "status": "finalized"},
    }
    try:
        wire_json(origin_response(cancelled, claim_ref(cancelled)))
        wire_json(origin_response(closed, "x" * (ref_size - 2)))
    except ValueError as exc:
        raise ValueError("allocation lacks lifecycle metadata capacity") from exc


def verified_conclusion(result: object, metadata: dict) -> bool:
    """Require the original receiving operation's exact lifecycle conclusion."""
    if not isinstance(result, dict) or set(result) != {
        "schema", "version", "origin", "request_id", "token", "target",
        "identity", "status", "allocation_finished",
    }:
        return False
    if (
        result["schema"] != SCHEMA or type(result["version"]) is not int
        or result["version"] != VERSION or result["allocation_finished"] is not True
        or not isinstance(result["status"], str)
        or result["origin"] != metadata["creator"]
        or result["request_id"] != metadata["request_id"]
        or result["token"] != metadata["token"]
    ):
        return False
    try:
        if producer.canonical_json(result["target"]) != producer.canonical_json(metadata["recovery"]):
            return False
        if producer.canonical_json(result["identity"]) != producer.canonical_json(metadata.get("identity")):
            return False
    except (ValueError, TypeError, RecursionError):
        return False
    return (
        result["status"] in {"finalized", "archived"} if "identity" in metadata
        else result["status"] == "cancelled"
    )


def verified_observation(result: object, metadata: dict) -> bool:
    """Accept only a finished exact receiving operation, never an absence result."""
    if not isinstance(result, dict) or set(result) != {
        "schema", "version", "origin", "request_id", "token", "target",
        "identity", "status", "allocation_finished",
    }:
        return False
    if (
        result["schema"] != SCHEMA or type(result["version"]) is not int
        or result["version"] != VERSION or result["allocation_finished"] is not True
        or result["origin"] != metadata["creator"]
        or result["request_id"] != metadata["request_id"]
        or result["token"] != metadata["token"]
        or not isinstance(result["status"], str)
        or result["status"] not in {"allocated", "confirmed", "finalized", "archived", "cancelled"}
    ):
        return False
    try:
        producer.canonical_json(result)
        if producer.canonical_json(result["target"]) != producer.canonical_json(metadata["recovery"]):
            return False
        if result["status"] == "cancelled":
            return result["identity"] is None and "identity" not in metadata
        identity = AllocationIdentity.from_dict(result["identity"])
        if qualified_ref(identity.ref)[:2] != (
            metadata["recovery"]["machine"], metadata["recovery"]["project"],
        ):
            return False
        return "identity" not in metadata or identity.to_dict() == metadata["identity"]
    except (ValueError, TypeError, KeyError, RecursionError):
        return False


def valid_metadata(value: object) -> bool:
    """Unknown or damaged worktree responsibility never becomes settled state."""
    required = {
        "version", "lifetime", "namespace", "token", "request_id",
        "creator", "phase", "recovery",
    }
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - (
        required | {"identity", "closeout"}
    ):
        return False
    if (
        type(value["version"]) is not int or value["version"] != METADATA_VERSION
        or value["lifetime"] != LIFETIME or value["namespace"] != NAMESPACE
        or not producer.is_valid_token(value["token"])
        or not valid_request_id(value["request_id"])
        or not isinstance(value["phase"], str) or value["phase"] not in PHASES
        or not valid_target(value["recovery"])
    ):
        return False
    if value["phase"] in {"bound", "confirmed"} and "identity" not in value:
        return False
    if value["phase"] == "pending" and "identity" in value:
        return False
    try:
        qualified_ref(value["creator"])
        require_lifecycle_capacity(
            value["creator"], value["request_id"], value["token"], value["recovery"],
        )
        if "identity" in value:
            identity = AllocationIdentity.from_dict(value["identity"])
            machine, project, _ = qualified_ref(identity.ref)
            if (machine, project) != (
                value["recovery"]["machine"], value["recovery"]["project"],
            ):
                return False
        producer.canonical_json(value)
        claim_ref(value)
    except (ValueError, TypeError, KeyError, RecursionError):
        return False
    return (
        verified_conclusion(value.get("closeout"), value) if value["phase"] == "closed"
        else "closeout" not in value
    )
