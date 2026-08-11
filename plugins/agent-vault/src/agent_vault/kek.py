"""Envelope key-encryption-key (KEK) + seal/unseal for agent-vault.

A **KEK** is a per-name 32-byte key used to seal (AES-256-GCM encrypt) and unseal
small secrets -- e.g. a consumer's on-disk token cache -- so the consumer never has
to hardcode an encryption key. The KEK is generated once per name and persisted
**wrapped at rest**:

- **Windows:** wrapped with **DPAPI** (``CryptProtectData``, per-OS-user scope via
  ``ctypes`` -- no third-party dependency). The wrapped blob is only unwrappable by
  the same Windows user account, binding the KEK to that user.
- **POSIX:** stored as a ``0600`` file (documented lower assurance -- the same
  "hygiene, not high security" posture as the persistent cache's Fernet key;
  physical-access control + full-disk encryption are the real barriers).

seal/unseal run **inside the daemon**, so the raw KEK never crosses the wire -- only
ciphertext (seal) or plaintext (unseal) do. Because the KEK is DPAPI/file-backed and
independent of the KeePass master password, seal/unseal work on a **locked** vault
(like the persistent cache serving reads while locked); no unlock is required.

This module also exposes ``wrap_bytes``/``unwrap_bytes`` -- the bare DPAPI (Windows)
/ passthrough (POSIX) primitive -- so other at-rest key material (e.g. the persistent
cache's Fernet key) can be bound to the OS user with the same mechanism.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path

from .config import IS_WINDOWS, default_config_path

KEK_BYTES = 32          # AES-256
NONCE_BYTES = 12        # GCM standard nonce
SEAL_MAGIC = b"AVK1"    # sealed-blob magic + version
WRAP_MAGIC = b"AVW1"    # wrapped-key-material magic + version


class KekError(RuntimeError):
    """Raised for KEK / seal / unseal failures (missing crypto, bad ciphertext)."""


# ---------------------------------------------------------------------------
# DPAPI (Windows) wrap / unwrap primitive -- ctypes, no third-party dependency
# ---------------------------------------------------------------------------

def _dpapi(protect: bool, data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    # Keep the input buffer alive in THIS scope for the whole API call -- a
    # DATA_BLOB only borrows the pointer, so the backing buffer must outlive the
    # CryptProtect/Unprotect call (else ctypes may free it underneath us).
    in_buf = ctypes.create_string_buffer(data, len(data))
    in_blob = DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    CRYPTPROTECT_UI_FORBIDDEN = 0x1  # never pop UI
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = fn(
        ctypes.byref(in_blob), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob),
    )
    if not ok:
        err = ctypes.GetLastError()
        raise KekError(f"DPAPI {'protect' if protect else 'unprotect'} failed (error {err})")
    try:
        return ctypes.string_at(out_blob.pbData, int(out_blob.cbData))
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)
        del in_buf  # explicit: keep the buffer referenced until here


def wrap_bytes(data: bytes) -> bytes:
    """Wrap key material for at-rest storage (DPAPI per-user on Windows; passthrough
    on POSIX). Returns a magic-tagged, self-describing blob understood by
    ``unwrap_bytes``.
    """
    if IS_WINDOWS:
        return WRAP_MAGIC + b"D" + _dpapi(True, data)
    return WRAP_MAGIC + b"R" + data


def unwrap_bytes(blob: bytes) -> bytes:
    """Reverse :func:`wrap_bytes`. Raises :class:`KekError` if the DPAPI blob was
    produced by a different OS user (or is corrupt).
    """
    if not blob.startswith(WRAP_MAGIC):
        raise KekError("not a wrapped-key blob")
    scheme = blob[len(WRAP_MAGIC):len(WRAP_MAGIC) + 1]
    body = blob[len(WRAP_MAGIC) + 1:]
    if scheme == b"D":
        return _dpapi(False, body)
    if scheme == b"R":
        return body
    raise KekError(f"unknown wrap scheme {scheme!r}")


def is_wrapped(blob: bytes) -> bool:
    """Whether ``blob`` carries the wrapped-key-material magic."""
    return blob.startswith(WRAP_MAGIC)


# ---------------------------------------------------------------------------
# KEK store
# ---------------------------------------------------------------------------

def kek_dir() -> Path:
    """Directory holding per-name wrapped KEK files (beside the vault config)."""
    override = os.environ.get("AGENT_VAULT_KEK_DIR")
    return Path(override) if override else (default_config_path().parent / "keks")


def _safe_name(name: str) -> str:
    if not name or any(c in name for c in '/\\:*?"<>|') or name in (".", ".."):
        raise KekError(f"invalid KEK name: {name!r}")
    return name


def _kek_path(name: str) -> Path:
    return kek_dir() / (_safe_name(name) + ".kek")


def load_or_create_kek(name: str) -> bytes:
    """Return the 32-byte KEK for ``name``, creating + persisting it on first use.

    The on-disk file is JSON ``{"v":1,"scheme":"dpapi"|"raw","kek":"<b64>"}`` where
    the b64 body is DPAPI-wrapped (Windows) or raw (POSIX). Created ``0600``.
    """
    path = _kek_path(name)
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            raw = unwrap_bytes(base64.b64decode(doc["kek"]))
            if len(raw) == KEK_BYTES:
                return raw
            raise KekError("stored KEK has wrong length")
        except KekError:
            raise
        except Exception as exc:
            raise KekError(f"could not read KEK {name!r}: {exc}") from exc

    raw = secrets.token_bytes(KEK_BYTES)
    doc = {
        "v": 1,
        "scheme": "dpapi" if IS_WINDOWS else "raw",
        "kek": base64.b64encode(wrap_bytes(raw)).decode(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".kek.tmp")
    tmp.write_text(json.dumps(doc), encoding="utf-8")
    if not IS_WINDOWS:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return raw


def kek_exists(name: str) -> bool:
    return _kek_path(name).exists()


def list_keks() -> list[str]:
    d = kek_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.kek"))


def delete_kek(name: str) -> bool:
    path = _kek_path(name)
    if path.exists():
        path.unlink()
        return True
    return False


# ---------------------------------------------------------------------------
# seal / unseal (AES-256-GCM)
# ---------------------------------------------------------------------------

def _aesgcm(kek: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise KekError(
            "the 'cryptography' package is required for seal/unseal -- install it "
            "(e.g. `uv pip install cryptography`) or the agent-vault 'kek' extra"
        ) from exc
    return AESGCM(kek)


def seal(name: str, plaintext: bytes) -> str:
    """Seal ``plaintext`` under KEK ``name``; return base64 of
    ``MAGIC || nonce || ciphertext+tag``. The KEK name is bound as GCM AAD.
    """
    kek = load_or_create_kek(name)
    nonce = secrets.token_bytes(NONCE_BYTES)
    ct = _aesgcm(kek).encrypt(nonce, plaintext, name.encode("utf-8"))
    return base64.b64encode(SEAL_MAGIC + nonce + ct).decode()


def unseal(name: str, token_b64: str) -> bytes:
    """Reverse :func:`seal`. Raises :class:`KekError` on a missing KEK, a malformed
    blob, or a failed authentication tag (tamper / wrong KEK).
    """
    if not kek_exists(name):
        raise KekError(f"no KEK named {name!r}")
    try:
        blob = base64.b64decode(token_b64)
    except Exception as exc:
        raise KekError(f"invalid base64: {exc}") from exc
    if not blob.startswith(SEAL_MAGIC):
        raise KekError("not a sealed blob (bad magic)")
    body = blob[len(SEAL_MAGIC):]
    if len(body) < NONCE_BYTES + 16:
        raise KekError("sealed blob too short")
    nonce, ct = body[:NONCE_BYTES], body[NONCE_BYTES:]
    kek = load_or_create_kek(name)
    try:
        return _aesgcm(kek).decrypt(nonce, ct, name.encode("utf-8"))
    except Exception as exc:
        raise KekError(f"unseal failed (bad key or tampered data): {exc}") from exc
