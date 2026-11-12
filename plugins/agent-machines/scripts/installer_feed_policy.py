"""Shared public-index rejection; affirmative feed trust remains caller-owned."""
from __future__ import annotations

from urllib.parse import urlsplit


_PUBLIC_PYPI_HOSTS = {"pypi.org", "pypi.python.org", "test.pypi.org"}


def _normalize_hostname(host: str) -> str:
    host = host.lower()
    return host[:-1] if host.endswith(".") else host


def _is_public_pypi_url(url: str) -> bool:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    return _normalize_hostname(host or "") in _PUBLIC_PYPI_HOSTS
