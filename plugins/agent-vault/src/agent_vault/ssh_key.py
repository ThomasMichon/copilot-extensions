"""Create-only SSH key preparation through the selected Vault owner."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import time
from pathlib import Path

from .identity_key import SERVICE_FAILURE_HINTS, IdentityKeyError

FAILURE_HINTS = {
    **SERVICE_FAILURE_HINTS,
    "entry_missing": "Synchronize the existing SSH key entry; ordinary preparation never initializes it.",
    "malformed_key": "Existing SSH key record is invalid; inspect it without replacing or rotating it.",
    "confirmation_required": "Initial SSH key creation requires both explicit initialization flags.",
    "crypto_unavailable": "Install agent-vault[ssh] in the owning runtime and retry.",
}
MAX_RECORD_BYTES = 16384


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as exc:
        raise IdentityKeyError("crypto_unavailable", FAILURE_HINTS["crypto_unavailable"]) from exc
    return serialization, Ed25519PrivateKey, Ed25519PublicKey


def public_metadata(public_key: str) -> dict:
    serialization, _, public_type = _crypto()
    from cryptography.exceptions import UnsupportedAlgorithm

    try:
        if not isinstance(public_key, str) or len(public_key) > 256:
            raise ValueError
        public = serialization.load_ssh_public_key(public_key.encode("ascii"))
        if not isinstance(public, public_type):
            raise ValueError
        canonical = public.public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
        ).decode("ascii")
        if canonical != public_key:
            raise ValueError
        wire = base64.b64decode(public_key.split(" ")[1], validate=True)
    except (ValueError, TypeError, UnicodeError, binascii.Error, UnsupportedAlgorithm) as exc:
        raise IdentityKeyError("malformed_key", FAILURE_HINTS["malformed_key"]) from exc
    digest = base64.b64encode(hashlib.sha256(wire).digest()).decode("ascii").rstrip("=")
    return {"public_key": canonical, "fingerprint": "SHA256:" + digest}


def _generate_record() -> str:
    serialization, private_type, _ = _crypto()
    private = private_type.generate()
    record = {
        "schema": "agent-vault.ssh-key",
        "version": 1,
        "private_key": private.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode("ascii"),
        "public_key": private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
        ).decode("ascii"),
    }
    return base64.b64encode(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).decode("ascii")


def decode_record(value: str) -> dict:
    serialization, private_type, _ = _crypto()
    from cryptography.exceptions import UnsupportedAlgorithm

    try:
        if not isinstance(value, str) or not value or len(value) > MAX_RECORD_BYTES:
            raise ValueError
        decoded = base64.b64decode(value, validate=True)
        record = json.loads(decoded)
        if (
            not isinstance(record, dict)
            or set(record) != {"schema", "version", "private_key", "public_key"}
            or record["schema"] != "agent-vault.ssh-key"
            or type(record["version"]) is not int or record["version"] != 1
            or not isinstance(record["private_key"], str)
            or not isinstance(record["public_key"], str)
        ):
            raise ValueError
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii")
        if canonical != decoded or base64.b64encode(decoded).decode("ascii") != value:
            raise ValueError
        private = serialization.load_ssh_private_key(
            record["private_key"].encode("ascii"), password=None,
        )
        if not isinstance(private, private_type):
            raise ValueError
        derived = private.public_key().public_bytes(
            serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH,
        ).decode("ascii")
        if derived != record["public_key"]:
            raise ValueError
        public_metadata(derived)
        return record
    except (
        ValueError, TypeError, KeyError, UnicodeError, binascii.Error,
        RecursionError, UnsupportedAlgorithm,
    ) as exc:
        raise IdentityKeyError("malformed_key", FAILURE_HINTS["malformed_key"]) from exc


def _read_record(value: str) -> dict:
    return public_metadata(decode_record(value)["public_key"])


def read_for_install(service, kpdb: str, entry: str) -> dict:
    """Read an existing whole record for the local protected-file consumer."""
    deadline = time.monotonic() + (
        100.0 if getattr(service._request_ctx, "allow_prompt", False) else 60.0
    )
    validate_request(entry, False, False)
    _crypto()
    if not service._credential_lock.acquire(timeout=5.0):
        raise IdentityKeyError("service_busy", FAILURE_HINTS["service_busy"])
    try:
        if not service.ensure_unlocked(kpdb):
            raise IdentityKeyError("needs_unlock", FAILURE_HINTS["needs_unlock"])
        if time.monotonic() >= deadline:
            raise IdentityKeyError("operation_expired", FAILURE_HINTS["operation_expired"])
        value = service.cli.read_identity_key(kpdb, entry, min(deadline, time.monotonic() + 20.0))
        if value is None:
            raise IdentityKeyError("entry_missing", FAILURE_HINTS["entry_missing"])
        decode_record(value)
        return {"ok": True, "value": value}
    finally:
        service._credential_lock.release()


def validate_request(entry: str, initialize: bool, confirm: bool) -> None:
    if type(initialize) is not bool or type(confirm) is not bool or initialize != confirm:
        raise IdentityKeyError("confirmation_required", FAILURE_HINTS["confirmation_required"])
    if (
        not isinstance(entry, str) or not entry or len(entry) > 256
        or entry != entry.strip() or any(ord(char) < 32 or ord(char) == 127 for char in entry)
        or any(part in ("", ".", "..", "[empty]") or part != part.strip() for part in entry.split("/"))
        or "\\" in entry
    ):
        raise IdentityKeyError("invalid_entry", FAILURE_HINTS["invalid_entry"])


def prepare(service, kpdb: str, entry: str, initialize: bool, confirm: bool) -> dict:
    """Commit a whole pair with one protected-field add; return no private bytes."""
    operation_deadline = time.monotonic() + (
        100.0 if getattr(service._request_ctx, "allow_prompt", False) else 60.0
    )
    validate_request(entry, initialize, confirm)
    _crypto()
    if not service._credential_lock.acquire(timeout=5.0):
        raise IdentityKeyError("service_busy", FAILURE_HINTS["service_busy"])
    try:
        if not service.ensure_unlocked(kpdb):
            raise IdentityKeyError("needs_unlock", FAILURE_HINTS["needs_unlock"])
        if time.monotonic() >= operation_deadline:
            raise IdentityKeyError("operation_expired", FAILURE_HINTS["operation_expired"])
        deadline = min(operation_deadline, time.monotonic() + 20.0)
        value = service.cli.read_identity_key(kpdb, entry, deadline)
        status = "reused"
        if value is None:
            if not initialize:
                raise IdentityKeyError("entry_missing", FAILURE_HINTS["entry_missing"])
            candidate = _generate_record()
            # The existing backend serializes create-only adds and never retries an
            # uncertain write. Re-read its winner instead of replacing attachments.
            service.cli.create_identity_key(kpdb, entry, candidate, deadline)
            value = service.cli.read_identity_key(
                kpdb, entry, min(operation_deadline, time.monotonic() + 10.0),
            )
            if value is None:
                raise IdentityKeyError("creation_unconfirmed", FAILURE_HINTS["creation_unconfirmed"])
            status = "created" if value == candidate else "reused"
        return {"ok": True, "status": status, **_read_record(value)}
    finally:
        service._credential_lock.release()


def handle_prepare_request(service, kpdb: str, group: str | None, request: dict) -> dict:
    from .config import normalize_entry

    try:
        entry = request.get("entry", "")
        if not isinstance(entry, str) or (group is not None and not isinstance(group, str)):
            raise IdentityKeyError("invalid_entry", "Select a slash-delimited SSH key entry.")
        if request.get("action") == "ssh-key-read":
            validate_request(
                entry, request.get("initialize", False), request.get("confirm_initialization", False),
            )
            if request.get("initialize", False):
                raise IdentityKeyError("confirmation_required", "SSH installation never initializes a key.")
            return read_for_install(service, kpdb, normalize_entry(entry, group))
        validate_request(
            entry, request.get("initialize", False), request.get("confirm_initialization", False),
        )
        return prepare(
            service, kpdb, normalize_entry(entry, group),
            request.get("initialize", False), request.get("confirm_initialization", False),
        )
    except IdentityKeyError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}


def command(args) -> int:
    from . import cli, config

    try:
        validate_request(args.entry, args.initialize, args.confirm_initialization)
        _crypto()
        context = config.resolve_context(strict=True)
        if not context.kpdb:
            raise IdentityKeyError("database_unconfigured", "Configure an existing KeePass database first.")
        database = Path(context.kpdb).expanduser().resolve(strict=True)
        if not database.is_file():
            raise IdentityKeyError("database_unavailable", "Configured KeePass database is not a file.")
        entry = config.normalize_entry(args.entry, context.group)
        validate_request(entry, args.initialize, args.confirm_initialization)
        if args.require_running:
            running = cli.send_command({"action": "ping"}, timeout=5.0, strict_endpoint=True)
            if not isinstance(running, dict) or running.get("ok") is not True:
                raise IdentityKeyError("service_unavailable", FAILURE_HINTS["service_unavailable"])
        elif not cli.ensure_service():
            raise IdentityKeyError("service_unavailable", FAILURE_HINTS["service_unavailable"])
        response = cli.send_command({
            "action": "ssh-key-prepare", "entry": entry, "kpdb": str(database), "group": "",
            "initialize": args.initialize, "confirm_initialization": args.confirm_initialization,
            "allow_prompt": args.prompt,
        }, timeout=120.0 if args.prompt else 90.0, strict_endpoint=True)
        if not isinstance(response, dict) or response.get("ok") is not True:
            code = response.get("code") if isinstance(response, dict) else None
            if not isinstance(code, str) or code not in FAILURE_HINTS:
                code = "service_unavailable"
            raise IdentityKeyError(code, FAILURE_HINTS[code])
        metadata = public_metadata(response.get("public_key"))
        if (
            response.get("status") not in ("created", "reused")
            or response.get("fingerprint") != metadata["fingerprint"]
        ):
            raise IdentityKeyError("invalid_response", "Vault returned invalid public key metadata.")
        print(json.dumps({"version": 1, "status": response["status"], **metadata}, sort_keys=True))
        return 0
    except IdentityKeyError as exc:
        payload = {"version": 1, "error": exc.code, "message": str(exc)}
    except (OSError, RuntimeError, ValueError):
        payload = {
            "version": 1, "error": "context_invalid",
            "message": "Selected vault context is unusable; repair it without replacing the SSH key.",
        }
    print(json.dumps(payload, sort_keys=True))
    return 1
