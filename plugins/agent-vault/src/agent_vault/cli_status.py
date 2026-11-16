"""User-facing labels for existing service and transport observations."""

from __future__ import annotations


def _vault_status_label(cli_state: str) -> str:
    """Translate internal cli state to user-facing status."""
    if cli_state == "unlocked":
        return "available"
    return "locked"


_TRANSPORT_LABELS: dict[str, str] = {
    "unix-socket": "unix socket",
    "tcp": "local TCP",
    "discovered-pipe": "named pipe (discovered)",
    "discovered-unix": "unix socket (discovered)",
    "discovered-tcp": "local TCP (discovered)",
}
