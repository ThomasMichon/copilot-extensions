"""Credential-relay helpers owned by the agent registry."""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from agent_procutil import no_window_flags

log = logging.getLogger("agent-bridge")


class FileTokenValidator:
    """A file-backed relay-token validator."""

    __slots__ = ("_path",)

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def __call__(self, token: str) -> bool:
        if not token:
            return False
        try:
            data = json.loads(self._path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(data, dict):
            return False
        for value in data.values():
            secret = value if isinstance(value, str) else (
                value.get("token") if isinstance(value, dict) else None
            )
            if not isinstance(secret, str) or not secret:
                continue
            try:
                if secrets.compare_digest(token, secret):
                    return True
            except TypeError:
                return False
        return False


class FileTokenAuthorizer:
    """A file-backed, request-scoped relay-token authorizer.

    Mirrors ``agent_codespaces.relay_token.authorize_azure`` for the
    degrade-safe CLI seam (``agent-codespaces relay-profile``): both read the
    same on-host token store, so both must raise
    :class:`credential_relay.server.ScopeDenied` -- not merely return
    ``False`` -- for a *recognized* token whose specific scope is denied
    (#4367), so this path also gets a wire-visible denial via the server's
    token gate instead of a silent closed connection.
    """

    __slots__ = ("_path", "_static")

    def __init__(
        self, path: str | Path, static_resources: list[str] | None = None,
    ) -> None:
        self._path = Path(path)
        self._static = {
            str(resource).removesuffix("/.default").rstrip("/")
            for resource in (static_resources or [])
        }

    def __call__(self, token: str, action: str, fields: dict[str, str]) -> bool:
        if action != "get-azure-token" or not token:
            return False
        try:
            data = json.loads(self._path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(data, dict):
            return False
        from credential_relay.server import ScopeDenied

        requested = fields.get("scope") or fields.get("resource") or ""
        normalized = requested.removesuffix("/.default").rstrip("/")
        for entry in data.values():
            secret = entry if isinstance(entry, str) else (
                entry.get("token") if isinstance(entry, dict) else None
            )
            if not isinstance(secret, str) or not secret:
                continue
            try:
                if not secrets.compare_digest(token, secret):
                    continue
            except TypeError:
                return False
            if isinstance(entry, dict) and "allowed_resources" in entry:
                allowed = {
                    str(value).removesuffix("/.default").rstrip("/")
                    for value in entry.get("allowed_resources", [])
                }
            else:
                allowed = self._static
            if "*" in allowed or normalized in allowed:
                return True
            raise ScopeDenied
        return False


def _relay_source_by_name(name: str, profile: dict | None = None):
    """Construct a shared ``credential_relay`` source by its profile name."""
    from credential_relay.sources.gh_auth import GhAuthSource
    from credential_relay.sources.git_credential import GitCredentialSource

    profile = profile or {}
    if name == "git-credential":
        return GitCredentialSource(github_hosts=profile.get("github_hosts"))
    if name == "gh-auth":
        return GhAuthSource(github_hosts=profile.get("github_hosts"))
    return None


def _apply_relay_profile(builder, profile: dict) -> None:
    """Apply a declarative provider relay profile to ``builder``."""
    for source_name in profile.get("sources", []):
        source = _relay_source_by_name(source_name, profile)
        if source is not None:
            builder.add_source(source)
        else:
            log.warning("Unknown relay source '%s' in profile -- skipping", source_name)
    builder.set_port(profile.get("port"))
    builder.set_ado_host(profile.get("ado_host"))
    if "azure_resources" in profile:
        builder.allow_azure_resources(list(profile["azure_resources"] or []))
    gated = profile.get("gated_actions") or []
    store = profile.get("token_store")
    if gated and store:
        if profile.get("scoped_azure"):
            builder.authorize_token(
                list(gated),
                FileTokenAuthorizer(store, profile.get("azure_resources")),
            )
        else:
            builder.require_token(list(gated), FileTokenValidator(store))


def _relay_profile_via_cli(binstub: str) -> dict | None:
    """Fetch a provider's declarative relay profile via ``<binstub> relay-profile``."""
    exe = shutil.which(binstub)
    if not exe:
        return None
    try:
        result = subprocess.run(
            [exe, "relay-profile"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=no_window_flags(),
        )
    except Exception:
        log.debug("relay-profile CLI failed for %s", binstub, exc_info=True)
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
        return data if isinstance(data, dict) else None
    except Exception:
        log.warning("relay-profile output unparseable (%s)", binstub, exc_info=True)
        return None


#: Backoff (seconds) between ``relay-profile`` retries for a provider that is
#: registered in ``providers.d`` but whose CLI seam is momentarily unavailable
#: (e.g. its runtime is mid-update during a bridge cutover). Bounded so a truly
#: broken provider can't stall relay startup for long.
_PROFILE_RETRY_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)


def _provider_registered(binstub: str) -> bool:
    """Whether ``binstub`` has a ``providers.d/<binstub>.json`` manifest."""
    try:
        from .provider_sources import providers_dir

        return (providers_dir() / f"{binstub}.json").is_file()
    except Exception:
        return False


def _register_provider_relay(
    builder, binstub: str, *, sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Register one provider's relay profile via its ``relay-profile`` CLI seam.

    A provider that is not installed contributes nothing (debug log). A provider
    that *is* registered in ``providers.d`` but whose seam fails is retried with
    bounded backoff; if it never answers, a WARNING is logged, because the relay
    would otherwise run for its whole lifetime without that provider's token
    gate and silently deny its gated actions.
    """
    profile = _relay_profile_via_cli(binstub)
    if profile is None and _provider_registered(binstub):
        for delay in _PROFILE_RETRY_DELAYS:
            log.info(
                "%s relay-profile unavailable but provider is registered -- "
                "retrying in %.0fs", binstub, delay,
            )
            sleep(delay)
            profile = _relay_profile_via_cli(binstub)
            if profile is not None:
                break
        if profile is None:
            log.warning(
                "%s is a registered bridge provider but its relay-profile is "
                "unavailable; its credential-relay sources and token gate are "
                "NOT active (gated actions such as get-azure-token will be "
                "denied). Run `agent-bridge service restart` once %s is healthy.",
                binstub, binstub,
            )
            return
    if profile is None:
        log.debug("%s relay-profile unavailable -- no relay sources", binstub)
        return
    try:
        _apply_relay_profile(builder, profile)
        log.info("Applied credential-relay profile (%s, CLI seam)", binstub)
    except Exception:
        log.warning("Failed applying %s relay profile", binstub, exc_info=True)


def register_credential_sources(builder) -> None:
    """Auto-discover and inject credential-relay sources from optional providers."""
    _register_provider_relay(builder, "agent-codespaces")
    _register_provider_relay(builder, "agent-containers")
