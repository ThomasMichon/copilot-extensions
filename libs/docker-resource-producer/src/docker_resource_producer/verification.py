"""Authoritative operations only through provider-admitted local sessions."""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote, urlencode

from .local_session import LocalDaemonAdmission, LocalDaemonSession
from .models import (
    LABEL_CREATOR, LABEL_GENERATION, LABEL_TOKEN, Endpoint, Identity,
    VerificationError, birth_labels,
)


class LocalDockerVerifier:
    """No ledger writes, allocation permission, remote fallback or CLI mutation."""

    def __init__(self, endpoint: Endpoint, admission: LocalDaemonAdmission, *, timeout: float = 8) -> None:
        self.endpoint = endpoint
        self.admission = admission
        self.timeout = timeout

    def _inventory(self, session: LocalDaemonSession, kind: str, token: str | None = None) -> list[str]:
        parameters: dict[str, str] = {"all": "1"} if kind == "container" else {}
        if token is not None:
            parameters["filters"] = json.dumps({"label": [f"{LABEL_TOKEN}={token}"]}, separators=(",", ":"))
        target = "/containers/json" if kind == "container" else "/volumes"
        if parameters:
            target += "?" + urlencode(parameters)
        result = session.request("GET", target)
        if kind == "volume":
            if not isinstance(result, dict):
                raise VerificationError("Incomplete Docker volume inventory")
            warnings = result.get("Warnings")
            if warnings is not None and (not isinstance(warnings, list) or warnings):
                raise VerificationError("Incomplete Docker volume inventory")
            result = result.get("Volumes")
        if not isinstance(result, list):
            raise VerificationError("Malformed Docker inventory")
        field = "Id" if kind == "container" else "Name"
        identifiers = [entry.get(field) if isinstance(entry, dict) else None for entry in result]
        pattern = r"[0-9a-f]{64}" if kind == "container" else r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}"
        if any(not isinstance(value, str) or re.fullmatch(pattern, value) is None for value in identifiers):
            raise VerificationError("Malformed Docker inventory identity")
        if len(set(identifiers)) != len(identifiers):
            raise VerificationError("Duplicate Docker inventory identity")
        return identifiers

    def _identity(self, session: LocalDaemonSession, kind: str, identifier: str, token: str, creator: str) -> Identity:
        target = f"/containers/{identifier}/json" if kind == "container" else f"/volumes/{quote(identifier, safe='')}"
        result = session.request("GET", target)
        if not isinstance(result, dict) or result.get("Id" if kind == "container" else "Name") != identifier:
            raise VerificationError("Docker resource identity does not match")
        config = result.get("Config") if kind == "container" else result
        labels = config.get("Labels") if isinstance(config, dict) else None
        if not isinstance(labels, dict) or labels.get(LABEL_TOKEN) != token or labels.get(LABEL_CREATOR) != creator:
            raise VerificationError("Docker creator evidence does not match")
        epoch: Any = labels.get(LABEL_GENERATION)
        if not isinstance(epoch, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", epoch):
            raise VerificationError("Malformed Docker generation label")
        return Identity(kind, identifier, result.get("Created" if kind == "container" else "CreatedAt"),
                        self.endpoint.daemon_id, token, creator, int(epoch))

    def _probe(self, session: LocalDaemonSession, identity: Identity) -> bool:
        if identity.daemon_id != self.endpoint.daemon_id:
            raise VerificationError("Resource belongs to another daemon")
        exists = identity.resource_id in self._inventory(session, identity.resource_type)
        if exists and self._identity(session, identity.resource_type, identity.resource_id,
                                     identity.token, identity.creator) != identity:
            raise VerificationError("Docker birth identity changed")
        return exists

    def probe(self, identity: Identity) -> bool:
        """Prove current existence against the same admitted daemon connection."""
        with LocalDaemonSession(self.endpoint, self.admission, timeout=self.timeout) as session:
            exists = self._probe(session, identity)
            session._check_response_boundary()
            return exists

    def find_reservation(self, token: str, creator: str) -> list[Identity]:
        """Inventory both resource kinds on one daemon; never attest quiescence."""
        birth_labels(token, creator, 1)
        with LocalDaemonSession(self.endpoint, self.admission, timeout=self.timeout) as session:
            identities = [
                self._identity(session, kind, identifier, token, creator)
                for kind in ("container", "volume")
                for identifier in self._inventory(session, kind, token)
            ]
            session._check_response_boundary()
            return identities

    def remove(self, identity: Identity, *, authorized: bool = False) -> bool:
        """Refuse until the daemon supports atomic birth-conditional deletion."""
        if authorized is not True:
            raise VerificationError("Exact resource cleanup is not authorized")
        if identity.resource_type == "volume":
            raise VerificationError("Docker has no birth-conditional volume deletion; automatic cleanup remains blocked")
        raise VerificationError("Docker container deletion is not ID-only; automatic cleanup remains blocked")
