#!/usr/bin/env python3
"""Resolve, validate, and explicitly mutate marketplace installation context."""
from __future__ import annotations

from pathlib import Path

_FRAGMENT_FILES = (
    "_installation_context_base.py",
    "_installation_context_files.py",
    "_installation_context_source.py",
    "_installation_context_lock_recovery.py",
    "_installation_context_receipts.py",
    "_installation_context_snapshot.py",
    "_installation_context_runtime_slot_ownership.py",
    "_installation_context_runtime_slot_completion.py",
    "_installation_context_resolution.py",
    "_installation_context_guest_environment.py",
    "_installation_context_activation.py",
    "_installation_context_legacy_attribution.py",
    "_installation_context_legacy_transition.py",
    "_installation_context_legacy_retirement.py",
    "_installation_context_maintenance.py",
    "_installation_context_mode_cli.py",
    "_installation_context_deployment.py",
    "_installation_context_publication.py",
    "_installation_context_powershell.py",
)


def _fragment_directory(script: Path) -> Path:
    # Resolving a bootstrap-held GLOBALROOT path reintroduces the mutable DOS map.
    if script.drive.casefold() == "\\\\?\\globalroot" and script.root == "\\":
        return script.parent
    return script.resolve().parent


def _load_fragments() -> None:
    fragment_dir = _fragment_directory(Path(__file__))
    for fragment_name in _FRAGMENT_FILES:
        fragment_path = fragment_dir / fragment_name
        exec(  # noqa: S102 - Fixed canonical fragments share the module namespace.
            compile(fragment_path.read_text(encoding="utf-8"), str(fragment_path), "exec"),
            globals(),
        )


_load_fragments()


if __name__ == "__main__":
    raise SystemExit(globals()["main"]())
