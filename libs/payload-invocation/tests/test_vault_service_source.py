"""Run Vault's service-ingress controls without installing its service."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, cast

_tests = Path(__file__).resolve().parents[3] / "plugins/agent-vault/tests"
_spec = importlib.util.spec_from_file_location(
    "_vault_service_source", _tests / "test_managed_service_launcher_sh.py",
)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
_original = cast(Any, _module)
pytestmark = _original.pytestmark
ingress = _original.ingress
test_service_keeps_pid_streams_arguments_and_context = (
    _original.test_service_keeps_pid_streams_arguments_and_context
)
test_raw_checkout_bootstrap_keeps_its_companion_declaration = (
    _original.test_raw_checkout_bootstrap_keeps_its_companion_declaration
)
test_service_refuses_unprepared_support_before_worker = (
    _original.test_service_refuses_unprepared_support_before_worker
)
test_service_real_uv_keeps_offline_managed_runtime = (
    _original.test_service_real_uv_keeps_offline_managed_runtime
)
test_service_selector_caller_loss_and_deadline_retire_owned_tree = (
    _original.test_service_selector_caller_loss_and_deadline_retire_owned_tree
)

_publication_spec = importlib.util.spec_from_file_location(
    "_vault_service_publication", _tests / "test_install_service_failures.py",
)
assert _publication_spec and _publication_spec.loader
_publication_module = importlib.util.module_from_spec(_publication_spec)
_publication_spec.loader.exec_module(_publication_module)
test_service_support_publication_preflights_and_commits_entrypoint_last = cast(
    Any, _publication_module,
).test_service_support_publication_preflights_and_commits_entrypoint_last
