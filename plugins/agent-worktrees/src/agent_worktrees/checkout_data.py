"""Preserve the documented checkout-local agenda fallback during disposal."""

from __future__ import annotations

from pathlib import Path


def preservation_reason(checkout: str | Path) -> str | None:
    """Presence or uncertain inspection requires owner-led rescue, not deletion."""
    try:
        (Path(checkout) / ".effort-agenda").lstat()
    except FileNotFoundError:
        return None
    except OSError:
        return (
            "cannot inspect checkout-local .effort-agenda; retain the checkout "
            "until its agenda owner reconciles preservation"
        )
    return (
        "checkout-local .effort-agenda requires preservation; retain the checkout "
        "until its agenda owner verifies rescue and explicitly retires the source"
    )
