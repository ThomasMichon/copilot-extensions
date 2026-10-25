from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest

from agent_ssh_service.config import ServiceConfig, SSHServiceError
from agent_ssh_service.store import DirectoryStore
from fleet_manager.gateway_ssh_provider import project_gateway_authorization, project_gateway_profiles
from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy
from fleet_manager.planning import PlanError

from conftest import enrollment, key


def test_join_commits_public_directory_and_deduplicates_original_request(config):
    store = DirectoryStore(config)
    assert store.snapshot()["directory"]["revision"] == 1
    update, changed = store.enroll("principal-worker-a", enrollment())
    assert changed and update["directory"]["revision"] == 2
    assert update["directory"]["machines"][0]["public_key"] == key(1)
    assert store.enroll("principal-worker-a", enrollment()) == (update, False)
    assert DirectoryStore(config).snapshot() == update


@pytest.mark.parametrize("mutation", ["target", "generation", "request", "key"])
def test_existing_identity_cannot_be_impersonated_or_rotated(config, mutation):
    store = DirectoryStore(config)
    before, _ = store.enroll("principal-worker-a", enrollment())
    request = enrollment()
    if mutation == "target":
        request["machine"] = "worker-b"
    elif mutation == "generation":
        request["generation"] = 2
    elif mutation == "request":
        request["host_key"] = key(23)
    else:
        request.update(request_id="different-request", public_key=key(23))
    with pytest.raises(SSHServiceError):
        store.enroll("principal-worker-a", request)
    assert store.snapshot() == before


@pytest.mark.parametrize("mutation", ["missing-host-pin", "duplicate-peer-key", "unknown-principal"])
def test_invalid_join_rolls_back_both_notification_and_request_journal(config, mutation):
    store = DirectoryStore(config)
    before, _ = store.enroll("principal-worker-a", enrollment())
    second = enrollment(1)
    if mutation == "missing-host-pin":
        second["host_key"] = None
    elif mutation == "duplicate-peer-key":
        second["public_key"] = key(1)
    principal = "unknown" if mutation == "unknown-principal" else "principal-worker-b"
    with pytest.raises((PlanError, SSHServiceError)):
        store.enroll(principal, second)
    assert store.snapshot() == before
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


def test_later_join_supplies_existing_profile_builder_and_login_authorization(config):
    store = DirectoryStore(config)
    store.enroll("principal-worker-a", enrollment())
    update, _ = store.enroll("principal-worker-b", enrollment(1))
    policy = GatewaySSHPolicy.from_dict(update["policy"])
    directory = update["directory"]
    projected = project_gateway_profiles(
        policy, directory, machine="worker-a", environment="linux",
        gateway="selected-gateway", identity_file="/private/local-key", local_public_key=key(1),
    )
    assert {row["name"] for row in projected["config"]["machines"]} == {"worker-a", "worker-b"}
    authorized = project_gateway_authorization(
        policy, directory, machine="worker-a", environment="linux", local_public_key=key(1),
    )
    assert {row["public_key"] for row in authorized["peers"]} == {key(1), key(2)}


def test_new_config_generation_withdraws_old_key_and_retains_floor_after_removal(config):
    store = DirectoryStore(config)
    store.enroll("principal-worker-a", enrollment())
    document = config.to_dict()
    document["revision"] = 2
    document["bindings"][0]["generation"] = 2
    second = DirectoryStore(ServiceConfig.from_dict(document))
    assert second.snapshot()["directory"]["machines"] == []
    document["revision"] = 3
    removed = document["bindings"].pop(0)
    DirectoryStore(ServiceConfig.from_dict(document))
    document["revision"] = 4
    document["bindings"].append({**removed, "generation": 1})
    with pytest.raises(SSHServiceError, match="enrollment-rollback"):
        DirectoryStore(ServiceConfig.from_dict(document))


@pytest.mark.parametrize("mutation", ["same-revision", "older-revision", "foreign-store"])
def test_restart_preserves_configuration_and_service_authority(config, mutation):
    DirectoryStore(config)
    document = config.to_dict()
    if mutation == "same-revision":
        document["bindings"][0]["alias"] = "renamed-worker"
    elif mutation == "older-revision":
        document["revision"] = 2
        DirectoryStore(ServiceConfig.from_dict(document))
        document["revision"] = 1
    else:
        document["fleet"] = "other-fleet"
    with pytest.raises(SSHServiceError):
        DirectoryStore(ServiceConfig.from_dict(document))


def test_configuration_validation_cannot_mutate_its_inputs(config):
    document = config.to_dict()
    expected = deepcopy(document)
    ServiceConfig.from_dict(document)
    assert document == expected
    document["bindings"][0]["generation"] = 20
    assert config.to_dict() == expected


def test_disconnected_request_cannot_commit_enrollment(config):
    store = DirectoryStore(config)
    calls = 0

    def checkpoint():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SSHServiceError("withdrawn")

    with pytest.raises(SSHServiceError, match="withdrawn"):
        store.enroll("principal-worker-a", enrollment(), checkpoint=checkpoint)
    assert store.snapshot()["directory"]["revision"] == 1


def test_filesystem_root_cannot_be_selected_as_service_state(config):
    value = config.to_dict()
    value["data_dir"] = Path(value["data_dir"]).anchor
    with pytest.raises(SSHServiceError, match="dedicated-service"):
        ServiceConfig.from_dict(value)
