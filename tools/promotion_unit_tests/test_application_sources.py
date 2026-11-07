"""Actor-free controls for explicit associated application inputs."""
from __future__ import annotations

import time

import pytest

from application_sources import application_sources
from local_install_profile import ProfileError, registry_roots


@pytest.mark.parametrize(("selected", "owner"), [
    (False, None),
    (True, "standalone:fleet-manager"),
    (True, None),
    (True, "different"),
], ids=["default", "selected", "missing", "different"])
def test_declared_application_is_required_only_when_selected(selected, owner):
    sources = application_sources({"tool": {"copilot-extensions": {
        "application-sources": {"sdk": "standalone:fleet-manager"},
    }}})
    wheels = {"consumer": {
        "version": "1", "provides_extras": ["feature"],
        "requires_dist": ['sdk>=1; extra == "feature"'],
        "application_sources": sources,
    }}
    if owner is not None:
        wheels["sdk"] = {
            "version": "1", "provides_extras": [],
            "requires_dist": ["external>=1"], "source_project": owner,
        }
    root = "consumer[feature]==1" if selected else "consumer==1"
    if selected and owner != "standalone:fleet-manager":
        with pytest.raises(ProfileError, match="selected associated application"):
            registry_roots(wheels, root, {}, deadline=time.monotonic() + 5)
    else:
        assert registry_roots(
            wheels, root, {}, deadline=time.monotonic() + 5,
        ) == (["external>=1"] if selected else [])


@pytest.mark.parametrize("sources", [
    False,
    {"sdk": "../outside"},
    {"some-sdk": "plugin", "SOME_SDK": "plugin"},
], ids=["table", "selector", "collision"])
def test_application_source_declaration_refuses_invalid_or_ambiguous_values(sources):
    with pytest.raises(ValueError):
        application_sources({"tool": {"copilot-extensions": {
            "application-sources": sources,
        }}})


def test_undeclared_application_name_is_not_a_source_exemption():
    assert registry_roots({
        "consumer": {
            "version": "1", "provides_extras": [], "requires_dist": ["sdk>=1"],
            "application_sources": application_sources({}),
        },
    }, "consumer==1", {}, deadline=time.monotonic() + 5) == ["sdk>=1"]
