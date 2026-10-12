import copy
import json
import os
import subprocess
import sys
import time

import pytest

from fleet_manager.planning import MAX_ITEMS, PlanError, digest
from fleet_manager.release import inspect_release, validate_release


def manifest():
    return {
        "schema": "fleet-manager.release", "version": 1, "id": "example-r1",
        "components": {
            "cli": {"kind": "client", "artifact_sha256": "a" * 64,
                    "api": {"min": 1, "max": 2}, "data_schemas": [1]},
            "core": {"kind": "control", "artifact_sha256": "b" * 64,
                     "api": {"min": 2, "max": 3}, "data_schemas": [1, 2]},
            "executor": {"kind": "executor", "artifact_sha256": "c" * 64,
                         "api": {"min": 2, "max": 2}, "data_schemas": [1]},
        },
        "roles": {"client": ["cli"], "gateway": ["cli", "core"], "node": ["executor"]},
    }


def observation(release, state="ready"):
    return {
        "schema": "fleet-manager.installation-observation", "version": 1,
        "authority": "test-authority", "installation": "test-installation",
        "release_digest": digest(release), "observed_at": 100, "expires_at": 200,
        "complete": True,
        "components": {
            name: {"artifact_sha256": component["artifact_sha256"],
                   "api": component["api"]["min"], "data_schema": component["data_schemas"][0],
                   "state": state}
            for name, component in release["components"].items()
        },
    }


def inspect(release, observed, role="gateway", now=150):
    return inspect_release(release, observed, role=role, authority="test-authority",
                           installation="test-installation", now=now)


def test_ready_is_explicit_snapshot_assertion_not_live_proof():
    release = manifest()
    observed = observation(release)
    before = copy.deepcopy((release, observed))
    result = inspect(release, observed)
    assert result["status"] == "ready"
    assert result["evidence"] == "supplied-snapshot-only"
    assert not result["live_readiness_verified"] and not result["mutations_performed"]
    assert (release, observed) == before
    assert result["inspected_at"] == 150
    assert result["observed_at"] == 100 and result["valid_until"] == 200


def test_evidence_window_survives_serialization_and_expiry():
    release = manifest()
    observed = observation(release)
    saved = json.loads(json.dumps(inspect(release, observed)))
    assert saved["inspected_at"] < saved["valid_until"]
    expired = inspect(release, observed, now=200)
    assert expired["status"] == "unknown"
    assert expired["observed_at"] == saved["observed_at"]
    assert expired["valid_until"] == saved["valid_until"]
    absent = inspect(release, None)
    assert absent["observed_at"] is None and absent["valid_until"] is None


@pytest.mark.parametrize("state", ["installed", "ready", "unknown"])
def test_observed_status_is_preserved(state):
    release = manifest()
    assert inspect(release, observation(release, state))["status"] == state


def test_missing_requires_complete_fresh_observation():
    release = manifest()
    observed = observation(release)
    del observed["components"]["core"]
    assert inspect(release, observed)["status"] == "missing"
    observed["complete"] = False
    assert inspect(release, observed)["status"] == "unknown"
    assert inspect(release, None)["status"] == "unknown"


@pytest.mark.parametrize("now,reason", [(99, "observation-from-future"),
                                      (200, "observation-expired"), (201, "observation-expired")])
def test_stale_and_future_observations_do_not_prove_readiness(now, reason):
    release = manifest()
    result = inspect(release, observation(release), now=now)
    assert result["status"] == "unknown"
    assert {item["reason"] for item in result["components"]} == {reason}


@pytest.mark.parametrize("field,value,reason", [
    ("artifact_sha256", "d" * 64, "artifact-mismatch"),
    ("api", 1, "api-mismatch"), ("api", 4, "api-mismatch"),
    ("data_schema", 3, "data-schema-mismatch"),
])
def test_component_compatibility_rejected(field, value, reason):
    release = manifest()
    observed = observation(release)
    observed["components"]["core"][field] = value
    result = inspect(release, observed)
    assert result["status"] == "incompatible"
    assert result["components"][1]["reason"] == reason


def test_release_digest_binds_content_not_only_display_identity():
    release = manifest()
    observed = observation(release)
    release["components"]["core"]["artifact_sha256"] = "d" * 64
    assert inspect(release, observed)["status"] == "incompatible"
    assert inspect(release, observed)["components"][0]["reason"] == "release-mismatch"


@pytest.mark.parametrize("change", [{"complete": False}, {"observed_at": 160},
                                  {"expires_at": 140}])
def test_release_mismatch_is_not_masked_by_observation_freshness(change):
    release = manifest()
    observed = observation(release)
    observed.update(change)
    observed["release_digest"] = "f" * 64
    result = inspect(release, observed)
    assert result["status"] == "incompatible"
    assert {row["reason"] for row in result["components"]} == {"release-mismatch"}


def test_invalid_release_digest_has_correct_diagnostic():
    release = manifest()
    observed = observation(release)
    observed["release_digest"] = "invalid-private"
    with pytest.raises(PlanError, match="release digest must be lowercase SHA-256") as error:
        inspect(release, observed)
    assert "invalid-private" not in str(error.value)


def test_field_diagnostic_names_expected_keys_without_echoing_extra_input():
    release = manifest()
    release["components"]["cli"]["private-key-name"] = "private-value"
    with pytest.raises(PlanError, match="expected: api, artifact_sha256, data_schemas, kind") as error:
        validate_release(release)
    assert "private-key-name" not in str(error.value) and "private-value" not in str(error.value)


def test_release_and_observation_component_count_bounds():
    release = manifest()
    for i in range(MAX_ITEMS - 3):
        name = f"c{i}"
        release["components"][name] = copy.deepcopy(release["components"]["core"])
        release["roles"]["gateway"].append(name)
    assert len(release["components"]) == MAX_ITEMS
    assert inspect(release, observation(release), role="client")["status"] == "ready"
    too_many = copy.deepcopy(release)
    too_many["components"]["extra"] = copy.deepcopy(release["components"]["core"])
    with pytest.raises(PlanError, match="exceeds bounds"):
        validate_release(too_many)
    observed = observation(release)
    observed["components"]["extra"] = copy.deepcopy(observed["components"]["core"])
    with pytest.raises(PlanError, match="exceeds bounds"):
        inspect(release, observed, role="client")


@pytest.mark.parametrize("field", ["authority", "installation"])
def test_wrong_identity_rejected_without_echoing_input(field):
    release = manifest()
    observed = observation(release)
    observed[field] = "private-value"
    with pytest.raises(PlanError, match="mismatch") as error:
        inspect(release, observed)
    assert "private-value" not in str(error.value)


def test_client_inspection_does_not_require_server_or_node_observations():
    release = manifest()
    observed = observation(release)
    del observed["components"]["core"]
    del observed["components"]["executor"]
    result = inspect(release, observed, role="client")
    assert result["status"] == "ready"
    assert [row["component"] for row in result["components"]] == ["cli"]
    assert inspect(release, observed, role="node")["status"] == "missing"


@pytest.mark.parametrize("api,schema", [(2, 1), (3, 2)])
def test_inclusive_api_range_and_declared_schemas(api, schema):
    release = manifest()
    observed = observation(release)
    observed["components"]["core"].update(api=api, data_schema=schema)
    assert inspect(release, observed)["status"] == "ready"


def test_component_status_precedence_is_not_success_shaped():
    release = manifest()
    observed = observation(release)
    observed["components"]["cli"]["state"] = "installed"
    assert inspect(release, observed)["status"] == "installed"
    observed["components"]["core"]["state"] = "unknown"
    assert inspect(release, observed)["status"] == "unknown"
    del observed["components"]["core"]
    assert inspect(release, observed)["status"] == "missing"
    observed["components"]["cli"]["artifact_sha256"] = "d" * 64
    assert inspect(release, observed)["status"] == "incompatible"


@pytest.mark.parametrize("change", [
    "unknown-field", "wrong-version", "missing-role", "unknown-component",
    "duplicate-component", "empty-role", "server-on-client", "bad-digest",
    "bool-api", "inverted-range", "duplicate-schema", "empty-schema", "unreferenced",
])
def test_strict_release_contract(change):
    release = manifest()
    if change == "unknown-field":
        release["execute"] = "do-not-run"
    elif change == "wrong-version":
        release["version"] = True
    elif change == "missing-role":
        del release["roles"]["node"]
    elif change == "unknown-component":
        release["roles"]["node"] = ["absent"]
    elif change == "duplicate-component":
        release["roles"]["node"] *= 2
    elif change == "empty-role":
        release["roles"]["node"] = []
    elif change == "server-on-client":
        release["roles"]["client"].append("core")
    elif change == "bad-digest":
        release["components"]["cli"]["artifact_sha256"] = "private-invalid"
    elif change == "bool-api":
        release["components"]["cli"]["api"]["min"] = True
    elif change == "inverted-range":
        release["components"]["cli"]["api"]["max"] = 0
    elif change == "duplicate-schema":
        release["components"]["cli"]["data_schemas"] = [1, 1]
    elif change == "empty-schema":
        release["components"]["cli"]["data_schemas"] = []
    else:
        release["components"]["unused"] = copy.deepcopy(release["components"]["cli"])
    with pytest.raises(PlanError):
        validate_release(release)


@pytest.mark.parametrize("field,value", [
    ("complete", 1), ("observed_at", True), ("expires_at", 100),
    ("expires_at", float("inf")), ("observed_at", 10**1000),
    ("expires_at", 10**1000), ("schema", "unknown"),
])
def test_invalid_observation_is_error_not_unknown(field, value):
    release = manifest()
    observed = observation(release)
    observed[field] = value
    with pytest.raises(PlanError):
        inspect(release, observed)


@pytest.mark.parametrize("field,value", [("state", "healthy"), ("api", True),
                                      ("data_schema", 0), ("artifact_sha256", "A" * 64)])
def test_invalid_component_observations_rejected(field, value):
    release = manifest()
    observed = observation(release)
    observed["components"]["core"][field] = value
    with pytest.raises(PlanError):
        inspect(release, observed)


def test_snapshot_inspection_real_cli_is_read_only_and_has_no_hosting_dependencies(tmp_path):
    release = manifest()
    observed = observation(release, state="installed")
    observed["observed_at"] = int(time.time()) - 5
    observed["expires_at"] = int(time.time()) + 60
    release_path, observed_path = tmp_path / "release.json", tmp_path / "observed.json"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    args = [sys.executable, "-I", "-m", "fleet_manager", "inspect", "--release", str(release_path),
            "--observation", str(observed_path), "--role", "client",
            "--authority", "test-authority", "--installation", "test-installation"]
    run = subprocess.run(args, capture_output=True, text=True, timeout=15,
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["status"] == "installed" and not result["mutations_performed"]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert "core" not in run.stdout and "executor" not in run.stdout


def test_cli_missing_explicit_observation_file_is_error(tmp_path, capsys):
    from fleet_manager.__main__ import main

    release_path = tmp_path / "release.json"
    release_path.write_text(json.dumps(manifest()), encoding="utf-8")
    base = ["inspect", "--release", str(release_path), "--role", "client",
            "--authority", "test-authority", "--installation", "test-installation"]
    assert main(base) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "unknown"
    assert main(base + ["--observation", str(tmp_path / "missing-private")]) == 1
    output = capsys.readouterr()
    assert not output.out and "missing-private" not in output.err


def test_cli_oversized_epoch_is_bounded_error(tmp_path, capsys):
    from fleet_manager.__main__ import main

    release = manifest()
    observed = observation(release)
    observed["observed_at"] = 10**1000
    release_path, observed_path = tmp_path / "release.json", tmp_path / "observed.json"
    release_path.write_text(json.dumps(release), encoding="utf-8")
    observed_path.write_text(json.dumps(observed), encoding="utf-8")
    assert main(["inspect", "--release", str(release_path), "--observation", str(observed_path),
                 "--role", "client", "--authority", "test-authority",
                 "--installation", "test-installation"]) == 1
    output = capsys.readouterr()
    assert not output.out and "Traceback" not in output.err
    assert "finite epoch time" in output.err
