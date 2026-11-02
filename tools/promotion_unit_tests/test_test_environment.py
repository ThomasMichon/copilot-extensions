"""Pure controls for the shared environment adapter and compatibility surface."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools import plugin_test_containment as containment
from tools import plugin_test_environment as environment


@pytest.mark.parametrize("allow_explicit_tiers", [False, True])
def test_isolation_reexports_preserve_host_detachment(tmp_path, allow_explicit_tiers):
    assert containment.isolated_environment is environment.isolated_environment
    assert containment.ALWAYS_SCRUB_NAMES is environment.ALWAYS_SCRUB_NAMES
    base = {
        "HOME": "foreign-home", "GH_TOKEN": "synthetic-token",
        "GITHUB_TOKEN": "synthetic-token", "COPILOT_AGENT_SESSION_ID": "foreign-session",
        "AGENT_DISPATCH_DB": "foreign-db", "KEEP": "preserved",
        containment.ALLOW_HOST_STATE_ENV: "1",
        "COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS": "1",
    }
    original = dict(base)
    actual = containment.isolated_environment(
        base, tmp_path, allow_explicit_tiers=allow_explicit_tiers,
    )
    assert base == original
    assert actual["KEEP"] == "preserved"
    assert actual["HOME"] == str(tmp_path.resolve() / "home")
    assert actual["AGENT_DISPATCH_DB"] == str(
        tmp_path.resolve() / "agent-dispatch/state/dispatch.db",
    )
    assert not {"GH_TOKEN", "GITHUB_TOKEN", "COPILOT_AGENT_SESSION_ID"} & actual.keys()
    assert containment.ALLOW_HOST_STATE_ENV not in actual
    assert ("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS" in actual) == allow_explicit_tiers
    assert actual[containment.CONTAINED_ENV] == "1"
    assert actual[containment.SANDBOX_ENV] == str(tmp_path.resolve())


def test_explicit_host_state_still_contains_temporary_storage(tmp_path):
    actual = containment.isolated_environment(
        {"HOME": "preserved-home", "GH_TOKEN": "synthetic-token", "AGENT_RT_ROOT": "foreign"},
        tmp_path, allow_explicit_tiers=True, allow_host_state=True,
    )
    assert actual["HOME"] == "preserved-home"
    assert actual["GH_TOKEN"] == "synthetic-token"
    assert "AGENT_RT_ROOT" not in actual
    assert actual[containment.ALLOW_HOST_STATE_ENV] == "1"
    assert all(
        actual[name] == str(tmp_path.resolve() / relative)
        for name, relative in (
            ("TEMP", "tmp"), ("TMP", "tmp"), ("TMPDIR", "tmp"), ("XDG_RUNTIME_DIR", "run"),
        )
    )


def test_host_state_requires_explicit_tier_permission(tmp_path):
    with pytest.raises(ValueError, match="requires allow_explicit_tiers"):
        containment.isolated_environment({}, tmp_path, allow_host_state=True)
    assert not list(tmp_path.iterdir())


def test_registry_comparison_preserves_casefold_names_values_and_types():
    expected = {"Path": ("old", 1), "UNCHANGED": ("same", 2), "REMOVED": ("gone", 1)}
    current = {"PATH": ("new", 1), "unchanged": ("same", 2), "ADDED": ("present", 1)}
    assert environment.registry_environment_changes("User", expected, current) == [
        "User:ADDED", "User:PATH", "User:REMOVED",
    ]
    assert environment.registry_environment_changes(
        "Machine", {"Value": ("same", 1)}, {"value": ("same", 2)},
    ) == ["Machine:value"]


@pytest.mark.parametrize("available", [False, True])
def test_registry_wrapper_keeps_reader_patch_and_error_identity(monkeypatch, available):
    snapshot = SimpleNamespace(user={"Path": ("old", 1)}, machine={"Stable": ("same", 1)})
    current = SimpleNamespace(user={"PATH": ("new", 1)}, machine={"stable": ("same", 1)})
    seen = []

    def read():
        seen.append("read")
        return current if available else None

    monkeypatch.setattr(containment, "_read_registry_environment", read)
    assert containment._registry_environment_drift(None) == []
    assert seen == []
    if available:
        assert containment._registry_environment_drift(snapshot) == ["User:PATH"]
    else:
        with pytest.raises(containment.ContainmentError, match="became unavailable"):
            containment._registry_environment_drift(snapshot)
    assert seen == ["read"]
