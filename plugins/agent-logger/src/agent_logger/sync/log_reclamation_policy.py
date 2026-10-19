"""Strict policy parsing for machine-local, all-target process-log reclamation."""

from __future__ import annotations

from dataclasses import dataclass

from agent_logger.sync.log_archives import LogArchiveSettings


@dataclass(frozen=True)
class LogReclamationSettings:
    enabled: bool = False
    min_age_seconds: int | None = None
    required_targets: tuple[str, ...] = ()


def resolve_log_reclamation_settings(
    raw: object, *, process_logs_enabled: bool, archives: LogArchiveSettings,
    active_target: str,
) -> LogReclamationSettings:
    """Require an explicit age and complete target selection, never infer either."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("process_logs.reclamation must be an object")
    if set(raw) - {"enabled", "min_age_seconds", "required_targets"}:
        raise ValueError("process_logs.reclamation contains unknown settings")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ValueError("process_logs.reclamation.enabled must be a boolean")
    age = raw.get("min_age_seconds")
    if age is not None and (
        isinstance(age, bool) or not isinstance(age, int) or age <= 0
    ):
        raise ValueError("process-log reclamation min_age_seconds must be a positive integer")
    names = raw.get("required_targets", [])
    if not isinstance(names, list) or any(
        not isinstance(name, str) or not name or name != name.strip() for name in names
    ):
        raise ValueError("process-log reclamation required_targets must be a list of target names")
    if len(set(names)) != len(names):
        raise ValueError("process-log reclamation required_targets contains duplicates")
    if enabled:
        if age is None:
            raise ValueError("process-log reclamation requires explicit min_age_seconds")
        if not names or active_target not in names:
            raise ValueError("process-log reclamation required_targets must include the active target")
        if not process_logs_enabled or not archives.enabled or not archives.local_source:
            raise ValueError("process-log reclamation requires local process logs and ZIP snapshots")
    return LogReclamationSettings(enabled, age, tuple(names))
