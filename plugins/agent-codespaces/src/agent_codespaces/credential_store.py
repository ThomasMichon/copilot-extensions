"""Provider-owned credentials, never GitHub CLI keyring slots."""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

import keyring
import yaml

from .oauth_device import CredentialUnavailable

log = logging.getLogger("agent-codespaces")


def service() -> str:
    from .picker_auth import auth_root

    identity = hashlib.sha256(str(auth_root().resolve()).encode("utf-8")).hexdigest()[:24]
    return f"agent-codespaces:{identity}:github.com"


def save(path: Path, login: str, credential: str) -> None:
    try:
        keyring.set_password(service(), login, credential)
        if keyring.get_password(service(), login) != credential:
            raise keyring.errors.PasswordSetError("Provider keyring write could not be verified.")
    except keyring.errors.KeyringError:
        if os.name == "nt":
            raise RuntimeError("Provider OS keyring is unavailable; credential was not adopted.") from None
        log.warning("OS keyring unavailable; storing OAuth in the private provider configuration.")
        _save_file(path, login, credential)
        kind = "file"
    else:
        kind = "keyring"
    (path / "store-kind").write_text(kind, encoding="utf-8")


def load(path: Path, login: str) -> str:
    kind = (path / "store-kind").read_text(encoding="utf-8")
    if kind == "keyring":
        try:
            credential = keyring.get_password(service(), login)
        except keyring.errors.KeyringError:
            raise RuntimeError("Provider OS keyring is unavailable; unlock it and refresh.") from None
        if not credential:
            raise CredentialUnavailable("Provider credential is missing; sign in again.")
        return credential
    if kind != "file":
        raise RuntimeError("Provider credential storage metadata is invalid.")
    try:
        document = yaml.safe_load((path / "hosts.yml").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CredentialUnavailable("Provider credential is missing; sign in again.") from None
    record = document.get("github.com") if isinstance(document, dict) else None
    if (not isinstance(record, dict)
            or str(record.get("user", "")).casefold() != login.casefold()
            or not isinstance(record.get("oauth_token"), str)
            or not record["oauth_token"]):
        raise RuntimeError("Provider credential configuration is invalid.")
    return record["oauth_token"]


def _save_file(path: Path, login: str, credential: str) -> None:
    document = {"github.com": {
        "user": login, "oauth_token": credential, "git_protocol": "https",
        "users": {login: {"oauth_token": credential}},
    }}
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path, delete=False) as stream:
            pending = Path(stream.name)
            yaml.safe_dump(document, stream)
        pending.replace(path / "hosts.yml")
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
