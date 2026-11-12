"""Current deny-only input checks, usable before semantic tooling admission."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import re

from runtime_profile_errors import ProfileError


def verify_digests_not_revoked(
    selected, revoked_inputs: Callable[[], frozenset[str]] | None,
) -> None:
    if revoked_inputs is None:
        return
    denied = revoked_inputs()
    if not isinstance(denied, frozenset) or any(
        not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None for value in denied
    ):
        raise ProfileError("runtime revocation evidence must be an exact immutable digest set")
    if set(selected) & denied:
        raise ProfileError("selected source runtime inputs have been revoked")


def verify_not_revoked(receipt: dict, revoked_inputs: Callable[[], frozenset[str]] | None) -> None:
    """Consume current deny-only evidence; it cannot approve an installation."""
    if revoked_inputs is None:
        return
    def digest(record):
        return "sha256:" + hashlib.sha256(json.dumps(
            record, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()

    binding = receipt["binding"]
    selected = {
        receipt["lock_id"], digest(binding["profile"]),
        *[wheel["sha256"] for wheel in receipt["distributions"].values()],
    }
    if "build_profile" in binding:
        selected.add(digest(binding["build_profile"]))
        selected.update(wheel["sha256"] for wheel in binding["build_profile"]["wheels"])
    locked = binding.get("source_lock", {})
    if locked:
        selected.add(digest(locked))
    selected.update(
        value for key, value in locked.items()
        if key.endswith("_sha256") and isinstance(value, str)
    )
    selected.update(wheel["sha256"] for wheel in locked.get("files", ()))
    if isinstance(locked.get("source"), dict):
        selected.add("sha256:" + locked["source"]["assembly_hash"])
    verify_digests_not_revoked(selected, revoked_inputs)
