"""AES-256-GCM via Windows CNG; compatible with existing identity tokens."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes as w


class IdentityCryptoError(ValueError):
    """Native encryption or authentication failed."""


class _AuthInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.ULONG), ("dwInfoVersion", w.ULONG),
        ("pbNonce", ctypes.c_void_p), ("cbNonce", w.ULONG),
        ("pbAuthData", ctypes.c_void_p), ("cbAuthData", w.ULONG),
        ("pbTag", ctypes.c_void_p), ("cbTag", w.ULONG),
        ("pbMacContext", ctypes.c_void_p), ("cbMacContext", w.ULONG),
        ("cbAAD", w.ULONG), ("cbData", ctypes.c_ulonglong), ("dwFlags", w.ULONG),
    ]


def _check(status: int) -> None:
    if status != 0:
        raise IdentityCryptoError("Windows identity encryption or authentication failed")


class WindowsAESGCM:
    def __init__(self, key: bytes):
        if os.name != "nt" or len(key) != 32:
            raise IdentityCryptoError("Windows AES-GCM requires a 32-byte identity key")
        self._key = key

    def encrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        return self._crypt(True, nonce, data, associated_data or b"")

    def decrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        if len(data) < 16:
            raise IdentityCryptoError("identity ciphertext is too short")
        return self._crypt(False, nonce, data, associated_data or b"")

    def _crypt(self, encrypt: bool, nonce: bytes, data: bytes, aad: bytes) -> bytes:
        if len(nonce) != 12:
            raise IdentityCryptoError("identity nonce must contain 12 bytes")
        dll = ctypes.WinDLL("bcrypt.dll")
        handle = ctypes.c_void_p
        ptr = ctypes.c_void_p
        ulong = w.ULONG
        signatures = {
            "BCryptOpenAlgorithmProvider": [ctypes.POINTER(handle), w.LPCWSTR, w.LPCWSTR, ulong],
            "BCryptSetProperty": [handle, w.LPCWSTR, ptr, ulong, ulong],
            "BCryptGetProperty": [handle, w.LPCWSTR, ptr, ulong, ctypes.POINTER(ulong), ulong],
            "BCryptGenerateSymmetricKey": [
                handle, ctypes.POINTER(handle), ptr, ulong, ptr, ulong, ulong,
            ],
            "BCryptEncrypt": [
                handle, ptr, ulong, ptr, ptr, ulong, ptr, ulong, ctypes.POINTER(ulong), ulong,
            ],
            "BCryptDecrypt": [
                handle, ptr, ulong, ptr, ptr, ulong, ptr, ulong, ctypes.POINTER(ulong), ulong,
            ],
            "BCryptDestroyKey": [handle],
            "BCryptCloseAlgorithmProvider": [handle, ulong],
        }
        for name, args in signatures.items():
            function = getattr(dll, name)
            function.argtypes = args
            function.restype = w.LONG
        algorithm, key_handle = handle(), handle()
        _check(dll.BCryptOpenAlgorithmProvider(ctypes.byref(algorithm), "AES", None, 0))
        try:
            mode = ctypes.create_unicode_buffer("ChainingModeGCM")
            _check(dll.BCryptSetProperty(
                algorithm, "ChainingMode", mode, ctypes.sizeof(mode), 0,
            ))
            length, copied = ulong(), ulong()
            _check(dll.BCryptGetProperty(
                algorithm, "ObjectLength", ctypes.byref(length), ctypes.sizeof(length),
                ctypes.byref(copied), 0,
            ))
            key_object = ctypes.create_string_buffer(length.value)
            key_bytes = ctypes.create_string_buffer(self._key)
            _check(dll.BCryptGenerateSymmetricKey(
                algorithm, ctypes.byref(key_handle), key_object, length.value,
                key_bytes, len(self._key), 0,
            ))
            try:
                plaintext = data if encrypt else data[:-16]
                source = ctypes.create_string_buffer(plaintext)
                nonce_buffer = ctypes.create_string_buffer(nonce)
                aad_buffer = ctypes.create_string_buffer(aad)
                tag = ctypes.create_string_buffer(16)
                if not encrypt:
                    ctypes.memmove(tag, data[-16:], 16)
                auth = _AuthInfo()
                auth.cbSize, auth.dwInfoVersion = ctypes.sizeof(auth), 1
                auth.pbNonce, auth.cbNonce = ctypes.addressof(nonce_buffer), len(nonce)
                auth.pbAuthData, auth.cbAuthData = ctypes.addressof(aad_buffer), len(aad)
                auth.pbTag, auth.cbTag = ctypes.addressof(tag), 16
                output = ctypes.create_string_buffer(max(1, len(plaintext)))
                written = ulong()
                operation = dll.BCryptEncrypt if encrypt else dll.BCryptDecrypt
                _check(operation(
                    key_handle, source, len(plaintext), ctypes.byref(auth), None, 0,
                    output, len(plaintext), ctypes.byref(written), 0,
                ))
                result = output.raw[:written.value]
                return result + tag.raw if encrypt else result
            finally:
                dll.BCryptDestroyKey(key_handle)
        finally:
            dll.BCryptCloseAlgorithmProvider(algorithm, 0)
