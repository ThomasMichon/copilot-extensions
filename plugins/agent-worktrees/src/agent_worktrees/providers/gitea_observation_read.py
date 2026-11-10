"""Byte-bounded typed Gitea reads with authorization off the process command line."""

from __future__ import annotations

import time
from urllib.parse import urlsplit

from .base import ProviderError
from .observation_credentials import ObservationCredentialError


def read(method, url, credentials, *, invoke, payload=None, deadline=None):
    if method != "GET" or payload is not None:
        raise ProviderError("observation credentials permit only native reads")
    address = urlsplit(url)
    if (
        address.scheme != "https" or not address.hostname
        or address.username is not None or address.password is not None
        or address.fragment or any(ord(char) < 32 for char in url)
    ):
        raise ObservationCredentialError("typed Gitea observation requires an HTTPS endpoint")
    address.port
    authorization = credentials.authorization()
    remaining = 60 if deadline is None else deadline - time.monotonic()
    if remaining <= 0:
        raise ObservationCredentialError("observation inventory deadline exhausted")
    result = invoke(
        ["curl", "-sS", "-X", "GET", url, "-H", "@-", "-H", "Accept: application/json",
         "-w", "\n%{http_code}"],
        input_text="Authorization: " + authorization + "\n",
        timeout=max(1, int(remaining)), max_stdout_bytes=1_048_584, max_stderr_bytes=8192,
    )
    if result.returncode:
        raise ObservationCredentialError("Gitea observation read unavailable", transient=True)
    body, separator, raw_status = result.stdout.rpartition("\n")
    try:
        status = int(raw_status) if separator else 0
    except ValueError:
        status = 0
    if status in (401, 403):
        raise ObservationCredentialError("Gitea observation authorization lost", transient=True)
    return status, body
