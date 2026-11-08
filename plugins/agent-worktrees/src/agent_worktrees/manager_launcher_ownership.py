"""Pure full-byte recognition of published Manager launchers."""

from __future__ import annotations

import hashlib


_OWNED_STUBS = {
    "worktree-manager.cmd": {
        "e5166fbe6d9bb2ffb2e18b6adb5b9fd28b6aada9be772da784508ef789b72efd",
        "fa96da97ca82f2f3ac72fc33bed22b04467ec1eee018e289bcc70d691766d858",
        "db1f2e5c3e7df70d8eee1659fd08d465485d4ad49d0bc7fb9900ff8667dd8022",
        "84c13cdc024a25402bbe1428ff13410da1ffb9b9f9c21245846bc8b02c46e443",
    },
    "worktree-manager.ps1": {
        "3ca986ce56b2a4bd8ffe1a1cd20dad83ba69c8a77eb5162f66b2cf745104eeff",
        "29c2bae43865a9307665f83fb2ed3c7e24c6d661b906dc47f44950d8900febbc",
        "55d2077997a9d1528b5c1e9aba3a4eb04327366ba2460e9244254f42b42ba2db",
        "fe7809ba94fa714b866ce6f6e33d136b4b3e877db909778c41181cdd4ad0b030",
        "56e68cfacd94738a19081a5ca0e843524c7be939aa28bedc77a8e79efbf527ae",
    },
    "worktree-manager": {
        "1d378d9461dcb0c892d1b64f443c8a38dee4ff666ca484db62697bb3424b2c65",
        "37836dbddadb559b209ec2ab08ba0f9fd062503f6794441c17c2cb9f44390ca2",
        "c23fd51c53fd009783ff161163a76f7fef4674e3822278a7ff228529e508d5b2",
        "da4d602f53b5c55c242e567c037d0a1e47cf8b7cc6bc34483234242008f5657d",
    },
}


def owns_launcher(name: str, body: bytes | None) -> bool:
    return (
        body is not None
        and hashlib.sha256(body).hexdigest() in _OWNED_STUBS.get(name, ())
    )
