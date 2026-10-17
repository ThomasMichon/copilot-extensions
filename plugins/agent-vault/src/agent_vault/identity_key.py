"""Shared owner-key provisioning and non-secret source context."""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
from pathlib import Path

from . import config
from .identity_context import identity_cache_digest

SERVICE_FAILURE_HINTS = {
    "needs_unlock": "Unlock the selected vault or explicitly retry with --prompt.",
    "entry_missing": "Synchronize the existing shared owner entry; never initialize another key.",
    "malformed_key": "Existing owner key is malformed; inspect it without rotating or overwriting.",
    "ambiguous_entry": "Vault contains ambiguous or malformed paths; inspect before retrying.",
    "vault_read_failed": "Authoritative vault read failed; restore access/synchronization and retry.",
    "creation_unconfirmed": "Creation is unconfirmed; inspect the authoritative vault before retrying.",
    "group_create_failed": "Owner group creation failed; inspect the authoritative vault.",
    "operation_expired": "Operation expired; inspect the authoritative vault before retrying.",
    "service_busy": "Vault is busy; retry after the current operation.",
    "confirmation_required": "First-owner initialization requires both explicit consent flags.",
    "invalid_entry": "Select an unambiguous slash-delimited owner entry path.",
    "service_unavailable": "Configured vault service unavailable; check its installation and retry.",
}


class IdentityKeyError(RuntimeError):
    """Sanitized, actionable identity-key failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_key(value: str) -> str:
    if not isinstance(value, str):
        raise IdentityKeyError("malformed_key", "Owner key must be a canonical base64 string for 32 bytes.")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        decoded = b""
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != value:
        raise IdentityKeyError(
            "malformed_key", "Existing owner key is not canonical base64 for 32 bytes; never replace it."
        )
    return value


def source_context() -> dict:
    from .cache import CACHE_DIR_ENV, default_cache_dir

    context = config.resolve_context(strict=True)
    if not context.kpdb:
        raise IdentityKeyError("database_unconfigured", "Configure an existing KeePass database first.")
    database = Path(context.kpdb).expanduser().resolve()
    if not database.is_file() or not os.access(database, os.R_OK):
        raise IdentityKeyError("database_unavailable", "Configured KeePass database is not a readable file.")
    cache_root = Path(os.environ.get(CACHE_DIR_ENV) or default_cache_dir()).resolve()
    if config.PINNED_CONTEXT_ENV not in os.environ:
        digest = identity_cache_digest(
            str(database), str(config.home_dir()), config.installation_id() or "",
        )
        cache_root = cache_root / "identity" / digest
    environment = {
        "KPDB": str(database),
        "VAULT_GROUP": context.group or "",
        "AGENT_VAULT": context.vault_name or "",
        config.PINNED_CONTEXT_ENV: json.dumps({
            "version": 1,
            "kpdb": str(database),
            "group": context.group or "",
            "vault_name": context.vault_name or "",
            "port": context.port,
        }, sort_keys=True, separators=(",", ":")),
        "AGENT_VAULT_CACHE": "1",
        CACHE_DIR_ENV: str(cache_root),
        config.HOME_ENV: str(config.home_dir().resolve()),
        config.INSTALLATION_ID_ENV: config.installation_id() or "",
        config.CONFIG_ENV: str(config._global_config_path().resolve()),
        config.RUN_DIR_ENV: str(config.run_dir().resolve()),
        "AGENT_VAULT_PORT": str(context.port),
        config.ENDPOINT_ENV: os.environ.get(config.ENDPOINT_ENV, ""),
        config.SOCKET_ENV: os.environ.get(config.SOCKET_ENV) or config.SOCKET_PATH,
        config.PIPE_ENV: os.environ.get(config.PIPE_ENV) or config.PIPE_PATH,
        config.PID_ENV: str(Path(os.environ.get(config.PID_ENV) or config.PID_FILE).resolve()),
        config.LOG_ENV: str(Path(os.environ.get(config.LOG_ENV) or config.LOG_FILE).resolve()),
        "AGENT_VAULT_HOST": os.environ.get("AGENT_VAULT_HOST", "127.0.0.1"),
        "COPILOT_EXTENSIONS_CONTEXT": os.environ.get("AGENT_VAULT_VALIDATED_CONTEXT", ""),
    }
    for name in (config.SYSTEMD_UNIT_ENV, config.TASK_NAME_ENV):
        environment[name] = os.environ.get(name, "")
    windows_run = os.environ.get("AGENT_VAULT_WINDOWS_RUN_DIR")
    environment["AGENT_VAULT_WINDOWS_RUN_DIR"] = (
        str(Path(windows_run).resolve()) if windows_run else ""
    )
    return {"version": 1, "env": environment}


def provision(service, kpdb: str, entry: str, initialize: bool, confirm: bool) -> dict:
    operation_deadline = time.monotonic() + (
        100.0 if getattr(service._request_ctx, "allow_prompt", False) else 60.0
    )
    if initialize != confirm:
        raise IdentityKeyError("confirmation_required", "First-owner initialization requires both flags.")
    if not entry or any(segment in ("", ".", "..", "[empty]") for segment in entry.split("/")):
        raise IdentityKeyError("invalid_entry", "Select an unambiguous slash-delimited entry path.")
    if not service._credential_lock.acquire(timeout=5.0):
        raise IdentityKeyError("service_busy", "Vault is busy; retry setup after the current operation.")
    try:
        if not service.ensure_unlocked(kpdb):
            raise IdentityKeyError("needs_unlock", "Vault locked; unlock it or explicitly use --prompt.")
        deadline = min(operation_deadline, time.monotonic() + 20.0)
        value = service.cli.read_identity_key(kpdb, entry, deadline)
        status = "reused"
        if value is None:
            if not initialize:
                raise IdentityKeyError(
                    "entry_missing", "Shared owner entry is absent; synchronize the authoritative vault."
                )
            candidate = base64.b64encode(os.urandom(32)).decode("ascii")
            service.cli.create_identity_key(kpdb, entry, candidate, deadline)
            # Always reread: failed add completion can mean another writer won.
            value = service.cli.read_identity_key(
                kpdb, entry, min(operation_deadline, time.monotonic() + 10.0)
            )
            if value is None:
                raise IdentityKeyError("creation_unconfirmed", "Creation unconfirmed; inspect and synchronize the vault before retrying.")
            status = "created" if value == candidate else "reused"
        validate_key(value)
        generation = time.time_ns()
        service.cache[(kpdb, entry, "password")] = value
        service._credential_generations[(kpdb, entry, "password")] = generation
        return {"ok": True, "status": status, "value": value, "generation": generation}
    finally:
        service._credential_lock.release()


def command(args) -> int:
    from . import cli
    from .cache import get_cache

    payload = {"version": 1}
    succeeded = False
    stage = "context"
    try:
        metadata = source_context()
        if args.identity_command == "context":
            print(json.dumps(metadata, sort_keys=True))
            return 0
        if args.initialize_owner != args.confirm_first_owner:
            raise IdentityKeyError("confirmation_required", "First-owner initialization requires both flags.")
        context = config.resolve_context(strict=True)
        entry = config.normalize_entry(args.entry, context.group)
        stage = "cache_read"
        cache = get_cache(metadata["env"]["AGENT_VAULT_CACHE_DIR"])
        if not cache.enabled:
            raise IdentityKeyError("cache_unavailable", "Enable a usable protected cache before setup (AGENT_VAULT_CACHE=1).")
        cache._read_store(readonly=True, strict=True)
        stage = "service"
        if getattr(args, "require_running", False):
            running = cli.send_command(
                {"action": "ping"}, timeout=5.0, strict_endpoint=True,
            )
            if not running or running.get("ok") is not True:
                raise IdentityKeyError(
                    "service_unavailable",
                    "Start the selected vault service independently, then retry setup; activation is disabled.",
                )
        elif not cli.ensure_service():
            raise IdentityKeyError("service_unavailable", "Could not start configured vault service; check its installation.")
        stage = "cache_update"
        with cache.replacement_lock():
            stage = "service"
            response = cli.send_command({
                "action": "identity-key-setup",
                "entry": entry,
                "kpdb": metadata["env"]["KPDB"],
                "group": "",
                "initialize_owner": args.initialize_owner,
                "confirm_first_owner": args.confirm_first_owner,
                "allow_prompt": args.prompt,
            }, timeout=120.0 if args.prompt else 90.0, strict_endpoint=True)
            if not response or not response.get("ok"):
                code = response.get("code", "service_unavailable") if response else "service_unavailable"
                if not isinstance(code, str) or code not in SERVICE_FAILURE_HINTS:
                    code = "service_unavailable"
                raise IdentityKeyError(code, SERVICE_FAILURE_HINTS[code])
            value = validate_key(response.get("value", ""))
            if response.get("status") not in ("created", "reused"):
                raise IdentityKeyError("invalid_response", "Vault returned an invalid provisioning status; retry with the owning runtime.")
            stage = "cache_update"
            if not cache.reconcile_authoritative(entry, "password", value, response.get("generation")):
                raise IdentityKeyError("cache_update_failed", "Owner entry preserved, but protected cache update failed; repair cache and retry.")
            if cache.get(entry, "password", readonly=True) != value:
                raise IdentityKeyError("cache_update_failed", "Protected cache verification failed; repair cache and retry.")
        payload.update(status=response["status"], cached=True)
        succeeded = True
    except IdentityKeyError as exc:
        payload.update(error=exc.code, message=str(exc))
    except (OSError, RuntimeError, ValueError):
        if stage in ("cache_read", "cache_update"):
            payload.update(
                error="cache_read_failed" if stage == "cache_read" else "cache_update_failed",
                message="Selected protected cache is unusable; repair it and retry without replacing the owner key.",
            )
        else:
            payload.update(
                error="context_invalid" if stage == "context" else "service_unavailable",
                message="Configured vault context or service is unusable; repair selection and retry.",
            )
    print(json.dumps(payload, sort_keys=True))
    return 0 if succeeded else 1
