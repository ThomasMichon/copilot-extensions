"""Create-only SSH key preparation through the selected Vault owner."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import struct
import time
from pathlib import Path

from .identity_key import SERVICE_FAILURE_HINTS, IdentityKeyError

FAILURE_HINTS = {
    **SERVICE_FAILURE_HINTS,
    "entry_missing": "Synchronize the existing SSH key entry; ordinary preparation never initializes it.",
    "malformed_key": "Existing SSH key record is invalid; inspect it without replacing or rotating it.",
    "confirmation_required": "Initial SSH key creation requires both explicit initialization flags.",
    "crypto_unavailable": "Select the owning Vault runtime's SSH installation profile and retry.",
}
MAX_RECORD_BYTES = 16384


def _crypto():
    try:
        from Cryptodome.PublicKey import ECC
    except ImportError as exc:
        raise IdentityKeyError("crypto_unavailable", FAILURE_HINTS["crypto_unavailable"]) from exc
    return ECC


def _public_bytes(key) -> str:
    if key.curve != "Ed25519":
        raise ValueError("not an Ed25519 key")
    return key.public_key().export_key(format="OpenSSH").strip()


def _openssh_private(key, public: str) -> str:
    """Serialize the existing unencrypted openssh-key-v1 record, not PKCS8."""
    def field(value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + value

    wire = base64.b64decode(public.split(" ")[1], validate=True)
    raw_public = key.public_key().export_key(format="raw")
    check = secrets.token_bytes(4)
    private = (
        check + check + field(b"ssh-ed25519") + field(raw_public)
        + field(key.seed + raw_public) + field(b"")
    )
    padding = 8 - len(private) % 8
    private += bytes(range(1, padding + 1))
    blob = (
        b"openssh-key-v1\0" + field(b"none") + field(b"none") + field(b"")
        + struct.pack(">I", 1) + field(wire) + field(private)
    )
    encoded = base64.b64encode(blob).decode("ascii")
    lines = "\n".join(encoded[index:index + 70] for index in range(0, len(encoded), 70))
    return "-----BEGIN OPENSSH PRIVATE KEY-----\n" + lines + "\n-----END OPENSSH PRIVATE KEY-----\n"


def _validate_private_encoding(value: str, public: str) -> None:
    # Validate redundant public fields and reject encrypted/KDF input before
    # invoking the library importer. It must not ignore damaged record framing.
    lines = value.splitlines()
    if (
        len(lines) < 3 or lines[0] != "-----BEGIN OPENSSH PRIVATE KEY-----"
        or lines[-1] != "-----END OPENSSH PRIVATE KEY-----"
    ):
        raise ValueError
    blob = base64.b64decode("".join(lines[1:-1]), validate=True)
    if not blob.startswith(b"openssh-key-v1\0"):
        raise ValueError

    def field(data: bytes) -> tuple[bytes, bytes]:
        if len(data) < 4:
            raise ValueError
        size = struct.unpack(">I", data[:4])[0]
        if size > len(data) - 4:
            raise ValueError
        return data[4:4 + size], data[4 + size:]

    cipher, rest = field(blob[15:])
    kdf, rest = field(rest)
    options, rest = field(rest)
    if cipher != b"none" or kdf != b"none" or options or rest[:4] != b"\0\0\0\1":
        raise ValueError
    wire, rest = field(rest[4:])
    private, rest = field(rest)
    if rest or wire != base64.b64decode(public.split(" ")[1], validate=True):
        raise ValueError
    if len(private) < 8 or len(private) % 8 or private[:4] != private[4:8]:
        raise ValueError
    key_type, rest = field(private[8:])
    public_raw, rest = field(rest)
    pair, rest = field(rest)
    _, padding = field(rest)
    if (
        key_type != b"ssh-ed25519" or len(public_raw) != 32
        or wire != struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + public_raw
        or len(pair) != 64 or pair[32:] != public_raw
        or len(padding) > 8 or padding != bytes(range(1, len(padding) + 1))
    ):
        raise ValueError


def public_metadata(public_key: str) -> dict:
    backend = _crypto()

    try:
        if not isinstance(public_key, str) or len(public_key) > 256:
            raise ValueError
        if not public_key.startswith("ssh-ed25519 "):
            raise ValueError
        public = backend.import_key(public_key.encode("ascii"))
        canonical = _public_bytes(public)
        if canonical != public_key:
            raise ValueError
        wire = base64.b64decode(public_key.split(" ")[1], validate=True)
    except (ValueError, TypeError, IndexError, UnicodeError, binascii.Error) as exc:
        raise IdentityKeyError("malformed_key", FAILURE_HINTS["malformed_key"]) from exc
    digest = base64.b64encode(hashlib.sha256(wire).digest()).decode("ascii").rstrip("=")
    return {"public_key": canonical, "fingerprint": "SHA256:" + digest}


def _generate_record() -> str:
    private = _crypto().generate(curve="Ed25519")
    public = _public_bytes(private)
    record = {
        "schema": "agent-vault.ssh-key",
        "version": 1,
        "private_key": _openssh_private(private, public),
        "public_key": public,
    }
    return base64.b64encode(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).decode("ascii")


def decode_record(value: str) -> dict:
    backend = _crypto()

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
        public_metadata(record["public_key"])
        _validate_private_encoding(record["private_key"], record["public_key"])
        private = backend.import_key(record["private_key"].encode("ascii"))
        if not private.has_private():
            raise ValueError
        derived = _public_bytes(private)
        if derived != record["public_key"]:
            raise ValueError
        return record
    except (
        ValueError, TypeError, KeyError, UnicodeError, binascii.Error,
        RecursionError, IndexError,
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
        try:
            database = Path(context.kpdb).expanduser().resolve(strict=True)
            if not database.is_file():
                raise IdentityKeyError("database_unavailable", "Configured KeePass database is not a file.")
        except OSError:
            raise IdentityKeyError(
                "database_unavailable",
                "Configured KeePass database is unavailable; restore access or synchronization without replacing it.",
            ) from None
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
