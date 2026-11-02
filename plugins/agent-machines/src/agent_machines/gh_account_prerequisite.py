"""Read-only, explicitly sourced GitHub CLI account prerequisites."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import PurePosixPath, PureWindowsPath
import shutil
import subprocess
from typing import Any

from agent_procutil.owned_process import (
    OutputLimitExceeded,
    run_caller_owned,
)

_MAX_STATUS_BYTES = 64 * 1024
_ENV_SOURCES = frozenset(
    {
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GH_ENTERPRISE_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
    }
)


def run_status(argv: list[str]) -> subprocess.CompletedProcess:
    executable = shutil.which(argv[0])
    if executable is None:
        raise FileNotFoundError("GitHub CLI is unavailable")
    env = dict(os.environ)
    env.pop("GH_DEBUG", None)
    env["GH_PROMPT_DISABLED"] = "1"
    result = run_caller_owned(
        [executable, *argv[1:]],
        timeout=5,
        max_stdout_bytes=_MAX_STATUS_BYTES,
        max_stderr_bytes=8 * 1024,
        env=env,
    )
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate status key")
        result[key] = value
    return result


def _source_kind(source: str) -> str | None:
    if source in _ENV_SOURCES:
        return "environment"
    if source == "keyring":
        return "stored"
    if (
        PureWindowsPath(source).is_absolute() or PurePosixPath(source).is_absolute()
    ) and source.replace("\\", "/").rsplit("/", 1)[-1] == "hosts.yml":
        return "stored"
    return None


def check_gh_account(
    host: str,
    login: str,
    credential_source: str,
    *,
    runner: Callable[[list[str]], Any] | None = None,
) -> tuple[str | None, list[str]]:
    """Return a safe failure code (or None), never raw CLI output or credentials."""
    argv = [
        "gh",
        "auth",
        "status",
        "--hostname",
        host,
        "--json",
        "hosts",
        "--no-refresh",
    ]
    try:
        outcome = (runner or run_status)(argv)
    except subprocess.TimeoutExpired:
        return "probe-timeout", argv
    except UnicodeError:
        return "invalid-status", argv
    except (OSError, OutputLimitExceeded):
        return "probe-unavailable", argv
    if outcome.returncode != 0:
        return "probe-failed-or-unsupported", argv
    try:
        if len(outcome.stdout.encode("utf-8")) > _MAX_STATUS_BYTES:
            raise ValueError("status exceeds its bound")
        payload = json.loads(outcome.stdout, object_pairs_hook=_unique_object)
        hosts = payload["hosts"]
        if not isinstance(hosts, dict):
            raise ValueError("invalid host inventory")
        matches = [rows for name, rows in hosts.items() if name.casefold() == host.casefold()]
        if not matches:
            return "account-missing", argv
        if len(matches) != 1 or not isinstance(matches[0], list):
            raise ValueError("ambiguous host inventory")
        accounts = []
        for row in matches[0]:
            if (
                not isinstance(row, dict)
                or not all(
                    isinstance(row.get(key), str)
                    for key in (
                        "host",
                        "login",
                        "state",
                        "tokenSource",
                    )
                )
                or type(row.get("active")) is not bool
                or row["host"].casefold() != host.casefold()
            ):
                raise ValueError("invalid account evidence")
            if row["login"].casefold() != login.casefold():
                continue
            source = _source_kind(row["tokenSource"])
            if source is None:
                return "credential-source-unknown", argv
            if source == credential_source:
                accounts.append(row)
        if not accounts:
            return "account-or-credential-source-missing", argv
        if len(accounts) != 1:
            return "account-ambiguous", argv
        if accounts[0]["state"] != "success":
            return "account-not-healthy", argv
    except (ValueError, TypeError, KeyError, UnicodeError):
        return "invalid-status", argv
    return None, argv
