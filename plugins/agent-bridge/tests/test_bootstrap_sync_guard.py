"""Guard: the bootstrap-check family classification stays consistent (#167).

Enforces `tools/check-bootstrap-sync.py` in CI: every runtime plugin's
session-start hook is classified in exactly one deploy-model family, and
multi-member families stay byte-identical. Also verifies the checker actually
catches drift (so the guard can't silently rot into a no-op).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_TOOL = _REPO / "tools" / "check-bootstrap-sync.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_bootstrap_sync", _TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tool_exists():
    assert _TOOL.exists(), f"missing {_TOOL}"


def test_real_repo_is_consistent():
    mod = _load_tool()
    problems = mod.verify()
    assert problems == [], "bootstrap-check drift:\n" + "\n".join(problems)


def test_every_runtime_plugin_is_classified():
    mod = _load_tool()
    classified = {p for members in mod.FAMILIES.values() for p in members}
    discovered = set(mod._runtime_plugins())
    assert discovered <= classified, (
        "unclassified runtime plugins: " + ", ".join(sorted(discovered - classified))
    )


def test_checker_detects_drift(monkeypatch):
    """Two plugins with different hooks placed in one family must be flagged."""
    mod = _load_tool()
    # agent-bridge (reference variant) and agent-codespaces (common) differ.
    monkeypatch.setattr(
        mod, "FAMILIES", {"synthetic": ["agent-bridge", "agent-codespaces"]}
    )
    problems = mod.verify()
    assert any("drifted" in p for p in problems), problems


def test_checker_flags_unclassified(monkeypatch):
    monkeypatch.setattr(mod := _load_tool(), "FAMILIES", {"tiny": ["agent-bridge"]})
    problems = mod.verify()
    assert any("not classified" in p for p in problems), problems


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
