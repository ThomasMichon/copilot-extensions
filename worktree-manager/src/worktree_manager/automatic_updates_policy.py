"""Explicit, installation-local automatic update consent; never repository discovery."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from zdd.cutover_lock import CutoverLock

SCHEMA = "copilot-extensions.automatic-updates"
POLICY_NAME = "automatic-updates.json"
Owner = Literal["disabled", "manager", "external"]


class PolicyError(RuntimeError):
    """Invalid consent or a conflicting update owner."""


@dataclass(frozen=True)
class Policy:
    schema: str = SCHEMA
    version: int = 1
    owner: Owner = "disabled"
    provenance: str = "default"
    user_disabled: bool = False
    interval_seconds: int = 3600
    project: str | None = None
    external_controller: str | None = None


def state_dir(root: Path) -> Path:
    return root / "automatic-updates"


def atomic_json(path: Path, value: dict[str, object]) -> None:
    """Publish a complete private snapshot, retaining the old one on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise PolicyError(f"refusing symbolic-link state: {path}")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate(data: object) -> Policy:
    if not isinstance(data, dict) or set(data) != set(Policy.__dataclass_fields__):
        raise PolicyError("automatic update policy has missing or unknown fields")
    if data["schema"] != SCHEMA or type(data["version"]) is not int or data["version"] != 1:
        raise PolicyError("unsupported automatic update policy schema/version")
    if data["owner"] not in ("disabled", "manager", "external"):
        raise PolicyError("automatic update owner must be disabled, manager or external")
    if type(data["user_disabled"]) is not bool:
        raise PolicyError("user_disabled must be a boolean")
    if data["user_disabled"] and data["owner"] != "disabled":
        raise PolicyError("explicit user disable conflicts with an enabled owner")
    interval = data["interval_seconds"]
    if type(interval) is not int or not 300 <= interval <= 86400:
        raise PolicyError("interval_seconds must be an integer from 300 to 86400")
    for key in ("provenance", "project", "external_controller"):
        value = data[key]
        optional = key != "provenance"
        if optional and value is None:
            continue
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
            raise PolicyError(f"{key} must be a nonempty, single-line string")
    if (data["owner"] == "external") != (data["external_controller"] is not None):
        raise PolicyError("external ownership requires exactly one named controller")
    if data["owner"] == "manager" and data["project"] is None:
        raise PolicyError("Manager automatic updates require an addressed registered project")
    return Policy(**data)


def load_policy(root: Path) -> Policy:
    path = root / POLICY_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if path.is_symlink():
            raise PolicyError(f"automatic update policy is a dangling symbolic link: {path}")
        return Policy()
    except (OSError, UnicodeError) as exc:
        raise PolicyError(f"cannot read automatic update policy: {exc}") from exc
    try:
        return validate(json.loads(text, object_pairs_hook=_unique_object))
    except (ValueError, TypeError) as exc:
        raise PolicyError(f"invalid automatic update policy: {exc}") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate policy field: {key}")
        result[key] = value
    return result


def configure(
    root: Path, *, owner: Owner, provenance: str, interval_seconds: int = 3600,
    project: str | None = None, external_controller: str | None = None,
    harness: bool = False, held_update: CutoverLock | None = None,
) -> Policy:
    """Explicit setup may recommend enrollment, but cannot erase a user disable.

    Ownership transitions require disabling the old owner first. The CLI also
    checks native startup/in-flight state before changing an enabled owner.
    """
    requested = validate(asdict(Policy(
        owner=owner, provenance=provenance, interval_seconds=interval_seconds,
        project=project, external_controller=external_controller,
        user_disabled=owner == "disabled" and not harness,
    )))
    update_lock = CutoverLock(state_dir(root) / "update-transaction")
    if held_update is not None and held_update.path.absolute() != update_lock.path.absolute():
        raise PolicyError("enrollment requires this installation's update transaction")
    custody = held_update.borrow() if held_update is not None else update_lock
    with custody, CutoverLock(state_dir(root) / "policy-transaction"):
        current = load_policy(root)
        if harness and current.user_disabled:
            return current
        if (
            current.owner != "disabled" and current.owner != requested.owner
            and requested.owner != "disabled"
        ):
            raise PolicyError(f"disable the {current.owner} owner before selecting {owner}")
        if (
            current.owner == requested.owner == "external"
            and current.external_controller != external_controller
        ):
            raise PolicyError("disable the existing external controller before replacing it")
        if harness and current.owner != "disabled":
            existing = asdict(current)
            desired = asdict(requested)
            existing.pop("provenance")
            desired.pop("provenance")
            if existing != desired:
                raise PolicyError("trusted setup cannot replace another enrollment")
            return current
        if current != requested:
            atomic_json(root / POLICY_NAME, asdict(requested))
        return requested


def admit_trigger(root: Path, trigger: str | None, controller: str | None = None) -> Policy:
    policy = load_policy(root)
    if trigger is None:
        return policy  # explicit foreground updates remain supported
    if trigger == "manager" and policy.owner == "manager":
        return policy
    if (
        trigger == "external" and policy.owner == "external"
        and controller == policy.external_controller
    ):
        return policy
    if trigger == "legacy-agent-machines" and not (root / POLICY_NAME).exists():
        return policy
    raise PolicyError(
        f"automatic trigger {trigger!r} refused: effective owner is {policy.owner!r}"
    )
