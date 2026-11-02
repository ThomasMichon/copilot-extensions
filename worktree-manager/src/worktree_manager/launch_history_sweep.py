"""Bounded metadata reads from explicitly selected configured SSH Managers."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import shlex
import shutil
import subprocess
from time import monotonic

from machine_transport import resolve_machine_selection, resolve_ssh_target, wrap_remote_command
from remote_login_shell import is_posix_login_shell
from ssh_manager.config_sources import SSHProfileSource
from ssh_manager.process import terminate_ssh_process_tree
from ssh_manager.proxy import create_ssh_subprocess

from . import launch_attempts, machine_roster

MAX_SOURCES = 8
SOURCE_TIMEOUT = 20
TOTAL_TIMEOUT = 120
MAX_RESPONSE_BYTES = 1024 * 1024
_FILTERS = (
    "before", "before_id", "after", "project", "action", "machine", "environment",
    "provider", "venue", "outcome",
)
_FIELDS = {
    "attempt_id", "submitted_at", "action", "project", "surface", "machine", "environment",
    "provider", "venue_id", "is_local", "worktree_id", "inputs_available", "manager_outcome",
    "last_manager_event", "manager_outcome_scope",
}


class SweepError(ValueError):
    """A sanitized source failure; never includes remote output."""


def query_argv(args) -> list[str]:
    """Validate before resolving a registry or opening a transport."""
    if (
        not args.sweep_project or not args.source_machine
        or not 1 <= len(args.source_machine) <= MAX_SOURCES
        or any(not name.strip() or len(name) > 256 or "\0" in name for name in args.source_machine)
        or args.attempt_id is not None or args.include_input or args.summary
        or args.event_limit != 100 or args.after_sequence != 0
        or args.receipt_limit != 100 or args.receipt_after is not None
        or not 1 <= args.limit <= 100
        or any(value is not None and (not math.isfinite(value) or value < 0)
               for value in (args.before, args.after))
        or args.action is not None and args.action not in launch_attempts._ACTIONS
    ):
        raise SweepError("invalid-query")
    if args.before_id is not None:
        try:
            launch_attempts._identity(args.before_id)
        except (ValueError, TypeError, AttributeError) as exc:
            raise SweepError("invalid-query") from exc
        if args.before is None:
            raise SweepError("invalid-query")
    result = ["launch-attempts", "--json", "--limit", str(args.limit)]
    for key in _FILTERS:
        value = getattr(args, key)
        if value is not None:
            result += ["--" + key.replace("_", "-"), str(value)]
    return result


def remote_command(shell: str, query: list[str]) -> str:
    """Use the target Manager's non-provisioning, published absolute front door."""
    if shell == "pwsh":
        quoted = " ".join("'" + part.replace("'", "''") + "'" for part in query)
        script = (
            "$ErrorActionPreference='Stop'; "
            "$p=Join-Path $env:USERPROFILE '.local\\bin\\worktree-manager.ps1'; "
            "if(-not(Test-Path -LiteralPath $p -PathType Leaf)){exit 127}; "
            f"& $p {quoted}; exit $LASTEXITCODE"
        )
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return "pwsh -NoLogo -NoProfile -NonInteractive -EncodedCommand " + encoded
    if not is_posix_login_shell(shell):
        raise SweepError("unsupported-shell")
    return wrap_remote_command(
        shell, 'exec "$HOME/.local/bin/worktree-manager" ' + shlex.join(query),
    )


async def _capture(stream, *, retain: bool) -> bytes:
    data = bytearray()
    count = 0
    while chunk := await stream.read(65536):
        count += len(chunk)
        if count > MAX_RESPONSE_BYTES:
            raise SweepError("response-too-large")
        if retain:
            data.extend(chunk)
    return bytes(data)


async def query_source(entry, query: list[str], *, timeout: float) -> bytes:
    output, return_code = await query_source_result(entry, query, timeout=timeout)
    if return_code != 0:
        raise SweepError("target-reader-unavailable")
    return output


async def query_source_result(entry, query: list[str], *, timeout: float) -> tuple[bytes, int]:
    """Retain the target exit separately so exact detail can preserve a validated partial."""
    if len(entry.ssh_environments) > 1:
        raise SweepError("ambiguous-ssh-profile")
    alias, shell = resolve_ssh_target(entry)
    if not entry.ssh_ready or not alias or not shell:
        raise SweepError("ssh-not-ready")
    profiles = [profile for profile in entry.ssh_environments if profile.alias == alias]
    if len(profiles) != 1:
        raise SweepError("ambiguous-ssh-profile")
    profile = profiles[0]
    command = remote_command(shell, query)
    ssh = shutil.which("ssh")
    if not ssh:
        raise SweepError("ssh-unavailable")
    process = None
    readers = []
    try:
        async with asyncio.timeout(timeout):
            config = await asyncio.to_thread(
                SSHProfileSource(
                    alias, user=profile.user, port=profile.port if profile.port != 22 else None,
                    resolve_timeout=min(5, timeout),
                ).get_ssh_config,
            )
            connection = []
            if config.port:
                connection += ["-p", str(config.port)]
            if config.user:
                connection += ["-l", config.user]
            process = await create_ssh_subprocess(
                ssh, "-T", "-a", "-x", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                "-o", "ConnectionAttempts=1", "-o", "ControlMaster=no",
                "-o", "ControlPath=none", "-o", "ClearAllForwardings=yes",
                "-o", "PermitLocalCommand=no", "-o", "StrictHostKeyChecking=yes",
                "-o", "UpdateHostKeys=no", "-o", "RemoteCommand=none",
                *connection, "--", alias, command,
                config=config, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            readers = [
                asyncio.create_task(_capture(process.stdout, retain=True)),
                asyncio.create_task(_capture(process.stderr, retain=False)),
            ]
            output, _ = await asyncio.gather(*readers)
            return output, await process.wait()
    except (TimeoutError, subprocess.TimeoutExpired) as exc:
        raise SweepError("source-timeout") from exc
    except (OSError, RuntimeError) as exc:
        raise SweepError("transport-unavailable") from exc
    finally:
        for reader in readers:
            if not reader.done():
                reader.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
        if process is not None:
            await terminate_ssh_process_tree(process, grace=1)


def validate_metadata(row: dict) -> None:
    if not isinstance(row, dict) or set(row) != _FIELDS:
        raise ValueError("Invalid metadata")
    if (
        type(row["is_local"]) not in (bool, type(None))
        or type(row["inputs_available"]) is not bool
        or type(row["submitted_at"]) not in (float, int)
        or row["manager_outcome_scope"] != "manager-boundary"
    ):
        raise ValueError("Invalid metadata types")
    raw = {key: row[key] for key in _FIELDS - {
        "manager_outcome", "manager_outcome_scope", "last_manager_event",
    }}
    raw["is_local"] = -1 if row["is_local"] is None else int(row["is_local"])
    raw["inputs_available"] = int(row["inputs_available"])
    launch_attempts._metadata(raw, version=2)
    event = row["last_manager_event"]
    if event is not None and (
        not isinstance(event, dict)
        or set(event) != {"sequence", "observed_at", "phase", "details"}
        or type(event["sequence"]) is not int or event["sequence"] <= 0
        or type(event["observed_at"]) not in (int, float)
    ):
        raise ValueError("Invalid Manager observation")
    observation = (None, None, None, None) if event is None else (
        event["sequence"], event["observed_at"], event["phase"], json.dumps(event["details"]),
    )
    if row["manager_outcome"] != launch_attempts._manager_outcome(*observation):
        raise ValueError("Invalid Manager outcome")


def validate_page(output: bytes, args) -> dict:
    """An older, noisy or overbroad target must not become empty success."""
    try:
        if len(output) > MAX_RESPONSE_BYTES:
            raise SweepError("response-too-large")
        value = json.loads(output)
        if (
            not isinstance(value, dict)
            or set(value) != {
                "schema", "version", "scope", "attempts", "has_more", "next_before", "next_before_id",
            }
            or value["schema"] != "worktree-manager-launch-history"
            or type(value["version"]) is not int or value["version"] != 1
            or value["scope"] != "local-manager" or type(value["has_more"]) is not bool
            or not isinstance(value["attempts"], list) or len(value["attempts"]) > args.limit
        ):
            raise ValueError("Invalid list")
        previous = None
        for row in value["attempts"]:
            validate_metadata(row)
            position = (row["submitted_at"], row["attempt_id"])
            if previous is not None and position >= previous:
                raise ValueError("Invalid list ordering")
            previous = position
            for key in ("project", "action", "machine", "environment", "provider", "outcome", "venue"):
                field = {"outcome": "manager_outcome", "venue": "venue_id"}.get(key, key)
                if getattr(args, key) is not None and row[field] != getattr(args, key):
                    raise ValueError("Ignored list filter")
            if (
                args.after is not None and row["submitted_at"] < args.after
                or args.before is not None and position >= (args.before, args.before_id or "")
            ):
                raise ValueError("Ignored time cursor")
        cursor = (value["next_before"], value["next_before_id"])
        if value["has_more"] and (
            type(cursor[0]) not in (int, float) or not math.isfinite(cursor[0])
            or not isinstance(cursor[1], str)
        ):
            raise ValueError("Invalid cursor types")
        expected = previous if value["has_more"] else (None, None)
        if cursor != expected or value["has_more"] and len(value["attempts"]) != args.limit:
            raise ValueError("Invalid continuation")
        return value
    except SweepError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise SweepError("incompatible-response") from exc


async def read_sweep(args, *, read_source=None) -> dict:
    query = query_argv(args)
    report = {
        "schema": "worktree-manager-launch-history-sweep", "version": 1,
        "scope": "selected-configured-ssh-managers", "roster_project": args.sweep_project,
        "atomic": False, "prompt_acceptance": "unknown", "sources": [], "errors": [],
        "limits": {
            "sources": MAX_SOURCES, "rows_per_source": args.limit,
            "source_timeout_seconds": SOURCE_TIMEOUT, "total_timeout_seconds": TOTAL_TIMEOUT,
            "response_bytes_per_source": MAX_RESPONSE_BYTES,
        },
    }
    try:
        entries = machine_roster.selected_entries(args.sweep_project)
    except (OSError, ValueError):
        report["errors"].append({"source": "configured-roster", "code": "unavailable-or-invalid"})
        report["sources"] = [
            {"selection": name, "status": "unavailable", "code": "roster-unavailable"}
            for name in args.source_machine
        ]
        return report
    deadline = monotonic() + TOTAL_TIMEOUT
    selected = set()
    for name in args.source_machine:
        source = {
            "selection": name, "machine": None, "environment": None,
            "identity_basis": "configured-ssh-profile", "status": "unavailable",
        }
        report["sources"].append(source)
        try:
            try:
                selection = resolve_machine_selection(entries, name)
            except ValueError as exc:
                raise SweepError("ambiguous-or-invalid-selection") from exc
            entry = selection.entry
            if entry is None:
                raise SweepError("not-configured")
            source["machine"] = entry.key
            source["environment"] = selection.target.environment
            if entry.key in selected:
                raise SweepError("duplicate-source")
            selected.add(entry.key)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise SweepError("sweep-deadline")
            output = await (read_source or query_source)(
                entry, query, timeout=min(SOURCE_TIMEOUT, remaining),
            )
            source["page"] = validate_page(output, args)
            source["status"] = "read"
        except SweepError as exc:
            source["code"] = str(exc)
        except (OSError, ValueError, RuntimeError):
            source["code"] = "transport-unavailable"
    return report
