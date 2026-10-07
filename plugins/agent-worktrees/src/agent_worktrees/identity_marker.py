"""Encrypted identity marker for PR source attribution (effort
``pr-attribution-codenames``, encrypted-identity-marker slice, Piece 2).

A THIRD, independent layer alongside the existing ``codename=``/``root=``
marker fields (see :mod:`agent_worktrees.providers.attribution` and
:mod:`agent_worktrees.root_chain`) -- never a replacement for either. Where
``codename=`` only decodes via local tracking-store access or an automated
cross-machine SSH scan, this field is a small AES-256-GCM-encrypted blob
carrying the **full raw identity** (worktree id, machine, session, head SHA,
project, timestamp -- mirroring the raw ``source_attribution: true`` marker's
own fields) that only the holder of a shared symmetric key can decrypt --
anyone else sees only opaque ciphertext, so it is exactly as public-safe as
``codename=`` itself and may be emitted unconditionally whenever a key
resolves, on any repo, in any ``pr.source_attribution`` mode.

Key custody (operator-confirmed design, effort README): a single raw
base64-encoded 32-byte key file, deliberately **not** machine-bound (no
DPAPI/KEK wrapping, unlike :mod:`agent_vault.kek`) so the SAME key decrypts
markers from every one of the operator's machines. The default location is
OneDrive-rooted specifically so the file travels with the operator rather
than staying pinned to one machine -- the whole point of this marker versus
the existing SSH-scan-based codename resolution, which must ask every known
machine in turn. This is intentionally a lighter-weight mechanism than
agent-vault (no KeePass database, no daemon, no MFA) -- a low-stakes
reverse-lookup convenience, not a secrets vault.

The key is never auto-generated on a publish path: :func:`load_identity_key`
only reads an already-provisioned file and returns ``None`` (the layer is
simply omitted) when none resolves. :func:`generate_identity_key` is the
explicit, operator-invoked provisioning step.

Kept as its own module (mirroring :mod:`agent_worktrees.root_chain`'s own
rationale) rather than growing ``pr_ops.py``/``tracking.py``/``config.py``,
all three already at their ``tools/check-module-size.py`` shrink-only
ceiling.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import tracking

#: Raw symmetric key size (AES-256).
KEY_BYTES = 32
#: GCM standard nonce size.
NONCE_BYTES = 12
#: Sealed-blob magic + version, distinguishing this format from any other
#: base64 payload that might accidentally be fed to :func:`decrypt_identity_payload`.
MARKER_MAGIC = b"AWI1"
#: Explicit override: an absolute path to the key file. Takes precedence over
#: the OneDrive-rooted default below.
KEY_ENV_VAR = "AGENT_WORKTREES_IDENTITY_KEY"
KEY_FILENAME = "identity.key"
#: Subfolder under the resolved OneDrive root -- mirrors
#: ``agent_logger.sync.targets.filesystem.OneDriveTarget``'s own
#: ``Apps/<plugin>/...`` convention for a shared, fleet-wide app folder.
DEFAULT_KEY_SUBDIR = "Apps/agent-worktrees"
#: Current payload schema version.
PAYLOAD_VERSION = 1


class IdentityMarkerError(RuntimeError):
    """Raised for identity-marker key/encrypt/decrypt failures.

    Never raised by the PR-publish hot path
    (:func:`encrypt_identity_payload`/:func:`identity_marker_field`), which
    degrades to ``None`` instead -- a missing key or missing ``cryptography``
    dependency must omit this optional layer, never block a PR publish.
    Raised by the operator-invoked reverse-lookup path
    (:func:`decrypt_identity_payload`, :func:`generate_identity_key`).
    """


def resolve_onedrive_root() -> Path | None:
    """Resolve the OneDrive root for the current OS, or ``None``.

    Honors the Windows ``OneDrive*`` environment variables first, then falls
    back to ``~/OneDrive`` if it exists. Duplicated from
    ``agent_logger.sync.targets.filesystem.resolve_onedrive_root`` (same
    logic, independently owned) -- this plugin does not depend on
    agent-logger.
    """
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(var)
        if value and Path(value).is_dir():
            return Path(value)
    fallback = Path.home() / "OneDrive"
    if fallback.is_dir():
        return fallback
    return None


def default_identity_key_path() -> Path | None:
    """Default location for the shared identity key: a OneDrive-synced
    folder, so the SAME key file is available to decrypt markers on ANY of
    the operator's machines. Returns ``None`` when no OneDrive root resolves
    on this machine (the layer then stays inert unless
    :data:`KEY_ENV_VAR` names an explicit path).
    """
    root = resolve_onedrive_root()
    if root is None:
        return None
    return root / DEFAULT_KEY_SUBDIR / KEY_FILENAME


def identity_key_path() -> Path | None:
    """Resolve the identity-key file path: :data:`KEY_ENV_VAR` (explicit
    override) first, else the OneDrive-rooted default. Returns ``None`` when
    neither resolves.
    """
    override = os.environ.get(KEY_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return default_identity_key_path()


def load_identity_key() -> bytes | None:
    """Load the raw 32-byte symmetric identity key, or ``None`` if no key
    file is configured, resolvable, or readable as a valid key.

    Never auto-creates one -- unlike a machine-bound KEK, this key must be
    the IDENTICAL file on every machine (see :func:`generate_identity_key`),
    so silently generating a fresh one here would fragment it across
    machines instead of sharing it. Any failure (missing file, bad base64,
    wrong length) returns ``None`` rather than raising -- this is the
    hot-path reader used by every PR publish.
    """
    path = identity_key_path()
    if path is None or not path.is_file():
        return None
    try:
        raw = base64.b64decode(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None
    if len(raw) != KEY_BYTES:
        return None
    return raw


def generate_identity_key(path: Path | None = None, *, force: bool = False) -> Path:
    """Create a new base64-encoded random 32-byte identity key file at
    *path* (default: :func:`identity_key_path`'s resolution).

    Explicit, operator-invoked only (a CLI verb or this module's own
    ``generate`` entry point below) -- never called automatically from a PR-
    publish path. Refuses to overwrite an existing key unless *force* is set,
    since overwriting silently would strand every marker already published
    under the old key (they would stop decrypting, with no warning).
    """
    target = path or identity_key_path()
    if target is None:
        raise IdentityMarkerError(
            "no identity key path resolvable -- set "
            f"{KEY_ENV_VAR} or ensure a OneDrive root is available"
        )
    if target.exists() and not force:
        raise IdentityMarkerError(
            f"identity key already exists at {target} (use --force to overwrite)"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = secrets.token_bytes(KEY_BYTES)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(base64.b64encode(raw).decode("ascii"), encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, target)
    return target


def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise IdentityMarkerError(
            "the 'cryptography' package is required for the encrypted "
            "identity marker -- install it (e.g. `uv pip install "
            "cryptography`)"
        ) from exc
    return AESGCM(key)


def build_identity_payload(
    *,
    worktree_id: str,
    machine: str = "",
    session: str = "",
    head: str = "",
    project: str = "",
) -> dict:
    """Build the full-identity payload dict (operator-confirmed Piece 2
    scope: mirrors the raw ``source_attribution: true`` marker's own fields,
    destined for encryption instead of plaintext exposure). Only
    ``worktree_id`` and ``ts`` (a UTC ISO-8601 timestamp, second precision)
    are always present; the rest are omitted when the caller has no value,
    matching :func:`agent_worktrees.providers.attribution.build_marker`'s own
    conditional-field convention.
    """
    payload: dict = {
        "v": PAYLOAD_VERSION,
        "worktree_id": worktree_id,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if machine:
        payload["machine"] = machine
    if session:
        payload["session"] = session
    if head:
        payload["head"] = head
    if project:
        payload["project"] = project
    return payload


def encrypt_identity_payload(payload: dict) -> str | None:
    """Encrypt *payload* (a JSON-serializable dict) under the resolved
    identity key; returns base64 of ``MAGIC || nonce || ciphertext+tag``.

    Returns ``None`` (never raises) when no key is configured or
    ``cryptography`` is unavailable -- this sits on the PR-publish hot path
    and must degrade to "omit this optional layer", exactly like a
    codename-resolution failure already does for the ``root=`` field.
    """
    key = load_identity_key()
    if key is None:
        return None
    try:
        plaintext = json.dumps(
            payload, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        nonce = secrets.token_bytes(NONCE_BYTES)
        ciphertext = _aesgcm(key).encrypt(nonce, plaintext, None)
        return base64.b64encode(MARKER_MAGIC + nonce + ciphertext).decode("ascii")
    except IdentityMarkerError:
        return None
    except Exception:
        return None


def decrypt_identity_payload(token_b64: str, key: bytes | None = None) -> dict:
    """Reverse :func:`encrypt_identity_payload`.

    This is the operator-invoked reverse-lookup path, so it RAISES
    :class:`IdentityMarkerError` (never silently degrades) on a missing key,
    malformed token, or a failed authentication tag (wrong key / tampered
    data).
    """
    resolved_key = key if key is not None else load_identity_key()
    if resolved_key is None:
        raise IdentityMarkerError(
            "no identity key configured -- set "
            f"{KEY_ENV_VAR} or place one at {identity_key_path()}"
        )
    try:
        blob = base64.b64decode(token_b64)
    except Exception as exc:
        raise IdentityMarkerError(f"invalid base64: {exc}") from exc
    if not blob.startswith(MARKER_MAGIC):
        raise IdentityMarkerError("not an identity-marker token (bad magic)")
    body = blob[len(MARKER_MAGIC):]
    if len(body) < NONCE_BYTES + 16:
        raise IdentityMarkerError("identity-marker token too short")
    nonce, ciphertext = body[:NONCE_BYTES], body[NONCE_BYTES:]
    try:
        plaintext = _aesgcm(resolved_key).decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode("utf-8"))
    except IdentityMarkerError:
        raise
    except Exception as exc:
        raise IdentityMarkerError(
            f"decrypt failed (wrong key or tampered data): {exc}"
        ) from exc


def identity_marker_field(
    *,
    worktree_id: str,
    machine: str = "",
    session: str = "",
    head: str = "",
    project: str = "",
) -> str | None:
    """One-call convenience for a PR-marker publish site: build the
    full-identity payload and encrypt it, returning the ciphertext ready to
    fold into the marker's ``enc=`` field, or ``None`` when no identity key
    is configured (the layer is simply omitted -- a codename-mode marker is
    already correct without it, matching the ``root=`` field's own
    best-effort/optional shape).
    """
    payload = build_identity_payload(
        worktree_id=worktree_id,
        machine=machine,
        session=session,
        head=head,
        project=project,
    )
    return encrypt_identity_payload(payload)


def identity_marker_field_for_record(
    record: tracking.WorktreeRecord, *, project: str = "", head: str = "",
) -> str | None:
    """``identity_marker_field`` convenience wrapper for a PR-marker publish
    site already holding a :class:`~agent_worktrees.tracking.WorktreeRecord`
    -- resolves ``worktree_id``/``machine`` from *record* and the live
    (not-yet-ended, else most-recent) session id the same way
    ``pr_ops.refresh_source_attribution`` already does locally. Never
    raises -- any failure degrades to omitting the field.
    """
    try:
        session = ""
        if record.sessions:
            live = [item for item in record.sessions if not item.ended_at]
            session = (live[-1] if live else record.sessions[-1]).session_id
        return identity_marker_field(
            worktree_id=record.worktree_id,
            machine=record.machine,
            session=session,
            head=head,
            project=project,
        )
    except Exception:
        return None


def _cli(argv: list[str] | None = None) -> int:
    """Minimal standalone CLI (``python -m agent_worktrees.identity_marker
    <verb>``) for key provisioning and reverse lookup -- deliberately NOT
    wired into ``__main__.py`` (at its own shrink-only line-count ceiling)
    for this initial slice. A full ``agent-worktrees identity`` subcommand
    can follow in a smaller, separate change if this proves useful enough to
    warrant the ceiling widen.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="agent_worktrees.identity_marker")
    sub = parser.add_subparsers(dest="verb", required=True)

    gen = sub.add_parser("generate", help="create a new identity key file")
    gen.add_argument("--path", default=None, help="explicit key file path")
    gen.add_argument("--force", action="store_true", help="overwrite an existing key")

    dec = sub.add_parser("decode", help="decrypt an enc=<token> marker value")
    dec.add_argument("token", help="the base64 token (the enc=<...> value)")

    args = parser.parse_args(argv)
    if args.verb == "generate":
        path = Path(args.path).expanduser() if args.path else None
        try:
            created = generate_identity_key(path, force=args.force)
        except IdentityMarkerError as exc:
            print(f"error: {exc}")
            return 1
        print(f"identity key written to {created}")
        return 0
    if args.verb == "decode":
        try:
            payload = decrypt_identity_payload(args.token)
        except IdentityMarkerError as exc:
            print(f"error: {exc}")
            return 1
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    return 1  # pragma: no cover - argparse enforces a known verb


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess, not coverage
    import sys

    raise SystemExit(_cli(sys.argv[1:]))
