"""Native Windows protection for a local credential cache, including ARM64."""

from __future__ import annotations

from . import kek

MAGIC = b"AVC1D"


class CacheProtectionError(RuntimeError):
    """A protected cache cannot safely be read or replaced."""


class DpapiCacheCipher:
    def encrypt(self, plaintext: bytes) -> bytes:
        try:
            return MAGIC + kek._dpapi(True, plaintext)
        except kek.KekError as exc:
            raise CacheProtectionError("Windows cache protection failed") from exc

    def decrypt(self, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(MAGIC):
            raise CacheProtectionError(
                "legacy cache requires cryptography before migration; cache was not replaced"
            )
        try:
            return kek._dpapi(False, ciphertext[len(MAGIC):])
        except kek.KekError as exc:
            raise CacheProtectionError("Windows cache cannot be decrypted by this user") from exc
