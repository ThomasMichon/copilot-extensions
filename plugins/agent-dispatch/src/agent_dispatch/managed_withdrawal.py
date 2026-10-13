"""Two-phase selection withdrawal keeps integrity outside root-wide bookkeeping."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .managed_runtime import ManagedRuntimeError, _ensure_safe_root, _reject_link, _RootLock


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
        return root, path, present, record

    def validate_withdrawal(
        self, registration_id: str, receipt_dir: Path, *, deadline: float | None = None
    ) -> None:
        if not self.root.exists():
            _reject_link(self.root, description="managed retention root")
            return
        self._withdrawal_snapshot(registration_id, receipt_dir, deadline)

    @contextmanager
    def withdraw_selection(
        self, registration_id: str, receipt_dir: Path, *, deadline: float | None = None
    ) -> Iterator[None]:
        """Revalidate exact metadata under the owner lock before changing either pin."""
        if not self.root.exists():
            _reject_link(self.root, description="managed retention root")
            yield
            return
        root, path, present, record = self._withdrawal_snapshot(registration_id, receipt_dir, deadline)
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
