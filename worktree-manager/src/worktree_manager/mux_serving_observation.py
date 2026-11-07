"""Read the resident Manager mux owner's actual serving generation."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import secrets
import stat
from pathlib import Path

from zdd import diagnostics, routing

from . import mux_daemon, mux_daemon_cutover, mux_daemon_process

SCHEMA = "worktree-manager.mux-serving-observation"
VERSION = 1


class ServingObservationError(RuntimeError):
    """The Manager serving state cannot be established."""


def _existing_token(root: Path) -> tuple[str, tuple]:
    path = mux_daemon_cutover.control_token_path(root)
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
        or getattr(before, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        or not 0 < before.st_size <= 4096
    ):
        raise ServingObservationError("existing Manager control credential is unavailable")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        content = stream.read(4097)
        after = os.fstat(stream.fileno())
    named = path.lstat()
    if (
        len(content) > 4096 or not os.path.samestat(before, opened)
        or not os.path.samestat(opened, after) or not os.path.samestat(opened, named)
        or opened.st_size != after.st_size or opened.st_mtime_ns != after.st_mtime_ns
        or opened.st_size != named.st_size or opened.st_mtime_ns != named.st_mtime_ns
    ):
        raise ServingObservationError("Manager control credential changed during observation")
    token = content.decode("utf-8").strip()
    if not token:
        raise ServingObservationError("existing Manager control credential is unavailable")
    return token, (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)


def _live_owner(endpoint: routing.Endpoint) -> bool:
    from agent_procutil._daemon_instance_identity import birth_identity

    try:
        birth_identity(endpoint.pid)
    except (OSError, RuntimeError, ValueError):
        return False
    return diagnostics.process_start_time(endpoint.pid) == endpoint.process_start_time


def _matches_health_owner(health: dict, root: Path, endpoint: routing.Endpoint, token: str) -> bool:
    if "pid" in health or "process_start_time" in health:
        return (
            type(health.get("pid")) is int and health["pid"] == endpoint.pid
            and health.get("process_start_time") == endpoint.process_start_time
        )
    record = mux_daemon.read_lock_data(mux_daemon.lock_path(root))
    if not isinstance(record, dict):
        return False
    credential = record.get("manager_mux_token")
    return (
        type(record.get("pid")) is int and record["pid"] == endpoint.pid
        and record.get("start_time") == endpoint.process_start_time
        and record.get("manager_mux_endpoint") == routing.format_authority(endpoint.bind, endpoint.port)
        and isinstance(credential, str) and secrets.compare_digest(credential, token)
    )


def observe(root: Path, *, timeout: float = 2.0) -> dict:
    """Observe only; never start a daemon, create credentials, or change routing."""
    try:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ServingObservationError("Manager observation timeout is invalid")
        root = Path(root).resolve(strict=True)
        if not root.is_dir():
            raise ServingObservationError("Manager root is unavailable")
        directory = mux_daemon_cutover.routing_dir(root)
        table = routing.read_table(directory, strict=True)
        raw = table.get("active") if table is not None else None
        if raw is None:
            if (
                table is not None
                or os.path.lexists(routing.routing_table_path(directory))
                or os.path.lexists(directory / routing._LOCK_FILENAME)
                or os.path.lexists(mux_daemon_cutover._cutover_lock_path(root))
                or os.path.lexists(mux_daemon.lock_path(root))
                or os.path.lexists(mux_daemon_process.spawn_lock_path(root))
            ):
                raise ServingObservationError("unrouted Manager ownership is unconfirmed")
            return {"state": "inactive", "generation": None}
        endpoint = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if (
            endpoint is None or endpoint.bind != "127.0.0.1"
            or type(raw.get("port")) is not int or not 0 < endpoint.port < 65536
            or type(raw.get("pid")) is not int or endpoint.pid <= 0
            or type(raw.get("generation")) is not int or endpoint.generation <= 0
            or not isinstance(endpoint.version, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,255}", endpoint.version) is None
            or endpoint.version.endswith(".")
            or not endpoint.process_start_time
            or not _live_owner(endpoint)
        ):
            raise ServingObservationError("routed Manager ownership is unconfirmed")
        token, token_identity = _existing_token(root)
        health = mux_daemon_cutover.ControlClient(
            endpoint.base_url, root=root, timeout=min(timeout, 2.0), token=token,
        ).health()
        if (
            not isinstance(health, dict) or health.get("status") != "ready"
            or health.get("active") is not True
            or health.get("runtime_generation") != endpoint.version
            or not _matches_health_owner(health, root, endpoint, token)
            or routing.read_table(directory, strict=True) != table
            or not _live_owner(endpoint)
            or _existing_token(root) != (token, token_identity)
            or not _matches_health_owner(health, root, endpoint, token)
        ):
            raise ServingObservationError("routed Manager readiness is unconfirmed")
        return {"state": "ready", "generation": endpoint.version}
    except ServingObservationError:
        raise
    except Exception:  # noqa: BLE001 -- never expose owner diagnostics
        raise ServingObservationError("Manager serving state is unavailable") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = {"schema": SCHEMA, "version": VERSION, "ok": True, **observe(args.root)}
    except ServingObservationError:
        result = {"schema": SCHEMA, "version": VERSION, "ok": False, "state": "unknown"}
    print(json.dumps(result), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
