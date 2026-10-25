"""Read the optional, inert Manager automatic-update ownership contract."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zdd.cutover_lock import CutoverLock

LEGACY_FLEET_TRIGGER = "legacy-agent-machines"
_FIELDS = {
    "schema",
    "version",
    "owner",
    "provenance",
    "user_disabled",
    "interval_seconds",
    "project",
    "external_controller",
}


@dataclass(frozen=True)
class UpdateOwnership:
    state: str
    path: Path
    detail: str

    @property
    def blocks_legacy_schedule(self) -> bool:
        return self.state != "standalone"

    @property
    def hands_off_suite(self) -> bool:
        return self.state in {"manager", "external", "disabled"}

    def refusal(self, *, automatic: bool = False) -> str | None:
        if self.state == "refused":
            return self.detail
        if self.state in {"manager", "external"}:
            return f"suite updates belong to the {self.state} owner; use its canonical update path"
        if automatic and self.state == "disabled":
            return "automatic suite updates are explicitly disabled"
        return None


def ownership_policy_path(home: Path | None = None) -> Path:
    root = os.environ.get("WORKTREE_MANAGER_ROOT")
    if root:
        return Path(root).expanduser() / "automatic-updates.json"
    base = home or Path(os.environ.get("USERPROFILE") or Path.home())
    return base / ".worktree-manager" / "automatic-updates.json"


def suite_update_transaction(home: Path | None = None) -> CutoverLock:
    root = ownership_policy_path(home).parent
    return CutoverLock(root / "automatic-updates" / "update-transaction")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate policy field: {key}")
        result[key] = value
    return result


def _nonempty(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(ord(character) < 32 for character in value)
    )


def validate_ownership_payload(payload: Any) -> str:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ValueError("policy must contain exactly the version-1 contract fields")
    if payload["schema"] != "copilot-extensions.automatic-updates":
        raise ValueError("unsupported automatic-update policy schema")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("unsupported automatic-update policy version")
    owner = payload["owner"]
    if not isinstance(owner, str) or owner not in {"disabled", "manager", "external"}:
        raise ValueError("invalid automatic-update owner")
    if not _nonempty(payload["provenance"]):
        raise ValueError("policy provenance must be a nonempty string")
    if type(payload["user_disabled"]) is not bool:
        raise ValueError("policy user_disabled must be boolean")
    if payload["user_disabled"] and owner != "disabled":
        raise ValueError("explicit user disable conflicts with an enabled owner")
    interval = payload["interval_seconds"]
    if type(interval) is not int or not 300 <= interval <= 86400:
        raise ValueError("policy interval_seconds must be an integer from 300 through 86400")
    if payload["project"] is not None and not _nonempty(payload["project"]):
        raise ValueError("policy project must be null or a nonempty string")
    if owner == "manager" and payload["project"] is None:
        raise ValueError("manager ownership requires a nonempty project")
    controller = payload["external_controller"]
    if owner == "external":
        if not _nonempty(controller):
            raise ValueError("external ownership requires a nonempty external_controller")
    elif controller is not None:
        raise ValueError("external_controller must be null unless owner is external")
    return owner


def read_update_ownership(home: Path | None = None) -> UpdateOwnership:
    """Absent means standalone; unreadable or invalid never means absent."""
    path = ownership_policy_path(home)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if path.is_symlink():
            return UpdateOwnership("refused", path, f"automatic-update policy is dangling: {path}")
        return UpdateOwnership(
            "standalone", path, "no automatic-update policy; standalone behavior"
        )
    except (OSError, UnicodeError) as exc:
        return UpdateOwnership(
            "refused", path, f"cannot read automatic-update policy {path}: {exc}"
        )
    try:
        payload = json.loads(text, object_pairs_hook=_unique_object)
        owner = validate_ownership_payload(payload)
        return UpdateOwnership(owner, path, f"automatic-update owner: {owner}")
    except (ValueError, TypeError) as exc:
        return UpdateOwnership(
            "refused", path, f"automatic-update policy refused at {path}: {exc}"
        )
