"""Current footprint semantics and explicit compatibility without child actors."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    source = ROOT / "libs/installer-engine/installer_engine_footprint.py"
    spec = importlib.util.spec_from_file_location("promotion_footprint_contract", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def record() -> dict[str, Any]:
    return {
        "version": 3, "schema": "copilot-extensions.installed-footprint",
        "plugin": "example", "revision": 2, "change": None,
        "contract": {
            "profile": {
                "version": 1, "module": "example", "root_entries": ["versions", "venv"],
                "bin_entries": [], "script_entries": [], "local_bin_entries": [],
            },
            "effects": {
                "control": {
                    "kind": "runtime-state", "scope": "runtime-root",
                    "entries": ["control.socket"], "disposition": "reset",
                },
                "data": {
                    "kind": "preserved-state", "scope": "runtime-root",
                    "entries": ["unknown-contents"], "disposition": "preserve",
                },
            },
            "recognizers": {
                "supported-legacy": {
                    "kind": "versioned-runtime-v1", "footprint": [1, 1],
                    "deployment_schema": [3, 3], "completion_contract": [1, 1],
                },
            },
        },
        "producers": {"scripts": "a" * 64, "src": "b" * 64},
    }


def test_source_refresh_is_not_a_footprint_revision(provider: ModuleType) -> None:
    old = record()
    size = len(json.dumps(old, sort_keys=True))
    for number in range(100):
        current = deepcopy(old)
        current["producers"]["scripts"] = f"{number:064x}"
        provider.require_transition(old, current, "example")
        assert current["revision"] == 2 and current["change"] is None
        assert len(json.dumps(current, sort_keys=True)) == size
        old = current


def test_supported_v2_adapter_exports_only_the_effective_contract(provider: ModuleType) -> None:
    current = record()
    first = {**current["contract"], "producers": {"scripts": "0" * 64, "src": "b" * 64}}
    last = {**current["contract"], "producers": current["producers"]}
    old = {
        "version": 2, "schema": current["schema"], "plugin": "example",
        "history": [
            {"revision": 1, "snapshot": first, "delta": provider.delta(None, first)},
            {"revision": 2, "snapshot": last, "delta": provider.delta(first, last)},
        ],
    }
    normalized = provider.parse(old, "example")
    assert normalized.profile == current["contract"]["profile"]
    assert normalized.effects == current["contract"]["effects"]
    assert normalized.recognizers == current["contract"]["recognizers"]
    assert not hasattr(normalized, "history")
    provider.require_transition(old, current, "example")


@pytest.mark.parametrize("change", ["artifact", "effect-entry", "legacy-rule", "producer-scope"])
def test_semantic_changes_require_revision_assessment_and_dispositions(
    provider: ModuleType, change: str,
) -> None:
    old, current = record(), record()
    if change == "artifact":
        current["contract"]["profile"]["root_entries"].remove("venv")
    elif change == "effect-entry":
        current["contract"]["effects"]["control"]["entries"].clear()
    elif change == "legacy-rule":
        current["contract"]["recognizers"].clear()
    else:
        current["producers"]["new-scope"] = "0" * 64
    with pytest.raises(provider.FootprintError, match="next revision"):
        provider.require_transition(old, current, "example")
    assessment = provider.contract_delta(provider.parse(old, "example"), provider.parse(current, "example"))
    current["revision"] = 3
    current["change"] = {
        "from_revision": 2, "delta": assessment,
        "dispositions": {
            identity: {"action": "preserve-unknown", "reason": "Retired support preserves remnants."}
            for identity in assessment["retired"]
        },
    }
    provider.require_transition(old, current, "example")
    assert current["contract"]["effects"]["data"]["disposition"] == "preserve"
    if assessment["retired"]:
        current["change"]["dispositions"].clear()
        with pytest.raises(provider.FootprintError, match="explicit disposition"):
            provider.parse(current, "example")


def test_unknown_required_incoming_semantics_are_not_ignored(provider: ModuleType) -> None:
    current = record()
    current["contract"]["required_policy"] = "not-supported"
    with pytest.raises(provider.FootprintError, match="Expected footprint fields"):
        provider.parse(current, "example")
    with pytest.raises(provider.FootprintError, match="Duplicate footprint field"):
        provider.decode('{"version":3,"version":2}')


def test_incoming_revision_is_not_an_incumbent_layout_version(provider: ModuleType) -> None:
    manifest = provider.parse(record(), "example")
    recognized = provider.recognize(manifest, Path("/not-read"), footprint_revision=True)
    assert not recognized.matched and recognized.reason == "footprint-unknown"
    assert manifest.recognizers["supported-legacy"]["footprint"] == [1, 1]
    assert manifest.revision == 2
