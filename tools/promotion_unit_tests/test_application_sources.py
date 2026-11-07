"""Actor-free controls for explicit associated application inputs."""
from __future__ import annotations

import ast
from pathlib import Path
import sys
import time

import pytest
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "libs" / "installer-engine"))

from application_sources import application_sources
from installer_wheel_inputs import _check_deadline
from runtime_profile import ProfileError, _requirement


@pytest.fixture(scope="module")
def registry_roots():
    source = ROOT / "tools" / "local_install_profile.py"
    tree = ast.parse(source.read_bytes(), filename=str(source))
    selected = [
        node for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
        or isinstance(node, ast.FunctionDef) and node.name == "registry_roots"
    ]
    assert len(selected) == 2
    namespace = {
        "__name__": "unit_application_sources", "_check_deadline": _check_deadline,
        "_requirement": _requirement, "canonicalize_name": canonicalize_name,
        "ProfileError": ProfileError,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return namespace["registry_roots"]


@pytest.mark.parametrize(("selected", "owner"), [
    (False, None),
    (True, "standalone:fleet-manager"),
    (True, None),
    (True, "different"),
], ids=["default", "selected", "missing", "different"])
def test_declared_application_is_required_only_when_selected(registry_roots, selected, owner):
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


def test_undeclared_application_name_is_not_a_source_exemption(registry_roots):
    assert registry_roots({
        "consumer": {
            "version": "1", "provides_extras": [], "requires_dist": ["sdk>=1"],
            "application_sources": application_sources({}),
        },
    }, "consumer==1", {}, deadline=time.monotonic() + 5) == ["sdk>=1"]
