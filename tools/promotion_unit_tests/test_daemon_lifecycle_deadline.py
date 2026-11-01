"""Admit the exact actor-free shared lifecycle deadline controls."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "promotion_original_daemon_lifecycle_deadline",
    ROOT / "libs/agent-procutil/tests/test_daemon_lifecycle_deadline.py",
)
original = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = original
spec.loader.exec_module(original)


@pytest.fixture(autouse=True)
def actor_free_registry_owner(monkeypatch, tmp_path):
    def owned(path):
        assert Path(path).resolve().is_relative_to(tmp_path.resolve())

    monkeypatch.setattr(original.store, "assert_path_owner", owned)

for name in (
    "test_expired_lifecycle_lock_never_creates_or_deletes_inventory_files",
    "test_local_lock_acquisition_consumes_only_remaining_original_time",
    "test_cleanup_overrun_retains_the_record_and_reports_held",
    "test_lifecycle_exposes_held_cleanup_without_retrying_it",
    "test_expired_owner_cannot_renew_a_qualification_lease",
    "test_monitor_settlement_preserves_other_lifecycle_custody",
    "test_monitor_wait_has_no_new_relative_grace",
):
    globals()[name] = getattr(original, name)
