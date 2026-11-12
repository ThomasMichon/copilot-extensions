"""Explicit machine-only credential command; no issuer, cache or ambient fallback."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
from agent_procutil.owned_budget import OwnedExecutionBudget

from .providers.observation_credentials import (
    MAX_SNAPSHOT_REQUESTS, CredentialSource, ObservationCredentialError,
)

_MAX_BYTES = 65536


def add_snapshot_arguments(parser: argparse.ArgumentParser) -> None:
    """Register only the explicit typed snapshot credential options."""
    parser.add_argument(
        "--credential-command", default=None,
        help="Trusted non-secret JSON argv acquiring typed credentials per native request",
    )
    parser.add_argument(
        "--max-credential-requests", type=int, default=None,
        help="Lower typed credential acquisition limit (1..64; not a physical HTTP-send limit)",
    )


def snapshot_source(args: argparse.Namespace) -> CredentialSource | None:
    """Refuse invalid typed selection before configuration or material access."""
    if args.max_credential_requests is not None and args.credential_command is None:
        raise ValueError("--max-credential-requests requires --credential-command")
    if args.credential_command is None:
        return None
    if args.token is not None:
        raise ValueError("--credential-command cannot be combined with --token")
    return command_source(
        args.credential_command, max_acquisitions=args.max_credential_requests,
    )


@dataclass(frozen=True)
class _Forge:
    provider: str
    api_base: str
    repository: str
    permission: str
    authentication: str


@dataclass(frozen=True)
class _Material:
    token: str = field(repr=False)
    expires_at: int | float
    forge: _Forge


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate credential field")
        value[key] = item
    return value


def command_source(
    serialized: str, *, remaining: Callable[[], float] | None = None,
    enclosing_budget: OwnedExecutionBudget | None = None,
    max_acquisitions: int | None = None,
) -> CredentialSource:
    """Validate trusted non-secret argv once; acquire new typed material on every call."""
    if max_acquisitions is not None and (
        type(max_acquisitions) is not int or not 1 <= max_acquisitions <= MAX_SNAPSHOT_REQUESTS
    ):
        raise ValueError("credential acquisition limit must be 1..64")
    try:
        if not isinstance(serialized, str) or len(serialized.encode("utf-8")) > 16384:
            raise ValueError
        argv = json.loads(serialized)
        if (
            not isinstance(argv, list) or not 1 <= len(argv) <= 64
            or any(not isinstance(part, str) or not part or "\0" in part for part in argv)
            or not Path(argv[0]).is_absolute() or not Path(argv[0]).is_file()
            or Path(argv[0]).suffix.lower() in (".cmd", ".bat")
        ):
            raise ValueError
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise ValueError("an explicit absolute credential command JSON argv is required") from None

    acquisitions = 0

    def acquire() -> _Material:
        nonlocal acquisitions
        if max_acquisitions is not None and acquisitions >= max_acquisitions:
            raise ObservationCredentialError("credential acquisition limit exhausted", transient=True)
        acquisitions += 1
        try:
            timeout = 15.0
            if remaining is not None:
                budget = remaining()
                if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
                    raise ValueError("credential command operation budget exhausted")
                timeout = min(timeout, budget)
            if enclosing_budget is not None:
                timeout = min(timeout, enclosing_budget.execution_deadline - time.monotonic())
                if timeout <= 0:
                    raise ValueError("enclosing credential command budget exhausted")
            result = run_caller_owned(
                list(argv), timeout=timeout, max_stdout_bytes=_MAX_BYTES, max_stderr_bytes=8192,
                **({"budget": enclosing_budget, "borrow_parent_scope": True}
                   if enclosing_budget is not None else {}),
            )
            if result.returncode or len(result.stdout.encode("utf-8")) > _MAX_BYTES:
                raise ValueError
            value = json.loads(result.stdout, object_pairs_hook=_unique)
            if (
                not isinstance(value, dict)
                or set(value) != {"schema", "version", "token", "expires_at", "forge"}
                or value["schema"] != "fleet-manager.forge-credential"
                or type(value["version"]) is not int or value["version"] != 1
                or not isinstance(value["forge"], dict)
                or set(value["forge"]) != set(_Forge.__dataclass_fields__)
                or any(not isinstance(part, str) for part in value["forge"].values())
            ):
                raise ValueError
            return _Material(value["token"], value["expires_at"], _Forge(**value["forge"]))
        except OwnedCleanupError:
            raise
        except (OSError, RuntimeError, ValueError, UnicodeError, subprocess.SubprocessError):
            raise ObservationCredentialError(
                "PR observation credential command unavailable", transient=True,
            ) from None

    return acquire
