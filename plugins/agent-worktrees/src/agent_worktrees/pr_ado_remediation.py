"""Consumed-before-write state for bounded native PR remediation."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

from .handoff_trace import _append_lock
from .registry_paths import registry_path
from .tracking import _atomic_write


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class RemediationError(ValueError):
    """A refused or uncertain remediation, never an automatic retry instruction."""


class Ledger:
    def __init__(self, target: str, *, state_dir: Path | None = None):
        root = state_dir if state_dir is not None else registry_path("pr-remediation")
        self.path = Path(root) / (target + ".json")
        self.lock_path = self.path.with_suffix(".lock")
        self.target = target
        self.records = {}

    @contextmanager
    def locked(self):
        with _append_lock(self.lock_path):
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if (not isinstance(data, dict) or data.get("version") != 1
                            or data.get("target") != self.target
                            or not isinstance(data.get("records"), dict)):
                        raise ValueError()
                    self.records = data["records"]
                    for key, row in self.records.items():
                        if (not isinstance(key, str) or not isinstance(row, dict)
                                or row.get("state") not in ("in_flight", "unknown", "verified")
                                or not isinstance(row.get("request_hash"), str)
                                or len(row["request_hash"]) != 64
                                or any(c not in "0123456789abcdef" for c in row["request_hash"])):
                            raise ValueError()
                except (ValueError, OSError) as exc:
                    raise RemediationError("Remediation ledger is corrupt or unreadable; refusing writes") from exc
            yield self

    def save(self):
        _atomic_write(self.path, json.dumps({
            "version": 1, "target": self.target, "records": self.records,
        }, sort_keys=True))
        with self.path.open("r+b") as stream:
            os.fsync(stream.fileno())
        if os.name != "nt":
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def consume(self, key, request_hash, **metadata):
        if key in self.records:
            raise RemediationError("Remediation already consumed; reconcile without another write")
        self.records[key] = {"state": "in_flight", "request_hash": request_hash, **metadata}
        self.save()

    def update(self, key, **fields):
        self.records[key].update(fields)
        self.save()
