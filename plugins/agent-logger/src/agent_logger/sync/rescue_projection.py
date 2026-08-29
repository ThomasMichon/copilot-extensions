"""Canonical projection and provenance for rescued sessions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from agent_logger import sessions
from agent_logger.config import Config
from agent_logger.sync.provenance import (
    MAX_PROVENANCE_BYTES,
)
from agent_logger.sync.provenance import (
    SCHEMA_VERSION as PROVENANCE_SCHEMA_VERSION,
)
from agent_logger.sync.rescue_validation import (
    SUPPORTED_PROVIDER,
    Member,
    RescuedSession,
    RescueSourceError,
    read_regular,
    require_directory,
)
from agent_logger.sync.targets import PushResult, Target

_MAX_PROVENANCE_INPUT_BYTES = 1024 * 1024


def metadata_value(session: RescuedSession, member: str) -> dict[str, Any]:
    """Read bounded provenance metadata without executing rescued content."""
    match = next((item for item in session.members if item.relative == member), None)
    if match is None or match.size > _MAX_PROVENANCE_INPUT_BYTES:
        return {}
    try:
        text = read_regular(
            match.path, max_bytes=_MAX_PROVENANCE_INPUT_BYTES
        ).decode("utf-8", errors="strict")
    except (RescueSourceError, UnicodeDecodeError):
        return {}
    if member == "origin.json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return sessions.parse_workspace_text(text)


def _normalized_label(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            normalized = re.sub(
                r"[^a-z0-9_.-]+", "-", value.strip().lower()
            ).strip("-")
            if normalized:
                return normalized
    return None


def build_provenance(session: RescuedSession) -> dict[str, Any]:
    """Build the additive generic v1 provenance sidecar."""
    workspace = metadata_value(session, "workspace.yaml")
    origin = metadata_value(session, "origin.json")
    source_repo = session.source_repo
    repository = next(
        (
            value.strip()
            for value in (
                source_repo,
                workspace.get("repository"),
            )
            if isinstance(value, str) and value.strip()
        ),
        None,
    )
    payload: dict[str, Any] = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "session_id": session.session_id,
        "provider": SUPPORTED_PROVIDER,
        "venue_kind": "container",
        "venue_id": session.venue_id,
        "target_id": session.target_id,
        "container_instance": session.container_instance,
        "container_generation": session.container_generation,
        "fleet": session.fleet,
        "capture_id": session.capture_id,
        "captured_at": session.captured_at,
        "billing_scope": "unknown",
        "repository": repository,
        "source_repo": source_repo,
        "members": {
            (
                "rescued-origin.json"
                if member.relative == "origin.json"
                else member.relative
            ): {"bytes": member.size, "sha256": member.sha256}
            for member in session.members
        },
    }
    optional = {
        "interface": _normalized_label(origin.get("interface"), workspace.get("interface")),
        "origin": _normalized_label(origin.get("origin"), workspace.get("origin")),
        "source": _normalized_label(origin.get("source"), workspace.get("source")),
        "model": next(
            (
                value.strip()
                for value in (origin.get("model"), workspace.get("model"))
                if isinstance(value, str) and value.strip()
            ),
            None,
        ),
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    return payload


def _copy_member(member: Member, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(member.path, flags)
    except OSError as exc:
        raise RescueSourceError(f"cannot open member {member.path}: {exc}") from exc
    try:
        try:
            target_fd = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except OSError as exc:
            raise RescueSourceError(
                f"cannot create projection member {destination}: {exc}"
            ) from exc
        try:
            try:
                digest = hashlib.sha256()
                copied = 0
                opened = os.fstat(source_fd)
                if not stat.S_ISREG(opened.st_mode) or opened.st_size != member.size:
                    raise RescueSourceError(
                        f"member changed before projection: {member.path}"
                    )
                with os.fdopen(source_fd, "rb", closefd=False) as source:
                    with os.fdopen(target_fd, "wb", closefd=False) as target:
                        while chunk := source.read(
                            min(1024 * 1024, member.size - copied + 1)
                        ):
                            target.write(chunk)
                            digest.update(chunk)
                            copied += len(chunk)
                            if copied > member.size:
                                break
                        target.flush()
                        os.fsync(target.fileno())
            finally:
                os.close(target_fd)
        except OSError as exc:
            raise RescueSourceError(f"cannot project {member.path}: {exc}") from exc
    finally:
        os.close(source_fd)
    if copied != member.size or digest.hexdigest() != member.sha256:
        destination.unlink(missing_ok=True)
        raise RescueSourceError(f"member changed before projection: {member.path}")


def _write_provenance(path: Path, payload: dict[str, Any]) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    if len(encoded) > MAX_PROVENANCE_BYTES:
        raise RescueSourceError(
            f"projected provenance exceeds {MAX_PROVENANCE_BYTES} bytes: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(fd)
    except OSError as exc:
        raise RescueSourceError(f"cannot write provenance {path}: {exc}") from exc


def _stage_root(cfg: Config) -> Path:
    root = cfg.home / "rescue-sync" / "staging"
    root.mkdir(parents=True, exist_ok=True)
    require_directory(root, "rescue projection root")
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def push_venue(
    target: Target,
    cfg: Config,
    venue: str,
    selected: list[RescuedSession],
) -> PushResult:
    """Project one venue to canonical source shape, push, then remove it."""
    with tempfile.TemporaryDirectory(prefix="projection-", dir=_stage_root(cfg)) as tmp:
        projection = Path(tmp)
        for session in selected:
            session_dest = projection / "session-state" / session.session_id
            for member in session.members:
                relative = (
                    "rescued-origin.json"
                    if member.relative == "origin.json"
                    else member.relative
                )
                _copy_member(member, session_dest / relative)
            _write_provenance(
                projection / "provenance" / f"{session.session_id}.json",
                build_provenance(session),
            )
        return target.push(
            projection,
            venue,
            {session.session_id for session in selected},
        )
