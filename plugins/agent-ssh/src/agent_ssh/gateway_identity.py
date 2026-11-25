"""Prepare local native Gateway keys without enrollment or transport activation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from agent_procutil import no_window_kwargs
from dropin_registry import atomic_write_text

from . import gateway_authorization as authorization
from . import gateway_native, gateway_profile, gateway_sshd
from .file_lock import exclusive_file_lock, validate_lock_file

_KEYS = ("client_ed25519", "host_ed25519")
_FILES = {"identity.json", "identity.lock", *_KEYS, *(name + ".pub" for name in _KEYS)}
_STAGING = re.compile(r"\.identity\.json\.[a-z0-9_]{8}\.tmp")


@dataclass(frozen=True)
class PreparedIdentity:
    """Local references stay local; only descriptor is a public exchange record."""

    client_key: Path
    client_public_key: Path
    host_key: Path
    host_public_key: Path
    descriptor: dict[str, object]


def _keygen(executable: Path, arguments: list[str]) -> bytes:
    result: subprocess.CompletedProcess[bytes] = subprocess.run(
        [str(executable), *arguments], stdin=subprocess.DEVNULL,
        capture_output=True, timeout=15, check=False,
        env=gateway_sshd._process_environment(), **no_window_kwargs(),
    )
    if result.returncode or len(result.stdout) > 4096 or len(result.stderr) > 4096:
        raise ValueError("Selected ssh-keygen could not prepare the native identity")
    return result.stdout


def _public(data: bytes) -> str:
    if len(data) > 4096:
        raise ValueError("Native identity public key exceeds its limit")
    try:
        parts = data.decode("ascii").split()
    except UnicodeError:
        raise ValueError("Native identity public key must be ASCII") from None
    if len(parts) != 2:
        raise ValueError("Native identity requires a canonical uncommented public key")
    return gateway_profile._public_key(" ".join(parts))


def _pair(root: Path, name: str, executable: Path, pinned: str | None) -> str:
    private, public = root / name, root / (name + ".pub")
    exists = os.path.lexists(private)
    if pinned is not None and not exists:
        raise ValueError("Established native private key is missing; preserve its identity pin")
    if os.path.lexists(public):
        authorization._private(public, directory=False)
        if not exists:
            raise ValueError("Native public key has no private key; preserve the existing identity")
    if exists:
        authorization._private(private, directory=False)
    else:
        _keygen(executable, ["-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(private)])
        # ssh-keygen assigns its own permissions to both newly generated files.
        for generated in (private, public):
            if not os.path.lexists(generated):
                continue
            if os.name == "nt":
                authorization._windows_protect_created_file(generated)
            else:
                gateway_sshd._absolute(str(generated))
                info = generated.lstat()
                if not generated.is_file() or info.st_nlink != 1:
                    raise PermissionError("Generated native key must be ordinary and unlinked")
                generated.chmod(0o600)
            authorization._private(generated, directory=False)
        authorization._private(private, directory=False)
    expected = _public(_keygen(executable, ["-y", "-P", "", "-f", str(private)]))
    if pinned is not None and expected != pinned:
        raise ValueError("Native private key differs from its established identity pin")
    if os.path.lexists(public):
        if _public(authorization._read(public).encode("ascii")) != expected:
            raise ValueError("Native public key differs from its private key; preserve both")
    else:
        with public.open("xb") as stream:
            if sys.platform != "win32":
                os.fchmod(stream.fileno(), 0o600)
            stream.write((expected + "\n").encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
        authorization._private(public, directory=False)
    return expected


def prepare(directory: Path, ssh_keygen: Path, *, machine: str, user: str) -> PreparedIdentity:
    """Create missing keys, or validate/reuse the same bound local identity.

    A missing public sidecar can be derived from its surviving private key.
    Invalid, foreign or orphaned public state is never repaired by key rotation.
    """
    machine, user = gateway_profile._alias(machine), gateway_native._user(user)
    account = gateway_sshd._account()
    if (account.casefold() != user.casefold() if os.name == "nt" else account != user):
        raise PermissionError("Native identity must belong to the current login user")
    root = gateway_sshd._absolute(str(directory))
    executable = gateway_sshd._absolute(str(ssh_keygen))
    gateway_sshd._executable(executable)
    if not root.exists():
        try:
            if os.name == "nt":
                authorization._windows_create_directory(root)
            else:
                root.mkdir(mode=0o700)
        except FileExistsError:
            pass
    authorization._private(root, directory=True)
    lock, owner = root / "identity.lock", root / "identity.json"
    try:
        lock_descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(lock_descriptor)
    validate_lock_file(lock)
    authorization._private(lock, directory=False)
    # Four bounded keygen calls plus filesystem/ACL work can exceed Windows' default lock wait.
    with exclusive_file_lock(lock, timeout=120):
        entries = tuple(root.iterdir())
        staging = tuple(entry for entry in entries if _STAGING.fullmatch(entry.name))
        if any(entry.name not in _FILES and entry not in staging for entry in entries):
            raise ValueError("Select a dedicated native identity directory; preserve unrelated contents")
        for entry in entries:
            authorization._private(entry, directory=False)
        binding = {"schema": "agent-ssh.native-identity", "version": 1, "machine": machine, "user": account}
        pins: dict[str, str] = {}
        if os.path.lexists(owner):
            stored = gateway_native._decode(authorization._read(owner).encode("ascii"))
            raw_pins = stored.pop("keys", None)
            if stored != binding or not isinstance(raw_pins, dict) or not set(raw_pins).issubset(_KEYS):
                raise ValueError("Native identity directory belongs to another machine or login")
            pins = {name: gateway_profile._public_key(value) for name, value in raw_pins.items()}
            if len(set(pins.values())) != len(pins):
                raise ValueError("Native identity roles have conflicting pins")
        elif any(entry.name != "identity.lock" and entry not in staging for entry in entries):
            raise ValueError("Native identity has no owner binding; preserve its existing keys")
        # This reserved namespace is dropin_registry.atomic_write_text's private scratch.
        # Validate all entries and the committed owner before discarding an interrupted write.
        for entry in staging:
            entry.unlink()
        if not os.path.lexists(owner):
            atomic_write_text(owner, gateway_profile._json({**binding, "keys": pins}))
        public = {}
        for name in _KEYS:
            public[name] = _pair(root, name, executable, pins.get(name))
            if name not in pins:
                pins[name] = public[name]
                atomic_write_text(owner, gateway_profile._json({**binding, "keys": pins}))
        descriptor = gateway_native._peer({
            "machine": machine, "user": account,
            "public_key": public[_KEYS[0]], "host_key": public[_KEYS[1]],
        })
    return PreparedIdentity(
        root / _KEYS[0], root / (_KEYS[0] + ".pub"),
        root / _KEYS[1], root / (_KEYS[1] + ".pub"), descriptor,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--ssh-keygen", type=Path, required=True)
    parser.add_argument("--machine", required=True)
    parser.add_argument("--user", required=True)
    args = parser.parse_args(argv)
    identity = prepare(args.directory, args.ssh_keygen, machine=args.machine, user=args.user)
    print(json.dumps(identity.descriptor, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
