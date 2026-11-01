"""Ready-only assembly delegates slot judgment to the existing runtime owner."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE))
from configuration_context import ready_owner_prefix  # noqa: E402
from configuration_layers import ConfigurationError  # noqa: E402


def test_absent_runtime_uses_actual_read_only_resolver_without_creating_it(tmp_path):
    path = ROOT / "libs" / "versioned-runtime" / "versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("configuration_runtime_owner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    missing = tmp_path / "absent-owner"
    assert ready_owner_prefix(missing, "agent_worktrees", resolver=module.resolve_python) == []
    assert not missing.exists()


def test_ready_prefix_retains_qualified_interpreter_and_module(tmp_path):
    selected = tmp_path / "versions" / "complete" / "python"
    roots = []

    def resolve(root):
        roots.append(root)
        return selected

    assert ready_owner_prefix(tmp_path, "agent_worktrees", resolver=resolve) == [
        str(selected), "-m", "agent_worktrees",
    ]
    assert roots == [tmp_path]
    assert not selected.exists()


def test_readiness_failure_never_becomes_optional_absence(tmp_path):
    def refuse(root):
        raise ValueError("unconfirmed selection")

    with pytest.raises(ConfigurationError, match="readiness read failed"):
        ready_owner_prefix(tmp_path, "agent_worktrees", resolver=refuse)
    with pytest.raises(ConfigurationError, match="relative interpreter"):
        ready_owner_prefix(tmp_path, "agent_worktrees", resolver=lambda root: Path("python"))


def test_standalone_payloads_ship_the_same_canonical_reader():
    spec = importlib.util.spec_from_file_location(
        "configuration_payload_sync", ROOT / "tools" / "sync-configuration-layers.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for canonical, delivered in module.vendor_pairs():
        assert delivered.read_bytes() == canonical.read_bytes()
