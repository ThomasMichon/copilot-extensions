"""Acquire bounded public signing trust from the configured Entra tenant."""

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from http.client import HTTPException
from pathlib import Path
from typing import Any, NoReturn
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, build_opener
from uuid import UUID

MAX_RESPONSE_BYTES = 256 * 1024
MAX_TRUST_BYTES = 64 * 1024
MAX_KEYS = 16
TRUST_SECONDS = 300
_CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class EntraTrustError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _guid(value: object) -> None:
    try:
        valid = type(value) is str and str(UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise EntraTrustError("invalid-application")


@dataclass(frozen=True)
class EntraApplication:
    tenant: str = field(repr=False)
    audience: str = field(repr=False)
    required_role: str
    required_scope: str

    def __post_init__(self) -> None:
        _guid(self.tenant)
        _guid(self.audience)
        if (type(self.required_role) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.required_role) is None):
            raise EntraTrustError("invalid-application")
        if (type(self.required_scope) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}", self.required_scope) is None):
            raise EntraTrustError("invalid-application")

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant}/discovery/v2.0/keys"


@dataclass(frozen=True)
class TrustSnapshot:
    issuer: str
    audience: str
    jwks: bytes = field(repr=False)
    fetched_at: int
    trust_valid_until: int


def _unique(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EntraTrustError("invalid-public-trust")
        result[key] = value
    return result


def _public_keys(raw: object) -> bytes:
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_RESPONSE_BYTES:
        raise EntraTrustError("invalid-public-trust")
    try:
        value = json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, UnicodeError):
        raise EntraTrustError("invalid-public-trust") from None
    if (type(value) is not dict or set(value) != {"keys"}
            or type(value["keys"]) is not list or not 1 <= len(value["keys"]) <= MAX_KEYS):
        raise EntraTrustError("invalid-public-trust")
    keys: list[dict[str, Any]] = []
    seen: set[str] = set()
    metadata = {"x5c", "x5t", "x5t#S256", "issuer", "cloud_instance_name"}
    public = {"kty", "kid", "n", "e", "alg", "use", "key_ops"}
    for key in value["keys"]:
        if (type(key) is not dict or key.keys() - public - metadata
                or not {"kty", "kid", "n", "e"} <= key.keys()
                or key["kty"] != "RSA" or key.get("alg", "RS256") != "RS256"
                or key.get("use", "sig") != "sig"
                or key.get("key_ops", ["verify"]) != ["verify"]):
            raise EntraTrustError("invalid-public-trust")
        kid = key["kid"]
        if (type(kid) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", kid) is None
                or kid in seen):
            raise EntraTrustError("invalid-public-trust")
        for name, maximum in (("n", 1024), ("e", 16)):
            item = key[name]
            if (type(item) is not str or not 0 < len(item) <= maximum
                    or re.fullmatch(r"[A-Za-z0-9_-]+", item) is None):
                raise EntraTrustError("invalid-public-trust")
        seen.add(kid)
        keys.append({name: key[name] for name in ("kty", "kid", "n", "e")}
                    | {"alg": "RS256", "use": "sig", "key_ops": ["verify"]})
    result = json.dumps({"keys": sorted(keys, key=lambda key: key["kid"])},
                        sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(result) > MAX_TRUST_BYTES:
        raise EntraTrustError("invalid-public-trust")
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any,
                         newurl: str) -> None:
        return None


def _read_keys(tenant: object, timeout: float) -> bytes:
    _guid(tenant)
    url = f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys"
    try:
        with build_opener(_NoRedirect()).open(url, timeout=timeout) as response:
            if response.status != 200 or response.geturl() != url:
                raise EntraTrustError("public-trust-unavailable")
            raw: bytes = response.read(MAX_RESPONSE_BYTES + 1)
            return raw
    except (HTTPException, URLError, OSError, TimeoutError):
        raise EntraTrustError("public-trust-unavailable") from None


def _acquire_keys(tenant: str, timeout: float) -> bytes:
    # A finite child makes DNS, headers and slow-progress bodies interruptible.
    deadline = time.monotonic() + timeout
    interpreter = getattr(sys, "_base_executable", sys.executable)
    command = [interpreter, "-I", str(Path(__file__).resolve()), "--fetch-keys", str(deadline)]
    try:
        with subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        ) as process:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise EntraTrustError("public-trust-unavailable")
                raw, error = process.communicate(
                    json.dumps({"tenant": tenant, "timeout": remaining}).encode("ascii"),
                    timeout=remaining,
                )
                if process.returncode != 0 or error or time.monotonic() >= deadline:
                    raise EntraTrustError("public-trust-unavailable")
                return raw
            except subprocess.TimeoutExpired:
                raise EntraTrustError("public-trust-unavailable") from None
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
    except OSError:
        raise EntraTrustError("public-trust-unavailable") from None


def fetch_trust(application: EntraApplication, *, clock: Callable[[], float] = time.time,
                timeout: float = 10) -> TrustSnapshot:
    """Explicit HTTPS public-key acquisition, outside pure verifier callbacks.

    The common Fleet verifier must still validate these RSA keys and every user
    token. A successful fetch is neither user authentication nor a policy grant.
    """
    if (type(application) is not EntraApplication or not callable(clock)
            or type(timeout) not in (int, float) or not 0 < timeout <= 30):
        raise EntraTrustError("invalid-application")
    started = clock()
    if type(started) not in (int, float) or not 0 <= started < 2**53:
        raise EntraTrustError("invalid-clock")
    raw = _acquire_keys(application.tenant, timeout)
    jwks = _public_keys(raw)
    completed = clock()
    if (type(completed) not in (int, float)
            or not started <= completed < started + TRUST_SECONDS):
        raise EntraTrustError("invalid-clock")
    fetched_at = int(started)
    return TrustSnapshot(application.issuer, application.audience, jwks,
                         fetched_at, fetched_at + TRUST_SECONDS)


def _exit(code: int) -> NoReturn:
    os._exit(code)


def _fetch_worker(deadline: float) -> int:
    remaining = deadline - time.monotonic()
    if not 0 < remaining <= 30:
        return 2
    # This read-only worker must expire even if its parent disappears.
    timer = threading.Timer(remaining, _exit, args=(2,))
    timer.daemon = True
    timer.start()
    try:
        raw_request = sys.stdin.buffer.read(4097)
        if len(raw_request) > 4096:
            return 2
        request = json.loads(raw_request, object_pairs_hook=_unique)
        if (type(request) is not dict or set(request) != {"tenant", "timeout"}
                or type(request["timeout"]) not in (int, float)
                or not 0 < request["timeout"] <= 30):
            return 2
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 2
        raw = _read_keys(request["tenant"], min(request["timeout"], remaining))
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_RESPONSE_BYTES:
            return 2
        sys.stdout.buffer.write(raw)
        return 0
    except (EntraTrustError, ValueError, UnicodeError, OSError):
        return 2
    finally:
        timer.cancel()
        timer.join()


if __name__ == "__main__":
    try:
        deadline = float(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == "--fetch-keys" else None
    except ValueError:
        deadline = None
    raise SystemExit(_fetch_worker(deadline) if deadline is not None else 2)
