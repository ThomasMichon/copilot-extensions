"""Explicit vendor OAuth ceremony; never called by inventory or rendering."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from . import credential_store, gh_account, oauth_device, picker_auth


def add_parser(sub) -> None:
    parser = sub.add_parser("authorize", help="Explicitly sign in/authorize CodeSpaces on this device")
    parser.add_argument("--account", default="", help="Required GitHub login, if already selected")
    parser.add_argument("--user-initiated", action="store_true", help="Confirm explicit operator activation")
    parser.set_defaults(func=command)


def command(args: argparse.Namespace) -> int:
    def emit(kind: str, message: str) -> None:
        key = "msg" if kind == "progress" else "message"
        print(json.dumps({"type": kind, key: message}), flush=True)

    if not args.user_initiated:
        emit("error", "Authorization requires an explicit user action.")
        return 1
    try:
        login = authorize(args.account, lambda message: emit("progress", message))
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        emit("error", str(exc))
        return 1
    except KeyboardInterrupt:
        emit("error", "Authorization cancelled.")
        return 1
    emit("done", f"Authorized {login}; refresh CodeSpaces.")
    return 0


def authorize(account: str, progress) -> str:
    """Run gh in an isolated config, adopt only a scope/identity-verified result."""
    if account and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", account):
        raise ValueError("Invalid GitHub account.")
    root = picker_auth.auth_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or (
        os.name == "posix" and (
            root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077
        )
    ):
        raise RuntimeError("Provider authentication directory must be private to this user.")
    target = f"Authorize GitHub account {account or '(choose in browser)'} on this device."
    progress(target)
    credential = oauth_device.acquire(lambda message: progress(target + "\n" + message))
    verified = oauth_device.verify(credential, account)
    login = verified["account"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login):
        raise RuntimeError("GitHub returned an invalid login.")
    if verified["status"] != "ready":
        raise RuntimeError("Authorization did not grant a verified codespace scope.")
    destination = root / login.casefold()
    destination.mkdir(mode=0o700, exist_ok=True)
    if destination.is_symlink() or (
        os.name == "posix" and (
            destination.stat().st_uid != os.getuid() or destination.stat().st_mode & 0o077
        )
    ):
        raise RuntimeError("Provider credential directory must be private to this user.")
    credential_store.save(destination, login, credential)
    (destination / "accepted-login").write_text(login, encoding="utf-8")
    gh_account.clear_caches()
    return login
