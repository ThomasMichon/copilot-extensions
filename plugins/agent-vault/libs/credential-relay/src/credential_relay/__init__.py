"""Credential relay -- TCP server for relaying git credentials.

Listens on port 9857 for git-credential-protocol connections and routes
them to pluggable credential sources (Git Credential Manager, ``gh auth``,
etc.). Used by CodeSpaces via SSH ``-R`` port forwarding.
"""

from .server import CredentialRelayServer, RelayPolicy, RelayStats
from .registry import RelayBuilder, TokenRegistry
from .sources import CredentialSource

__all__ = [
    "CredentialRelayServer",
    "CredentialSource",
    "RelayBuilder",
    "RelayPolicy",
    "RelayStats",
    "TokenRegistry",
]
