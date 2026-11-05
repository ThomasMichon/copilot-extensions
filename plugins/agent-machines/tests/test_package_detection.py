"""Compatibility uses the requested package's complete version and known absence."""

from __future__ import annotations

import pytest

from agent_machines import resources
from agent_machines.resources import ResourceContext, RunOutcome, apply_resources

from .test_package_compatibility import package


@pytest.mark.parametrize("manager, text", [
    ("pipx", "other 99.0.0\nexample-tool 3.3.6\n"),
    ("uv-tool", "other v99.0.0\n- other\nexample-tool v3.3.6\n- example-tool\n"),
    ("pip", "Name: example_tool\nSummary: release 99.0.0\nVersion: 3.3.6\n"),
])
def test_detection_uses_exact_package_identity_not_the_first_numeric_text(manager, text, tmp_path):
    observed = resources.HANDLERS["package"]._detect(
        ResourceContext(tmp_path, {}, "windows", lambda argv: RunOutcome(0, text)),
        resources.MANAGERS[manager], "example-tool",
    )
    assert observed == {"known": True, "present": True, "version": "3.3.6"}


@pytest.mark.parametrize("manager, text", [
    ("pipx", "example-tool 3.4.0rc1\n"),
    ("uv-tool", "example-tool v3.4.0rc1\n- example-tool\n"),
    ("pip", "Name: example-tool\nVersion: 3.4.0rc1\n"),
])
def test_version_suffix_is_preserved_and_blocks_numeric_compatibility(
    manager, text, tmp_path, monkeypatch,
):
    monkeypatch.setattr(resources.shutil, "which", lambda value: value)
    calls = []

    def runner(argv):
        calls.append(argv)
        return RunOutcome(0, text)

    result = apply_resources(
        [package(tmp_path, id="example-tool", manager=manager)], "box-1", "windows",
        ResourceContext(tmp_path, {}, "windows", runner), dry_run=False,
    )[0]
    assert result.status == "blocked" and len(calls) == 1


@pytest.mark.parametrize("manager", ["pipx", "uv-tool"])
def test_a_substring_package_name_is_not_presence(manager, tmp_path):
    observed = resources.HANDLERS["package"]._detect(
        ResourceContext(tmp_path, {}, "windows", lambda argv: RunOutcome(0, "example-tools 9.0.0")),
        resources.MANAGERS[manager], "example-tool",
    )
    assert observed["known"] and not observed["present"]


@pytest.mark.parametrize("manager, platform, absent", [
    ("apt", "linux", "dpkg-query: no packages found matching example-tool"),
    ("pip", "windows", "WARNING: Package(s) not found: example-tool"),
])
@pytest.mark.parametrize("confirmed", [True, False])
def test_confirmed_absence_installs_but_probe_failure_defers(
    tmp_path, monkeypatch, manager, platform, absent, confirmed,
):
    monkeypatch.setattr(resources.shutil, "which", lambda value: value)
    installed = False
    mutations = []

    def runner(argv):
        nonlocal installed
        if argv == resources.MANAGERS[manager]["detect"]("example-tool", None):
            if not installed:
                return RunOutcome(1, "", absent if confirmed else "permission denied")
            return RunOutcome(
                0, "3.4.0" if manager == "apt" else "Name: example-tool\nVersion: 3.4.0\n",
            )
        assert argv == resources.MANAGERS[manager]["install"]("example-tool", None)
        installed = True
        mutations.append(argv)
        return RunOutcome(0)

    result = apply_resources(
        [package(tmp_path, id="example-tool", manager=manager)], "box-1", platform,
        ResourceContext(tmp_path, {}, platform, runner), dry_run=False,
    )[0]
    assert result.status == ("changed" if confirmed else "deferred")
    assert installed is confirmed and len(mutations) == int(confirmed)
