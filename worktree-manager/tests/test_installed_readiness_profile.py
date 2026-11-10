"""Pure controls for the installed lifecycle witness's profile inputs."""
from __future__ import annotations

from copy import deepcopy
import runpy
from pathlib import Path

import pytest

_INPUTS = runpy.run_path(
    str(Path(__file__).parent / "native" / "manager_installed_readiness_profile.py")
)


def _profile(*, version="1.0", manager_version="1.0", entries=None, manifests=None):
    entries = entries or [
        {"name": name, "version": version, "source": "plugins/" + name}
        for name in ("agent-worktrees", "agent-machines", "example-guidance")
    ]
    manifests = manifests if manifests is not None else {
        row["name"]: {
            "name": row["name"], "version": row["version"],
            "runtimeScope": (
                "none" if row["name"] == "example-guidance"
                else "universal" if row["name"] == "agent-worktrees" else "machine-gated"
            ),
        } for row in entries
    }
    return _INPUTS["required_readiness_profile"](
        {"name": "example-marketplace", "plugins": entries}, manifests,
        marketplace_root="fixture/payloads",
        manager_version=manager_version,
        repository_config={
            "project": "example", "stateless": True, "require_application_readiness": False,
            "custom": {"retained": True},
        },
    )


def _identity(digit):
    return {key: digit * 40 for key in ("source_commit", "candidate_commit", "candidate_tree")}


def _upgrade(before=None, after=None, before_identity=None, after_identity=None):
    return _INPUTS["ordinary_upgrade_inputs"](
        before_identity or _identity("a"), before if before is not None else _profile(),
        after_identity or _identity("b"), after if after is not None else _profile(version="1.1"),
    )


def test_complete_profile_includes_payload_only_plugins_and_preserves_other_policy():
    profile = _profile()
    assert profile["settings"]["enabledPlugins"] == {
        "agent-worktrees@example-marketplace": True,
        "agent-machines@example-marketplace": True,
        "example-guidance@example-marketplace": True,
    }
    assert profile["repository_config"] == {
        "project": "example", "stateless": True, "require_application_readiness": True,
        "custom": {"retained": True},
    }
    assert profile["settings"]["extraKnownMarketplaces"] == {
        "example-marketplace": {"source": {"source": "directory", "path": "fixture/payloads"}},
    }


@pytest.mark.parametrize("defect", ["missing", "version", "duplicate", "source", "scope"])
def test_profile_refuses_incomplete_or_substituted_inventory(defect):
    rows = [
        {"name": name, "version": "1.0", "source": "plugins/" + name}
        for name in ("agent-worktrees", "agent-machines")
    ]
    manifests = {
        row["name"]: {**row, "runtimeScope": "machine-gated"} for row in rows
    }
    if defect == "missing":
        del manifests["agent-machines"]
    elif defect == "version":
        manifests["agent-machines"]["version"] = "0.9"
    elif defect == "duplicate":
        rows.append(dict(rows[-1]))
    elif defect == "source":
        rows[-1]["source"] = "../substitute"
    else:
        manifests["agent-machines"]["runtimeScope"] = "none"
    with pytest.raises(ValueError):
        _profile(entries=rows, manifests=manifests)


def test_upgrade_retains_both_complete_profiles_and_original_source_identities():
    before, after = _profile(), _profile(version="1.1")
    inputs = _upgrade(before, after)
    assert inputs["changed_runtimes"] == ["agent-machines", "agent-worktrees"]
    assert inputs["baseline_identity"] == _identity("a")
    assert inputs["candidate_identity"] == _identity("b")
    inputs["candidate_profile"]["repository_config"]["custom"]["retained"] = False
    assert after["repository_config"]["custom"]["retained"] is True
    assert before["inventory"]["agent-machines"]["version"] == "1.0"


def test_manager_only_version_change_is_a_real_upgrade_input():
    inputs = _upgrade(_profile(), _profile(manager_version="1.1"))
    assert inputs["changed_runtimes"] == ["worktree-manager"]


@pytest.mark.parametrize("defect", [
    "same-source", "same-runtimes", "identity", "readiness-disabled",
    "inventory-reduced", "inventory-nonboolean", "machines-missing",
])
def test_upgrade_refuses_noop_or_weakened_profile(defect):
    before, after = _profile(), _profile(version="1.1")
    identity = _identity("b")
    if defect == "same-source":
        identity["source_commit"] = "a" * 40
    elif defect == "same-runtimes":
        after = deepcopy(before)
    elif defect == "identity":
        del identity["candidate_tree"]
    elif defect == "readiness-disabled":
        after["repository_config"]["require_application_readiness"] = False
    elif defect == "inventory-reduced":
        del after["settings"]["enabledPlugins"]["example-guidance@example-marketplace"]
    elif defect == "inventory-nonboolean":
        after["settings"]["enabledPlugins"]["agent-machines@example-marketplace"] = 1
    else:
        del after["inventory"]["agent-machines"]
        del after["settings"]["enabledPlugins"]["agent-machines@example-marketplace"]
    with pytest.raises(ValueError):
        _upgrade(before, after, after_identity=identity)
