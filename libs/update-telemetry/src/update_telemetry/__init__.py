"""Shared update-run telemetry: JSONL events, a folded status file, exit codes.

Stdlib only. One process creates a run (the *owner*) and exports its identity
through the environment; child processes that find it set join as
*participants* and only append events. See ``README.md`` for the schemas.
"""

from __future__ import annotations

from .fold import STATUS_SCHEMA, STATUS_VERSION, fold, fold_phases, fold_targets, run_outcome
from .outcomes import (
    AUTH_REQUIRED,
    BLOCKED_BY_LOCK,
    COMMAND_FAILED,
    CONFIGURATION_LIMITATION,
    EXIT_CODES,
    EXIT_INTERRUPTED,
    EXIT_USAGE,
    IN_PROGRESS,
    INTERRUPTED,
    LATE_SUCCESS,
    OUTCOMES,
    REQUIRES_RESTART,
    SEVERITY,
    SKIPPED,
    TIMEOUT,
    VERIFIED_CURRENT,
    classify_exception,
    exit_code_for,
    is_failure,
    outcome_for_exit_code,
    outcome_for_installer_exit,
    worst,
)
from .redact import redact, redact_argv
from .run import (
    ENV_EVENTS,
    ENV_HOME,
    ENV_OWNER,
    ENV_RUN_ID,
    PhaseResult,
    Run,
    read_events,
    update_home,
)

__all__ = [
    "AUTH_REQUIRED",
    "BLOCKED_BY_LOCK",
    "COMMAND_FAILED",
    "CONFIGURATION_LIMITATION",
    "ENV_EVENTS",
    "ENV_HOME",
    "ENV_OWNER",
    "ENV_RUN_ID",
    "EXIT_CODES",
    "EXIT_INTERRUPTED",
    "EXIT_USAGE",
    "INTERRUPTED",
    "IN_PROGRESS",
    "LATE_SUCCESS",
    "OUTCOMES",
    "REQUIRES_RESTART",
    "SEVERITY",
    "SKIPPED",
    "STATUS_SCHEMA",
    "STATUS_VERSION",
    "TIMEOUT",
    "VERIFIED_CURRENT",
    "PhaseResult",
    "Run",
    "classify_exception",
    "exit_code_for",
    "fold",
    "fold_phases",
    "fold_targets",
    "is_failure",
    "outcome_for_exit_code",
    "outcome_for_installer_exit",
    "read_events",
    "redact",
    "redact_argv",
    "run_outcome",
    "update_home",
    "worst",
]
