"""Host-selected canonical policy support over existing receiving transports."""

from __future__ import annotations

import io
import json
import zipfile

from repository_policy import AgentHostingError
from repository_policy.receiving import build_receiving_policy_archive, build_receiving_policy_check


def selected_context() -> str:
    """Bind the portable receiver to this Bridge installation, not target env."""
    from .hosting_policy import marketplace_id

    return json.dumps({
        "schema": "agent-bridge.receiving-policy",
        "version": 1,
        "marketplaceId": marketplace_id(),
    }, separators=(",", ":"))


def policy_sources() -> tuple[tuple[str, bytes], ...]:
    """Reuse the canonical resolver, portable YAML and their license notices."""
    _reader, payload = build_receiving_policy_archive()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return tuple((name, archive.read(name)) for name in sorted(archive.namelist()))


def guest_policy_check(workspace: str, operation: str) -> tuple[str, bytes]:
    """Prepare a fresh read by our selected receiver, independent of guest suite."""
    return build_receiving_policy_check(workspace, selected_context(), operation)


def codespace_receiving_command(metadata: dict) -> str:
    """Select an advertised raw command only for the enforcing bundled Host."""
    declaration = metadata.get("portable_receiving_policy")
    if declaration is None:
        return metadata.get("acp_command") or ""
    if (
        not isinstance(declaration, dict)
        or type(declaration.get("version")) is not int or declaration["version"] != 1
        or not isinstance(declaration.get("acp_command"), str)
        or not declaration["acp_command"].strip()
    ):
        raise AgentHostingError("Unsupported CodeSpace portable receiving policy declaration")
    return declaration["acp_command"]
