"""Portable conversations and explicit clients; no implicit hosting or adoption."""

__version__ = "0.1.0-dev1"

from .store import ChatError, ChatStore, json_bytes, strict_json
from .client import Client, ClientError

__all__ = ["ChatError", "ChatStore", "Client", "ClientError", "json_bytes", "strict_json"]
