"""Consume the engine's update auth report over its existing JSON process seam."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from . import engine_client as ec


class AuthRequired(ec.EngineError):
    pass


def environment(source: str, *, cwd: Path | None = None) -> dict[str, str] | None:
    """Return a failure-only environment, or raise before a known-failing fetch.

    The Manager does not import the independently installed engine or implement
    another account resolver, credential router, or persistent auth authority.
    """
    if os.environ.get("CE_UPDATE_AUTH") == "off" or not source.startswith("https://"):
        return None
    if len(urlsplit(source).path.strip("/").split("/")) != 2:
        print(
            "Manager auth report: nested HTTPS source retains existing Git authentication.",
            file=sys.stderr,
        )
        return None
    location = ["--auth-cwd", str(cwd)] if cwd is not None else []
    if cwd is not None and not (cwd / ".git").is_dir():
        location.append("--auth-non-repository")
    try:
        result = ec.run_json(
            None,
            [
                "update",
                "--no-manager",
                "--auth-report",
                "--no-anchor-sync",
                "--auth-source",
                source,
                "--auth-environment",
                "--json",
                *location,
            ],
            allow_nonzero=True,
            timeout=90,
        )
    except ec.EngineError as error:
        detail = ec._engine_error_detail(error)
        unsupported = "unrecognized arguments" in detail and any(
            flag in detail
            for flag in (
                "--auth-report",
                "--auth-environment",
                "--auth-cwd",
                "--auth-non-repository",
                "--auth-source",
            )
        )
        legacy_project_guard = "Could not resolve a project for 'update'" in detail
        if not error.install_hint and not unsupported and not legacy_project_guard:
            raise
        print(
            "Manager auth report unavailable; retaining existing Git authentication.",
            file=sys.stderr,
        )
        return None
    if result.get("supported") is False:
        print(
            "Manager auth report declined this source; retaining existing Git authentication.",
            file=sys.stderr,
        )
        return None
    if result.get("failing") and not result.get("environment"):
        raise AuthRequired("Manager source authentication could not be resolved")
    for item in result.get("results", []):
        if item.get("required") and item.get("status") not in (
            "ok",
            "anonymous",
            "fixable_by_fallback",
        ):
            raise AuthRequired("auth_required: " + str(item.get("fix") or source))
        if item.get("status") == "fixable_by_fallback" and (
            os.environ.get("CE_UPDATE_AUTH") == "report-only"
        ):
            raise AuthRequired("auth_required: report-only did not apply a fallback")
    overlay = result.get("environment")
    if overlay is None:
        raise ec.EngineError("engine did not return its update auth environment")
    if not isinstance(overlay, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in overlay.items()
    ):
        raise ec.EngineError("invalid update auth environment from engine")
    unset = result.get("unset_environment", [])
    if not isinstance(unset, list) or any(not isinstance(name, str) for name in unset):
        raise ec.EngineError("invalid unset environment from engine auth report")
    env = {key: value for key, value in os.environ.items() if key not in unset}
    return ec._engine_environment({**env, **overlay}) if overlay or unset else None
