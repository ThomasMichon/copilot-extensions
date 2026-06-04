"""Credential relay -- TCP server for relaying git credentials.

Listens on port 9847 for git-credential-protocol connections and routes
them to pluggable credential sources (Git Credential Manager, ``gh auth``,
etc.). Used by CodeSpaces via SSH ``-R`` port forwarding.
"""

from .server import CredentialRelayServer, RelayPolicy, RelayStats
from .sources import CredentialSource

__all__ = [
    "CredentialRelayServer",
    "CredentialSource",
    "RelayPolicy",
    "RelayStats",
]
