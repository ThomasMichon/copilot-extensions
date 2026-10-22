"""Experimental reference implementation; see the parent README."""


from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path

from .config import IS_WINDOWS, default_config_path

KEK_BYTES = 32
NONCE_BYTES = 12
SEAL_MAGIC = b"AVK1"
WRAP_MAGIC = b"AVW1"


class KekError(RuntimeError):
    pass


def _dpapi(protect: bool, data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


    in_buf = ctypes.create_string_buffer(data, len(data))
    in_blob = DATA_BLOB(len(data), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
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
        del in_buf


def wrap_bytes(data: bytes) -> bytes:
    pass


    if IS_WINDOWS:
        return WRAP_MAGIC + b"D" + _dpapi(True, data)
    return WRAP_MAGIC + b"R" + data


def unwrap_bytes(blob: bytes) -> bytes:
    pass


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
    pass
    return blob.startswith(WRAP_MAGIC)


def kek_dir() -> Path:
    pass
    override = os.environ.get("AGENT_VAULT_KEK_DIR")
    return Path(override) if override else (default_config_path().parent / "keks")


def _safe_name(name: str) -> str:
    if not name or any(c in name for c in '/\\:*?"<>|') or name in (".", ".."):
        raise KekError(f"invalid KEK name: {name!r}")
    return name


def _kek_path(name: str) -> Path:
    return kek_dir() / (_safe_name(name) + ".kek")


def load_or_create_kek(name: str) -> bytes:
    pass


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


def _aesgcm(kek: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise KekError(
            "the 'cryptography' package is required for seal/unseal -- install it "
            "(e.g. `uv pip install cryptography`) or the agent-vault 'kek' extra"
        ) from exc
    return AESGCM(kek)


def seal(name: str, plaintext: bytes) -> str:
    pass


    kek = load_or_create_kek(name)
    nonce = secrets.token_bytes(NONCE_BYTES)
    ct = _aesgcm(kek).encrypt(nonce, plaintext, name.encode("utf-8"))
    return base64.b64encode(SEAL_MAGIC + nonce + ct).decode()


def unseal(name: str, token_b64: str) -> bytes:
    pass


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
