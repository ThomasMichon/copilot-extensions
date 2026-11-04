"""Actor-free decisions over synthetic launch source and version observations."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "cohort_launch_freshness"
package = ModuleType(PACKAGE)
package.__path__ = []
probe = ModuleType(f"{PACKAGE}.installation_probe")
probe.strict_json_object = dict
sys.modules[PACKAGE] = package
sys.modules[probe.__name__] = probe
spec = importlib.util.spec_from_file_location(
    f"{PACKAGE}.freshness",
    ROOT / "plugins/agent-worktrees/src/agent_worktrees/reconcile_freshness.py",
)
assert spec and spec.loader
freshness = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = freshness
spec.loader.exec_module(freshness)


@pytest.fixture
def observation(monkeypatch):
    payload = Path(
        "C:\\synthetic\\payload" if sys.platform == "win32" else "/synthetic/payload"
    )
    root = payload.parent / "runtime"
    values = {
        "plugin.json": {"version": "2.0.0"},
        "deploy-manifest.json": {"source": {"version": "1.0.0"}},
        "runtime-source.json": None,
    }
    state = SimpleNamespace(
        payload=payload, root=root, values=values, running={}, selected=None
    )
    package.reconcile = SimpleNamespace(
        _runtime_running_record=lambda *args: copy.deepcopy(state.running),
        _same_path=lambda left, right: left == right,
    )
    package.update_stage = SimpleNamespace(fingerprint=lambda path: "unchanged-content")
    monkeypatch.setattr(
        freshness, "_document", lambda path: copy.deepcopy(values[path.name])
    )
    monkeypatch.setattr(freshness, "_read", lambda path: state.selected)
    return state


@pytest.mark.parametrize(
    ("installed", "serving", "local", "reason"),
    [
        ("3.0.0", "1.0.0", False, "runtime-payload-older"),
        ("1.0.0", "3.0.0", False, "runtime-payload-older"),
        ("2.0.0", None, True, "runtime-source-selection-required"),
        ("1.0.0", None, False, None),
        (None, None, False, None),
    ],
)
def test_source_and_both_versions_govern_automatic_admission(
    observation,
    installed,
    serving,
    local,
    reason,
):
    source = observation.values["deploy-manifest.json"]["source"]
    source["version"] = installed
    if installed is None:
        observation.values["deploy-manifest.json"] = None
    if local:
        source.update(
            kind="local", path=str(observation.payload.parent / "other-source")
        )
    if serving:
        observation.running = {"version": serving}
    _snapshot, diagnostic = freshness.check_runtime_freshness(
        "example",
        observation.payload,
        observation.root,
    )
    assert (diagnostic["reason"] if diagnostic else None) == reason


@pytest.mark.parametrize("change", ["source", "selector"])
def test_cached_decision_requires_current_observations(observation, change):
    snapshot, diagnostic = freshness.check_runtime_freshness(
        "example",
        observation.payload,
        observation.root,
    )
    assert diagnostic is None
    if change == "source":
        observation.values["deploy-manifest.json"]["source"].update(
            kind="local",
            path=str(observation.payload.parent / "another-source"),
        )
    else:
        observation.selected = b"3.0.0"
    _snapshot, diagnostic = freshness.check_runtime_freshness(
        "example",
        observation.payload,
        observation.root,
        expected=snapshot,
    )
    assert diagnostic["reason"] == "runtime-freshness-changed"


@pytest.mark.parametrize(
    "unavailable", ["payload-version", "selected-manifest", "source-kind"]
)
def test_missing_evidence_is_not_standalone_permission(observation, unavailable):
    if unavailable == "payload-version":
        observation.values["plugin.json"] = {}
    elif unavailable == "selected-manifest":
        observation.values["deploy-manifest.json"] = None
        observation.selected = b"1.0.0"
    else:
        observation.values["deploy-manifest.json"]["source"]["kind"] = None
    _snapshot, diagnostic = freshness.check_runtime_freshness(
        "example",
        observation.payload,
        observation.root,
    )
    assert diagnostic["reason"] == "runtime-freshness-unavailable"
