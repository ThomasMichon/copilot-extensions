"""Export original policy-refusal and CLI controls without changing witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_dependency_policy.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dependency_cli_refusals", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_probe_preserves_arguments_environment_and_cwd",
    "test_effective_remote_override_is_not_an_index_exemption",
    "test_flat_index_is_not_a_registry_timestamp_proof",
    "test_environment_resolver_closure_bypass_refused",
    "test_parserless_bootstrap_fails_explicitly_without_installing",
    "test_payload_metadata_alone_without_owner_provenance_grants_no_roots",
    "test_registry_only_cli_does_not_discover_product_roots",
    "test_default_cli_retains_product_attribution",
    "test_deployment_cli_does_not_duplicate_caller_immutable_options",
    "test_deployment_cli_refuses_live_or_shared_code_before_resolver",
    "test_registry_only_cannot_grant_explicit_source_roots",
    "test_registry_only_refuses_local_sources_before_uv",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original CLI refusal control: {_name}"
    globals()[_name] = getattr(_original, _name)
