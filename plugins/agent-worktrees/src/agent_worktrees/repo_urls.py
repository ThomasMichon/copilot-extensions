"""Repository host authority normalization without registry or account discovery."""

from __future__ import annotations

_DNS_CHARACTERS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.")
_DEFAULT_PORTS = {"http": 80, "https": 443, "ssh": 22, "git": 9418}


def remote_host(value: str, scheme: str = "https") -> str:
    """Canonical host authority, retaining only non-default network ports."""
    if isinstance(value, str) and isinstance(scheme, str) and scheme.casefold() in _DEFAULT_PORTS:
        host, separator, port = value.partition(":")
        if host and all(character in _DNS_CHARACTERS for character in host):
            if not separator:
                return host.casefold()
            if port and port.isascii() and port.isdecimal():
                number = int(port)
                if 0 <= number <= 65535:
                    return host.casefold() + (
                        ":" + str(number) if number != _DEFAULT_PORTS[scheme.casefold()] else ""
                    )
    from urllib.parse import urlsplit

    parsed = urlsplit(scheme + "://" + value)
    if (
        not parsed.hostname or parsed.username or parsed.password
        or parsed.path or parsed.query or parsed.fragment
    ):
        raise ValueError("remote host must be a host authority")
    host = parsed.hostname.casefold()
    if ":" in host:
        host = "[" + host + "]"
    default = _DEFAULT_PORTS.get(scheme.casefold())
    if parsed.port is not None and parsed.port != default:
        host += ":" + str(parsed.port)
    return host
