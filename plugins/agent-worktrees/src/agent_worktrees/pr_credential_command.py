"""Explicit machine-only credential command; no issuer, cache or ambient fallback."""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned

from .providers.observation_credentials import CredentialSource, ObservationCredentialError

_MAX_BYTES = 65536


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
) -> CredentialSource:
    """Validate trusted non-secret argv once; acquire new typed material on every call."""
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

    def acquire() -> _Material:
        try:
            timeout = 15.0
            if remaining is not None:
                budget = remaining()
                if type(budget) not in (int, float) or not math.isfinite(budget) or budget <= 0:
                    raise ValueError("credential command operation budget exhausted")
                timeout = min(timeout, budget)
            result = run_caller_owned(
                list(argv), timeout=timeout, max_stdout_bytes=_MAX_BYTES, max_stderr_bytes=8192,
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
