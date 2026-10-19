"""HTTP credential providers use the attributed native provider command seam."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from dataclasses import dataclass

from credential_relay import RelayBuilder

from .provider_process import run_captured
from .provider_sources import ProviderManifest, scan_provider_registry

log = logging.getLogger("agent-bridge")
_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


@dataclass(frozen=True)
class ProviderHttpSource:
    """Adapt one explicitly advertised source without importing its package."""

    command: tuple[str, ...]
    source_id: str

    @property
    def name(self) -> str:
        return self.source_id

    def supports(self, action: str, fields: dict[str, str]) -> bool:
        return action == "get"

    async def resolve(
        self, action: str, fields: dict[str, str], *, timeout: float = 15.0,
    ) -> str:
        if action != "get" or any(
            not isinstance(key, str) or not isinstance(value, str)
            or any(char in key for char in "=\r\n") or any(char in value for char in "\r\n")
            for key, value in fields.items()
        ):
            raise PermissionError("HTTP credential provider request refused")
        wire = ("\n".join(f"{key}={value}" for key, value in fields.items()) + "\n\n").encode("utf-8")
        try:
            result = await asyncio.to_thread(
                run_captured,
                [*self.command, "http-credential", self.source_id, action],
                timeout, input_bytes=wire,
            )
        except (OSError, RuntimeError, UnicodeError, subprocess.TimeoutExpired):
            raise PermissionError("HTTP credential provider unavailable") from None
        if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 65536:
            raise PermissionError("HTTP credential provider refused")
        return result.stdout


def _register_profile(builder: RelayBuilder, manifest: ProviderManifest) -> None:
    result = run_captured([*manifest.command, "relay-profile"], timeout=20.0)
    if result.returncode != 0:
        raise ValueError("HTTP credential provider profile unavailable")
    profile = json.loads(result.stdout)
    entries = profile.get("http_sources") if isinstance(profile, dict) else None
    if not isinstance(entries, list):
        raise ValueError("HTTP credential provider profile requires http_sources")
    validated: list[tuple[str, bool]] = []
    for entry in entries:
        if (
            not isinstance(entry, dict) or set(entry) != {"id", "scoped"}
            or not isinstance(entry["id"], str) or not _SOURCE_ID.fullmatch(entry["id"])
            or type(entry["scoped"]) is not bool
        ):
            raise ValueError("Invalid HTTP credential provider source declaration")
        validated.append((entry["id"], entry["scoped"]))
    for source_id, scoped in validated:
        builder.add_http_source(
            f"{manifest.plugin}/{source_id}",
            ProviderHttpSource(manifest.command, source_id), scoped=scoped,
        )


def register_http_credential_sources(builder: RelayBuilder) -> None:
    """Register only opted-in, enabled, attributable providers; never get secrets."""
    report = scan_provider_registry()
    seen: set[tuple[str | None, tuple[str, ...]]] = set()
    for finding in report.findings:
        log.warning("HTTP credential provider registry: %s", finding.reason)
    for manifest in report.entries.values():
        if manifest.schema_version != 1 or manifest.http_credentials_version != 1:
            continue
        identity = manifest.plugin, manifest.command
        if identity in seen:
            continue
        seen.add(identity)
        try:
            _register_profile(builder, manifest)
        except (ValueError, TypeError, OSError, RuntimeError, UnicodeError, subprocess.TimeoutExpired):
            log.warning("HTTP credential provider profile unavailable or invalid: %s", manifest.plugin)
