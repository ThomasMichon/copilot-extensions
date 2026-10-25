"""Opt-in account credentials with exact, locally approved recipient bindings.

Account credentials are NOT downscoped credentials. Each operation rereads both
the policy and the selected provider. Capability expiry never attests token expiry.
"""

from __future__ import annotations

import json
import asyncio
import os
import secrets
import time
from pathlib import Path
from contextlib import contextmanager

from .sources.gh_auth import GhAuthSource

ACTION = "get-account-token"
REQUEST_FIELDS = frozenset({"source", "account", "recipient", "host", "action"})


@contextmanager
def publication_guard(policy_path):
    """One OS-owned cross-process guard; never unlink the lock's identity."""
    filename = Path(policy_path).with_name("account-relay-publication.lock")
    descriptor = os.open(filename, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("account-relay-publication-busy") from None
        locked = True
        yield
    finally:
        try:
            if locked:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate policy field")
        result[key] = value
    return result


def read_reference(path: str, limit: int = 65536) -> bytes:
    reference = Path(path)
    if not reference.is_absolute() or reference.is_symlink():
        raise ValueError("absolute owned reference required")
    with reference.open("rb") as stream:
        value = stream.read(limit + 1)
    if not value or len(value) > limit:
        raise ValueError("invalid reference size")
    return value


def _text(value):
    if (
        not isinstance(value, str) or not value or len(value) > 2048
        or any(ord(char) < 33 or ord(char) == 127 for char in value)
    ):
        raise ValueError("invalid account relay field")
    return value


class AccountRelay:
    """Source custodian; a path or source label alone is never authorization."""

    def __init__(self, policy_path: str):
        self.policy_path = str(Path(policy_path))
        if not Path(self.policy_path).is_absolute():
            raise ValueError("absolute policy reference required")
        self._server = None
        self._closing_server = None
        self._publication = None
        self._tasks = set()
        self._active = False
        self._withdraw_pending = False

    async def prepare(self):
        """Reserve an account-only listener without admitting or publishing it."""
        if self._server is not None or self._closing_server is not None:
            raise RuntimeError("account relay already running")
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self._publication = {"port": port, "pid": os.getpid(), "generation": secrets.token_hex(16)}

    def activate(self):
        """Open admission only after the embedding's current ownership grant."""
        if self._server is None or self._active:
            raise RuntimeError("account relay not prepared")
        destination = Path(self.policy_path).with_name("account-relay-port.json")
        stage = destination.with_name(
            f".{destination.name}.{os.getpid()}.{secrets.token_hex(8)}.new",
        )
        try:
            with publication_guard(self.policy_path):
                stage.write_text(json.dumps(self._publication), encoding="utf-8")
                os.replace(stage, destination)
                self._active = True
        finally:
            stage.unlink(missing_ok=True)

    async def start(self):
        await self.prepare()
        try:
            self.activate()
        except BaseException:
            await self.stop()
            raise

    def close(self):
        """Withdraw admission/discovery and cancel before waiting on transports."""
        self._active = False
        if self._server is not None:
            self._server.close()
            self._closing_server = self._server
            self._server = None
        for task in tuple(self._tasks):
            task.cancel()
        destination = Path(self.policy_path).with_name("account-relay-port.json")
        try:
            with publication_guard(self.policy_path):
                if self._publication is not None and json.loads(
                    destination.read_text(encoding="utf-8"),
                ) == self._publication:
                    destination.unlink()
        except (FileNotFoundError, ValueError):
            pass
        except (OSError, RuntimeError):
            self._withdraw_pending = True
            return
        self._publication = None
        self._withdraw_pending = False

    async def stop(self):
        self.close()
        tasks = tuple(self._tasks)
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=20)
        if self._closing_server is not None:
            await asyncio.wait_for(self._closing_server.wait_closed(), timeout=20)
            self._closing_server = None
        deadline = time.monotonic() + 2
        while self._withdraw_pending and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
            self.close()
        if self._withdraw_pending:
            raise RuntimeError("account-relay-withdrawal-unconfirmed")

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            if not self._active:
                return
            data = await asyncio.wait_for(reader.readuntil(b"\n\n"), timeout=20)
            if len(data) > 16384:
                return
            lines = data.decode("utf-8").strip().splitlines()
            if not lines or lines[0] != ACTION:
                return
            fields = {}
            for line in lines[1:]:
                key, separator, value = line.partition("=")
                if not separator or key in fields:
                    return
                fields[key] = value
            capability = fields.pop("auth", "")
            raw = await asyncio.wait_for(self.resolve(fields, capability), timeout=20)
            if raw:
                writer.write(raw.encode("utf-8"))
                await asyncio.wait_for(writer.drain(), timeout=5)
        except (OSError, UnicodeError, ValueError, asyncio.IncompleteReadError,
                asyncio.LimitOverrunError, asyncio.TimeoutError):
            pass
        finally:
            self._tasks.discard(task)
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except (OSError, asyncio.TimeoutError):
                pass

    async def resolve(self, fields: dict[str, str], capability: str) -> str | None:
        if set(fields) != REQUEST_FIELDS or not capability:
            return None
        try:
            for value in fields.values():
                _text(value)
            policy_bytes = read_reference(self.policy_path)
            document = json.loads(
                policy_bytes, object_pairs_hook=_pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            if (
                not isinstance(document, dict)
                or set(document) != {"schema", "version", "providers", "grants"}
                or document["schema"] != "credential-relay.account-policy"
                or type(document["version"]) is not int or document["version"] != 1
                or not isinstance(document["providers"], dict)
                or not isinstance(document["grants"], list)
                or len(document["providers"]) > 64 or len(document["grants"]) > 128
            ):
                return None
            providers = document["providers"]
            for name, provider in providers.items():
                _text(name)
                if (
                    not isinstance(provider, dict)
                    or set(provider) != {"kind", "account", "host"}
                    or provider["kind"] != "gh-auth"
                ):
                    return None
                _text(provider["account"])
                _text(provider["host"])
            selected = []
            identities = set()
            now = time.time()
            for grant in document["grants"]:
                if (
                    not isinstance(grant, dict)
                    or set(grant) != REQUEST_FIELDS | {"provider", "capability_file", "expires_at"}
                    or type(grant["expires_at"]) not in (int, float)
                    or not 0 < grant["expires_at"] < 1e12
                ):
                    return None
                for key in REQUEST_FIELDS | {"provider"}:
                    _text(grant[key])
                if (
                    not isinstance(grant["capability_file"], str)
                    or not Path(grant["capability_file"]).is_absolute()
                    or any(ord(char) < 32 for char in grant["capability_file"])
                ):
                    return None
                identity = tuple(grant[key] for key in sorted(REQUEST_FIELDS))
                if identity in identities:
                    return None
                identities.add(identity)
                provider = providers.get(grant["provider"])
                if (
                    provider is None or provider["account"] != grant["account"]
                    or provider["host"] != grant["host"]
                    or grant["action"] != "get-github-token"
                ):
                    return None
                if all(grant[key] == fields[key] for key in REQUEST_FIELDS):
                    selected.append(grant)
            if len(selected) != 1 or selected[0]["expires_at"] <= now:
                return None
            grant = selected[0]
            expected = read_reference(grant["capability_file"], 4096).decode("ascii").strip()
            if len(expected) < 32 or not secrets.compare_digest(expected, capability):
                return None
            provider = GhAuthSource(account=fields["account"], github_hosts=[fields["host"]])
            raw = await provider.resolve(
                fields["action"], {"host": fields["host"]}, timeout=15,
            )
            # Revocation or expiry while the provider ran must fence delivery.
            if read_reference(self.policy_path) != policy_bytes or time.time() >= grant["expires_at"]:
                return None
            actual = read_reference(grant["capability_file"], 4096).decode("ascii").strip()
            if not secrets.compare_digest(actual, expected):
                return None
            return raw
        except (OSError, ValueError, TypeError, KeyError, UnicodeError):
            return None
