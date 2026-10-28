"""Admit only the original actor-free cell installer controls."""

from __future__ import annotations

import importlib.util
import concurrent.futures
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_identity = _load(
    "promotion_cell_source_identity",
    ROOT / "libs/versioned-runtime/versioned_runtime_source.py",
)
_original = _load(
    "promotion_original_cell_installer",
    ROOT / "plugins/agent-machines/tests/test_cell_installer.py",
)


@pytest.fixture(autouse=True)
def canonical_source_identity(monkeypatch):
    monkeypatch.setitem(sys.modules, "versioned_runtime_source", _identity)
    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", InlinePool)


class InlinePool:
    """Keep canonical file framing and reads; substitute only thread execution."""

    def __init__(self, **_options):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_error):
        return False

    def submit(self, function, *args, **kwargs):
        result = concurrent.futures.Future()
        try:
            result.set_result(function(*args, **kwargs))
        except BaseException as error:
            result.set_exception(error)
        return result


for _name in (
    "helper", "payload", "owned_runner",
    "test_release_hash_binds_copied_content_and_membership",
    "test_release_hash_rejects_replaced_version_without_source_metadata",
    "test_release_hash_excludes_only_root_publication_owner",
    "test_release_hash_also_binds_source_identity_metadata",
    "test_release_hash_binds_generated_and_cached_bytes",
    "test_release_hash_rejects_linked_payload_inputs",
    "test_release_hash_propagates_incomplete_inventory",
    "test_worker_binds_original_caller_before_payload_identity",
    "test_snapshot_control_launches_disable_bytecode_writes",
    "test_watchdog_uses_whole_tree_owner_and_preserves_output",
    "test_watchdog_refuses_an_inherited_changed_payload",
    "test_watchdog_timeout_is_failure_after_owned_retirement",
    "test_watchdog_refuses_unbounded_deadlines",
):
    globals()[_name] = getattr(_original, _name)
