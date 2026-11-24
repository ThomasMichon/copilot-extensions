"""Run native owner ingress controls without installing Codespaces dependencies."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, cast

_source = (
    Path(__file__).resolve().parents[3]
    / "plugins/agent-codespaces/tests/test_managed_owner_ingress_ps1.py"
)
_spec = importlib.util.spec_from_file_location("_codespaces_windows_owner_source", _source)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _module
_spec.loader.exec_module(_module)
_original = cast(Any, _module)
pytestmark = _original.pytestmark
windows_owner = _original.windows_owner
owner_shell = _original.owner_shell
test_windows_owner_keeps_managed_selection_streams_and_cutover = (
    _original.test_windows_owner_keeps_managed_selection_streams_and_cutover
)
test_windows_owner_missing_preparation_refuses_before_application = (
    _original.test_windows_owner_missing_preparation_refuses_before_application
)
test_windows_owner_publication_is_atomic_and_idempotent = (
    _original.test_windows_owner_publication_is_atomic_and_idempotent
)
test_windows_owner_status_and_selected_ensure_use_the_entry = (
    _original.test_windows_owner_status_and_selected_ensure_use_the_entry
)
test_owner_entry_preserves_scheduling_and_capability_holds = (
    _original.test_owner_entry_preserves_scheduling_and_capability_holds
)
test_owner_task_ownership_accepts_both_owned_entry_forms = (
    _original.test_owner_task_ownership_accepts_both_owned_entry_forms
)

_query_source = _source.with_name("test_connection_owner_task_idempotent.py")
_query_spec = importlib.util.spec_from_file_location("_codespaces_windows_owner_query", _query_source)
assert _query_spec and _query_spec.loader
_query_module = importlib.util.module_from_spec(_query_spec)
_query_spec.loader.exec_module(_query_module)
test_windows_owner_ensure_is_independent_of_existing_or_refused_task = cast(
    Any, _query_module,
).test_windows_owner_ensure_is_independent_of_existing_or_refused_task
