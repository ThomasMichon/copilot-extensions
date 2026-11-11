"""Existing AVK1 and Fernet formats backed by native PyCryptodome primitives."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time

from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import pad, unpad


class AesGcm:
    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("KEKs must contain 32 bytes")
        self._key = key

    def encrypt(self, nonce: bytes, plaintext: bytes, associated_data: bytes) -> bytes:
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce, mac_len=16)
        cipher.update(associated_data)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        return ciphertext + tag

    def decrypt(self, nonce: bytes, ciphertext: bytes, associated_data: bytes) -> bytes:
        if len(ciphertext) < 16:
            raise ValueError("GCM authentication tag missing")
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce, mac_len=16)
        cipher.update(associated_data)
        return cipher.decrypt_and_verify(ciphertext[:-16], ciphertext[-16:])


class InvalidToken(ValueError):
    """Malformed or unauthenticated Fernet data."""


class Fernet:
    """The cache's no-TTL Fernet interface; expiry remains in its entry records.

    Wire format: version || timestamp || IV || AES-CBC/PKCS7 || HMAC-SHA256.
    See https://github.com/fernet/spec/blob/master/Spec.md.
    """

    def __init__(self, key: bytes):
        try:
            raw = base64.b64decode(key, altchars=b"-_", validate=True)
        except (ValueError, TypeError, binascii.Error):
            raise ValueError("Fernet key must encode 32 bytes") from None
        if len(raw) != 32:
            raise ValueError("Fernet key must encode 32 bytes")
        self._signing_key, self._encryption_key = raw[:16], raw[16:]

    @staticmethod
    def generate_key() -> bytes:
        return base64.urlsafe_b64encode(secrets.token_bytes(32))

    def encrypt(self, plaintext: bytes) -> bytes:
        iv = secrets.token_bytes(16)
        cipher = AES.new(self._encryption_key, AES.MODE_CBC, iv=iv)
        body = (
            b"\x80" + struct.pack(">Q", int(time.time())) + iv
            + cipher.encrypt(pad(plaintext, 16))
        )
        tag = hmac.digest(self._signing_key, body, hashlib.sha256)
        return base64.urlsafe_b64encode(body + tag)

    def decrypt(self, token: bytes | str) -> bytes:
        try:
            raw = base64.b64decode(token, altchars=b"-_", validate=True)
            if len(raw) < 73 or raw[0] != 0x80 or (len(raw) - 57) % 16:
                raise ValueError
            expected = hmac.digest(self._signing_key, raw[:-32], hashlib.sha256)
            if not hmac.compare_digest(expected, raw[-32:]):
                raise ValueError
            cipher = AES.new(self._encryption_key, AES.MODE_CBC, iv=raw[9:25])
            return unpad(cipher.decrypt(raw[25:-32]), 16)
        except (ValueError, TypeError, binascii.Error):
            raise InvalidToken("Invalid protected cache token") from None
