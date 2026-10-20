"""Two-phase selection withdrawal keeps integrity outside root-wide bookkeeping."""

from __future__ import annotations

import fnmatch
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .managed_runtime import (
    ManagedRuntimeError, _assert_safe_descendant, _ensure_safe_root, _integrity_budget,
    _reject_link, _RootLock,
)


@dataclass(frozen=True)
class WithdrawalSnapshot:
    root: Path
    path: Path
    present: bool
    record: dict | None


class SelectionWithdrawal:
    """Internal retention mixin; published pins protect cells during validation."""

    @staticmethod
    def _lock_budget(deadline: float | None) -> dict:
        return {} if deadline is None else {"timeout": max(0.0, deadline - time.monotonic())}

    def _withdrawal_snapshot(self, registration_id: str, receipt_dir: Path, deadline: float | None):
        root = _ensure_safe_root(self.root)
        scope = self._scope(receipt_dir, registration_id)
        path = self._path("selections", scope)
        with _RootLock(root, **self._lock_budget(deadline)):
            present = path.exists() or path.is_symlink()
            record = None
            if present:
                record, _ = self._check_record(path, "selections", check_references=False)
                if record["scope"] != scope:
                    raise ManagedRuntimeError("cannot withdraw a foreign managed selection")
        if present:
            verified, _ = self._check_record(path, "selections", deadline=deadline)
            if verified != record:
                raise ManagedRuntimeError("managed selection changed during validation")
        return WithdrawalSnapshot(root, path, present, record)

    def validate_withdrawal(
        self, registration_id: str, receipt_dir: Path, *, deadline: float | None = None
    ) -> WithdrawalSnapshot | None:
        if not self.root.exists():
            _reject_link(self.root, description="managed retention root")
            return
        return self._withdrawal_snapshot(registration_id, receipt_dir, deadline)

    def selection_withdrawn(
        self, registration_id: str, receipt_dir: Path, *, deadline: float
    ) -> bool:
        """Observe every matching domain pin; never withdraw or repair one here."""
        _integrity_budget(deadline)
        if not self.root.exists():
            _reject_link(self.root, description="managed retention root")
            _integrity_budget(deadline)
            return True
        root = _ensure_safe_root(self.root)
        directory = root / ".retention" / "selections"
        receipt_identity = os.path.normcase(str(receipt_dir.expanduser().absolute()))
        with _RootLock(root, **self._lock_budget(deadline)):
            _assert_safe_descendant(root, directory, description="managed selection pin directory")
            try:
                entries = os.scandir(directory)
            except FileNotFoundError:
                _integrity_budget(deadline)
                return True
            with entries:
                for entry in entries:
                    _integrity_budget(deadline)
                    if not fnmatch.fnmatch(entry.name, "*.json"):
                        continue
                    record, _ = self._check_record(Path(entry.path), "selections", check_references=False)
                    scope = record["scope"]
                    if (
                        scope["registration_id"] == registration_id
                        and os.path.normcase(scope["receipt_dir"]) == receipt_identity
                    ):
                        return False
            _integrity_budget(deadline)
            return True

    @contextmanager
    def withdraw_selection(
        self, registration_id: str, receipt_dir: Path, *, deadline: float | None = None,
        validated: WithdrawalSnapshot | None = None,
    ) -> Iterator[None]:
        """Revalidate exact metadata under the owner lock before changing either pin."""
        if not self.root.exists():
            _reject_link(self.root, description="managed retention root")
            yield
            return
        snapshot = validated or self._withdrawal_snapshot(registration_id, receipt_dir, deadline)
        root, path, present, record = snapshot.root, snapshot.path, snapshot.present, snapshot.record
        if root != _ensure_safe_root(self.root) or path != self._path(
            "selections", self._scope(receipt_dir, registration_id),
        ):
            raise ManagedRuntimeError("validated withdrawal belongs to a different selection")
        with _RootLock(root, **self._lock_budget(deadline)):
            if (path.exists() or path.is_symlink()) != present:
                raise ManagedRuntimeError("managed selection changed during validation")
            if present:
                current, _ = self._check_record(path, "selections", check_references=False)
                if current != record:
                    raise ManagedRuntimeError("managed selection changed during validation")
            yield
            if present:
                path.unlink()
