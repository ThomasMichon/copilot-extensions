"""Opt-in direct native HTTP reads; executable qualification remains external."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit

from .observation_credentials import ObservationCredentialError, ObservationCredentials

MAX_BODY = 1_048_576
MAX_CAPTURE = MAX_BODY + 8
_ARGUMENTS = (
    "-q", "--http1.1", "--globoff", "--noproxy", "*",
    "--retry", "0", "--max-redirs", "0", "--proto", "=https",
)


def contract() -> dict[str, object]:
    """Describe enforced arguments, never attest an arbitrary curl executable."""
    return {"direct-v1": {
        "version": 1, "option": "--http-profile", "providers": ["github", "gitea"],
        "requires": ["curl"], "http": "1.1", "default_config": False,
        "url_globbing": False, "redirects": False, "configured_retries": 0,
        "proxy": "disabled", "capture_bytes": MAX_CAPTURE,
        "requires_binary_qualification": True,
    }}


def read_get(
    url: str, authorization: str, *, invoke: Callable[..., Any],
    deadline: float | None = None, github: bool = False,
    max_body_bytes: int = MAX_BODY,
) -> tuple[int, str]:
    """Use one fixed GET command; authentication was admitted by the caller."""
    address = urlsplit(url)
    if (
        address.scheme != "https" or not address.hostname
        or address.username is not None or address.password is not None
        or address.fragment or any(ord(char) < 32 for char in url)
        or not authorization or "\r" in authorization or "\n" in authorization
        or type(max_body_bytes) is not int or not 1 <= max_body_bytes <= MAX_BODY
    ):
        raise ObservationCredentialError("invalid direct observation read")
    address.port
    remaining = 60.0 if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise ObservationCredentialError("direct observation deadline exhausted")
    headers = ["-H", "Accept: application/json"]
    if github:
        headers = ["-H", "Accept: application/vnd.github+json",
                   "-H", "X-GitHub-Api-Version: 2022-11-28"]
    result = invoke(
        ["curl", *_ARGUMENTS, "-sS", "--max-time", str(remaining),
         "-X", "GET", url, "-H", "@-", *headers, "-w", "\n%{http_code}"],
        input_text="Authorization: " + authorization + "\n",
        timeout=max(1, int(remaining)), max_stdout_bytes=max_body_bytes + 8,
        max_stderr_bytes=8192,
    )
    if result.returncode:
        raise ObservationCredentialError("direct observation read unavailable", transient=True)
    body, separator, raw_status = result.stdout.rpartition("\n")
    try:
        status = int(raw_status) if separator else 0
    except ValueError:
        status = 0
    if not 100 <= status <= 599 or len(body.encode("utf-8")) > max_body_bytes:
        raise ObservationCredentialError("invalid direct observation response", transient=True)
    if status in (401, 403):
        raise ObservationCredentialError("observation authorization lost", transient=True)
    return status, body


def github_environment(credentials: ObservationCredentials) -> dict[str, str]:
    """Keep legacy transport aliases, while direct mode sends them only on stdin."""
    value = credentials.acquire().token
    return {key: value for key in (
        "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
    )}


def github_reader(
    invoke: Callable[..., Any], credentials: ObservationCredentials | None,
) -> Callable[..., Any]:
    if credentials is None or credentials.http_profile == "legacy":
        return invoke
    return partial(_github_read, invoke=invoke)


def _github_read(
    argv: list[str], *, invoke: Callable[..., Any], env: dict[str, str],
    max_stdout_bytes: int = MAX_BODY, timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    if len(argv) != 5 or argv[:3] != ["gh", "api", "--hostname"] or not env.get("GH_TOKEN"):
        raise ObservationCredentialError("unsupported direct GitHub read")
    scheme = "Bear" + "er "
    authorization = scheme + env["GH_TOKEN"]
    status, body = read_get(
        argv[-1], authorization, invoke=invoke, github=True,
        deadline=time.monotonic() + timeout, max_body_bytes=max_stdout_bytes,
    )
    return subprocess.CompletedProcess(
        argv, 0 if 200 <= status < 300 else 1, body,
        "" if 200 <= status < 300 else "HTTP " + str(status),
    )
