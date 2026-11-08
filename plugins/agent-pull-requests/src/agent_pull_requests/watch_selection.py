"""Recoverable owner metadata selection around the canonical completed-slot CAS."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from zdd.routing_persistence import windows_replace

from . import _installation_context as api
from .watch_installation import PLUGIN_ID
from .watch_journal import WatchJournal, _regular_bytes, _selection
from .watch_private import _sync_directory
from .watch_runtime import WatchRuntime


def _replace_metadata(path: Path, value: bytes | None, *, expected: bytes | None) -> None:
    """Exact-byte nonsecret metadata; native routing remains its own writer."""
    if _regular_bytes(path) != expected:
        raise PermissionError("watch selection metadata changed before publication")
    if value is None:
        if expected is not None:
            path.unlink()
            _sync_directory(path.parent)
        return
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        if _regular_bytes(path) != expected:
            raise PermissionError("watch selection metadata changed before replacement")
        if os.name == "nt":
            windows_replace(temporary, path)
        else:
            os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class WatchSelection:
    """The journal's held whole-transaction guard encloses canonical metadata locks."""

    def __init__(self, journal: WatchJournal) -> None:
        self.journal = journal
        self.installation = journal.installation

    def _read(self) -> tuple[dict, WatchRuntime, dict]:
        document = self.journal._current()
        target = WatchRuntime.from_evidence(
            document["target"], self.installation, environment=self.journal.environment,
        )
        selected = _selection(self.installation)
        for key in ("current", "lastKnownGood", "manifest"):
            actual = selected[key]
            prior = document["prior"][key]
            if key == "manifest":
                matches_target = actual == document["targetManifest"]
            else:
                matches_target = actual is not None and (
                    bytes.fromhex(actual).decode("utf-8").strip() == target.runtime_version
                )
            if actual != prior and not matches_target:
                raise PermissionError("watch selection metadata is outside captured recovery custody")
        return document, target, selected

    @contextmanager
    def _metadata_locks(self):
        installation = self.installation
        signature = installation.evidence()
        self.journal._guard()
        genesis = api._DirectoryLock(
            installation.durable_home / "marketplaces" / ".locks" / f"{installation.marketplace_id}.genesis",
            kind="genesis", marketplace_id=installation.marketplace_id,
            timeout_seconds=api.RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS,
        )
        install = api._DirectoryLock(
            installation.plugin_root.parent.parent / ".locks" / f"{PLUGIN_ID}.install.lock",
            kind="install", marketplace_id=installation.marketplace_id, plugin_id=PLUGIN_ID,
            timeout_seconds=api.RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS,
        )
        with genesis, install:
            self.journal._current()
            if installation.evidence() != signature:
                raise PermissionError("watch installation changed while acquiring selection locks")
            yield
            self.journal._current()

    def publish(self) -> None:
        document, target, selected = self._read()
        if document["phase"] not in {"prepared", "publishing", "committed"} or (
            document["phase"] == "prepared" and document["predecessor"] is not None
        ):
            raise PermissionError("watch runtime selection is not authorized in this phase")
        current = (
            None if selected["current"] is None
            else bytes.fromhex(selected["current"]).decode("utf-8").strip()
        )
        result = api.cutover_runtime_slot(
            context=self.installation.receipt,
            expected_marketplace_id=self.installation.marketplace_id,
            expected_plugin_id=PLUGIN_ID,
            expected_payload_root=target.payload_root,
            expected_payload_version=target.payload_version,
            snapshot_id=target.snapshot_id, runtime_version=target.runtime_version,
            expected_namespace_generation=self.installation.namespace_generation,
            expected_install_generation=self.installation.install_generation,
            expected_current_version=current, expect_current_absent=current is None,
            durable_home=self.installation.durable_home,
            environment=self.journal.environment,
        )
        if (
            result.get("status") != "ready"
            or result.get("currentVersion") != target.runtime_version
            or result.get("lastKnownGoodVersion") != target.runtime_version
        ):
            raise PermissionError("watch canonical runtime selection requires revalidation")
        with self._metadata_locks():
            confirmed, _, selected = self._read()
            if confirmed != document:
                raise PermissionError("watch transaction changed before manifest publication")
            _replace_metadata(
                self.installation.plugin_root / "deploy-manifest.json",
                bytes.fromhex(document["targetManifest"]),
                expected=None if selected["manifest"] is None else bytes.fromhex(selected["manifest"]),
            )
        self._read()

    def rollback(self) -> None:
        document, _, _ = self._read()
        if document["phase"] != "rolling-back" or (
            document["launchInstanceId"] is not None and document["successor"] is None
            or document["successor"] is not None and "successor" not in document["exited"]
        ):
            raise PermissionError("watch selection rollback requires exact successor exit")
        paths = {
            "current": self.installation.plugin_root / api.CURRENT_VERSION_FILE,
            "lastKnownGood": self.installation.plugin_root / api.LAST_KNOWN_GOOD_FILE,
            "manifest": self.installation.plugin_root / "deploy-manifest.json",
        }
        with self._metadata_locks():
            for key, path in paths.items():
                current_document, _, selected = self._read()
                if current_document != document:
                    raise PermissionError("watch transaction changed during selection rollback")
                prior = document["prior"][key]
                current = selected[key]
                _replace_metadata(
                    path, None if prior is None else bytes.fromhex(prior),
                    expected=None if current is None else bytes.fromhex(current),
                )
        self._read()
