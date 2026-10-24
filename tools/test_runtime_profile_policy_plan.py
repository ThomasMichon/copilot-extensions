"""Real installation policy composed with retained release authentication."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import promotion_python_artifacts as artifacts
import runtime_profile_policy as planner
from runtime_profile import ProfileError, ProfileUnavailable
from test_runtime_profile_release import (
    MAIN,
    _verification,
    release as release,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installation-context" / "tests"))
from test_runtime_profile_policy import adopter as adopter, owned_policy
from test_installation_mode_governance import _policy, _write_json


def plan(adopter, release, **overrides):
    _, installation, _ = adopter
    directory, _, record = release
    arguments = dict(
        installation=installation, release_index=directory / artifacts.INDEX_NAME,
        repository="example/plugins", signer_digest=MAIN, source_digest=MAIN,
        source_ref="refs/heads/main", release=record["release"],
        artifact=record["artifact"], target=record["target"],
        component=record["component"], roots=record["roots"], max_subject_bytes=1 << 20,
    )
    return planner.plan_installation_profile(**(arguments | overrides))


def test_fresh_owner_baseline_selects_authenticated_release_without_approval_file(
    adopter, release, monkeypatch,
):
    calls = _verification(monkeypatch, release)
    _, arguments, _ = adopter
    before = sorted(arguments["os_profile"].rglob("*"))
    result = plan(adopter, release)
    assert result.release_selection.selected.profile.record() == release[2]
    assert result.source_trigger is None
    assert result.policy()["selection"] == {
        "preferArtifact": True, "allowSourceFallback": True, "backend": "cpu",
    }
    assert result.policy()["owner"]["runtimeRoot"] == str(arguments["legacy_root"])
    detached = result.policy()
    detached["selection"]["backend"] = "another"
    assert result.policy()["selection"]["backend"] == "cpu"
    assert sorted(arguments["os_profile"].rglob("*")) == before
    assert len(calls) == 1 and not calls[0].exists()


@pytest.mark.parametrize("missing,trigger", [
    ({"release_index": None}, "release-index-unavailable"),
    ({"artifact": None}, "artifact-unavailable"),
    ({"component": "another"}, "approved-profile-unavailable"),
    ({"preference": ("sha256:" + "9" * 64,)}, "approved-profile-unavailable"),
])
def test_only_availability_requests_independent_source_mode(
    adopter, release, monkeypatch, missing, trigger,
):
    calls = _verification(monkeypatch, release)
    result = plan(adopter, release, **missing)
    assert result.release_selection is None
    assert result.source_trigger == trigger
    assert result.policy()["policyId"].startswith("sha256:")
    assert len(calls) == (trigger != "release-index-unavailable")
    # A request contains no source approval, fabricated lock or artifact binding.
    assert not hasattr(result, "source_profile")


@pytest.mark.parametrize("missing", [
    {"release_index": None}, {"artifact": None},
    {"preference": ("sha256:" + "9" * 64,)},
])
def test_owned_policy_can_disable_source_fallback(adopter, release, monkeypatch, missing):
    module, arguments, _ = adopter
    owned_policy(module, arguments, allowSourceFallback=False)
    _verification(monkeypatch, release)
    with pytest.raises(ProfileUnavailable, match="source fallback is disabled"):
        plan(adopter, release, **missing)
    assert module.resolve_runtime_deployment(**arguments)["deployment"]["allowPluginPublication"] is False


@pytest.mark.parametrize("failure,error", [
    ("signature", artifacts.PromotionArtifactError),
    ("timeout", subprocess.TimeoutExpired),
    ("cancel", KeyboardInterrupt),
])
@pytest.mark.parametrize("missing_artifact", [False, True])
def test_failed_authentication_never_requests_source(
    adopter, release, monkeypatch, failure, error, missing_artifact,
):
    calls = _verification(monkeypatch, release, failure=failure)
    with pytest.raises(error):
        plan(adopter, release, **({"artifact": None} if missing_artifact else {}))
    assert len(calls) == 1 and not calls[0].exists()


def test_missing_supplied_subject_is_not_reclassified_as_discovery_absence(adopter, release):
    (release[0] / artifacts.INDEX_NAME).unlink()
    with pytest.raises(ProfileError):
        plan(adopter, release)


@pytest.mark.parametrize("preference", [
    ("not-a-lock",), ("sha256:" + "9" * 64,) * 2,
])
@pytest.mark.parametrize("missing", [{}, {"release_index": None}, {"artifact": None}])
def test_invalid_preference_is_not_an_availability_result(
    adopter, release, monkeypatch, preference, missing,
):
    _verification(monkeypatch, release)
    with pytest.raises(ProfileError) as raised:
        plan(adopter, release, preference=preference, **missing)
    assert not isinstance(raised.value, ProfileUnavailable)


@pytest.mark.parametrize("malformation", ["approval", "ambiguity", "schema"])
def test_invalid_or_ambiguous_approved_records_never_request_source(
    adopter, release, monkeypatch, malformation,
):
    directory, index, record = release
    if malformation == "approval":
        record["approval_source"] = "self-asserted"
    elif malformation == "schema":
        index["runtime_profiles"]["version"] = 2
    else:
        other = json.loads(json.dumps(record))
        other["acquisition_policy"] = "another-policy"
        index["runtime_profiles"]["profiles"].append(other)
    (directory / artifacts.INDEX_NAME).write_text(json.dumps(index), encoding="utf-8")
    _verification(monkeypatch, release)
    with pytest.raises(ProfileError) as raised:
        plan(adopter, release)
    assert not isinstance(raised.value, ProfileUnavailable)


@pytest.mark.parametrize("state", ["invalid", "foreign", "pending"])
def test_unresolved_policy_blocks_before_release_or_source_request(adopter, release, monkeypatch, state):
    module, arguments, _ = adopter
    path, policy = owned_policy(module, arguments)
    if state == "invalid":
        policy["runtimeProfiles"]["allowSourceFallback"] = "yes"
    elif state == "foreign":
        policy["owner"]["pluginId"] = "another"
    else:
        _write_json(
            arguments["os_profile"] / ".copilot-extensions" / "installation-mode.json",
            _policy(True, module.resolve_runtime_deployment(**arguments)["marketplaceId"]),
        )
        arguments["legacy_probe"] = {
            "declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z",
        }
    _write_json(path, policy)
    calls = _verification(monkeypatch, release)
    with pytest.raises(ProfileError, match="policy is blocked"):
        plan(adopter, release, release_index=None)
    assert not calls


def test_backend_is_resolved_once_by_existing_owner_and_never_retried(
    adopter, release, monkeypatch,
):
    module, arguments, _ = adopter
    owned_policy(module, arguments, backend="cuda12")
    calls = _verification(monkeypatch, release)
    requested = plan(adopter, release)
    assert requested.source_trigger == "approved-profile-unavailable"
    assert requested.policy()["selection"]["backend"] == "cuda12"
    with pytest.raises(ValueError, match="conflicts"):
        plan(adopter, release, backend="cpu")
    assert len(calls) == 1
    assert module.resolve_runtime_deployment(**arguments)["deployment"]["allowPluginPublication"] is False


def test_unprofiled_update_requires_migration_even_without_artifacts(adopter, release):
    with pytest.raises(ValueError, match="migration"):
        plan(adopter, release, release_index=None, existing_installation=True)
    request = plan(
        adopter, release, release_index=None, existing_installation=True,
        retained_backend="cpu",
    )
    assert request.policy()["selection"]["backend"] == "cpu"


def test_fresh_native_caller_can_request_source_without_artifact_infrastructure(adopter):
    _, installation, _ = adopter
    before = sorted(installation["os_profile"].rglob("*"))
    script = """
import json, sys
sys.path.insert(0, sys.argv[1])
from runtime_profile_policy import plan_installation_profile
result = plan_installation_profile(
    installation=json.load(sys.stdin), release_index=None,
    repository="example/plugins", signer_digest="", source_digest="", source_ref="",
    release={}, artifact=None, target={}, component="demo", roots=[],
    max_subject_bytes=1024,
)
assert result.release_selection is None
assert result.source_trigger == "release-index-unavailable"
assert result.policy()["selection"]["backend"] == "cpu"
assert result.policy()["owner"]["runtimeRoot"]
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(Path(__file__).resolve().parent)],
        input=json.dumps(installation, default=str), capture_output=True, text=True,
        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert sorted(installation["os_profile"].rglob("*")) == before
