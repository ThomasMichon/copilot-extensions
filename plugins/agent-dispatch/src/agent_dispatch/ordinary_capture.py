"""Prospective relationship capture policy, independent of pool allocation."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .pool_instances import pool_instance_identity
from .registrar_json import unique_json_fields


@dataclass(frozen=True)
class OrdinaryCapture:
    """New-allocation scope; retained reservations carry their own decision."""

    scope: str = "local-ordinary"
    instance: tuple[str, str, str] | None = None

    def selects(self, winner: tuple[str, str, str]) -> bool:
        """Match the actual winner, never the reserving caller or a candidate subset."""
        return self.scope == "local-ordinary" or self.instance == winner


def configured_capture(spec: str | None, authority: str | None) -> OrdinaryCapture | None:
    """Decode native modes before any fallback to a trusted alternate factory."""
    prefix = "local-ordinary-instance="
    if spec in ("local-ordinary", "local-ordinary-drain"):
        capture = OrdinaryCapture(spec)
    elif isinstance(spec, str) and spec.startswith(prefix):
        try:
            identity = pool_instance_identity(
                json.loads(spec[len(prefix):], object_pairs_hook=unique_json_fields)
            )
            if any(
                part != part.strip() or not part.isprintable() or any(c in part for c in "*?[]")
                for part in identity
            ):
                raise ValueError("ambiguous pool identity")
        except (ValueError, RecursionError) as exc:
            raise ValueError("local ordinary admission requires one exact pool instance") from exc
        capture = OrdinaryCapture("local-ordinary-instance", identity)
    else:
        return None
    if authority != "local":
        raise ValueError("local ordinary relationship admission requires local queue authority")
    return capture
