"""Canonical enforcement for an explicitly selected portable receiving owner."""

from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import shlex
import zipfile
from typing import cast

from . import AgentHostingError, RepositoryAgentPolicy, require_agent_hosting


def receiving_marketplace(context: str) -> str:
    """Decode peer-selected scope; this declaration grants no execution authority."""
    try:
        selected = json.loads(context)
    except (TypeError, ValueError) as exc:
        raise AgentHostingError("Invalid Bridge receiving policy context") from exc
    if (
        not isinstance(selected, dict)
        or selected.get("schema") != "agent-bridge.receiving-policy"
        or type(selected.get("version")) is not int or selected["version"] != 1
        or not isinstance(selected.get("marketplaceId"), str)
    ):
        raise AgentHostingError("Unsupported Bridge receiving policy context")
    return cast(str, selected["marketplaceId"])


def require_receiving_policy(
    cwd: str | os.PathLike[str] | None, context: str, *, operation: str = "launch",
) -> RepositoryAgentPolicy:
    """Read the actual checkout using the selected owner's canonical evaluator."""
    if not cwd:
        raise AgentHostingError("Repository hosting receiver requires an actual execution directory")
    return require_agent_hosting(
        cwd, marketplace_id=receiving_marketplace(context), operation=operation,
    )


def build_receiving_policy_archive() -> tuple[str, bytes]:
    """Reuse the legacy archive's source/license closure with an explicit receiver."""
    from .guest_probe import build_guest_policy_probe

    _reader, payload = build_guest_policy_probe()
    stream = io.BytesIO(payload)
    with zipfile.ZipFile(stream, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(Path(__file__), "repository_policy/receiving.py")
    reader = Path(__file__).with_name("_receiving_reader.py").read_text(encoding="utf-8")
    return reader, stream.getvalue()


def build_receiving_policy_check(
    workspace: str, context: str, operation: str = "launch", *, text_transport: bool = False,
) -> tuple[str, bytes]:
    """Return one isolated receiving command and its bounded stdin payload."""
    if not isinstance(workspace, str) or not workspace.startswith("/") or "\0" in workspace:
        raise AgentHostingError("Repository hosting requires an absolute guest execution checkout")
    receiving_marketplace(context)
    reader, payload = build_receiving_policy_archive()
    encoding = "base64" if text_transport else "zip"
    if text_transport:
        payload = base64.b64encode(payload)
    command = (
        f"cd -- {shlex.quote(workspace)} && python3 -I -S -c {shlex.quote(reader)} "
        + " ".join(shlex.quote(value) for value in (workspace, context, operation, encoding))
    )
    return command, payload
