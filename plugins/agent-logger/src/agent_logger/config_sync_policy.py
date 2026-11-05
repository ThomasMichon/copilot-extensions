"""Machine-local sync retention and typed-collection configuration."""

from __future__ import annotations

from typing import Any


class SyncPolicyConfig:
    """Resolved sync policies, separate from the core configuration map."""

    _data: dict[str, Any]

    @property
    def sync_retention_days(self) -> int | None:
        """Retention in days, or ``None`` to retain everything.

        Accepts the sentinel strings ``infinite``/``forever``/``never`` (and
        blank) as "retain all".
        """
        raw = self._data.get("sync", {}).get("retention_days")
        if raw is None:
            return None
        if isinstance(raw, str):
            if raw.strip().lower() in {"infinite", "forever", "never", "none", ""}:
                return None
            try:
                return int(raw)
            except ValueError:
                return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @property
    def sync_worktree_state_enabled(self) -> bool:
        """Explicit machine-local opt-in to the separately admitted typed leg."""
        raw = self._data.get("sync", {}).get("worktree_state", {})
        if (
            not isinstance(raw, dict)
            or set(raw) - {"enabled"}
            or type(raw.get("enabled", False)) is not bool
        ):
            raise ValueError("sync.worktree_state requires a mapping with boolean enabled")
        return raw.get("enabled", False)
