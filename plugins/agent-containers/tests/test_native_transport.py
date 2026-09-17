"""Native container ownership shares the provider's lifecycle admission."""

import pytest

from agent_containers import lease, native_claims
from agent_containers.__main__ import main
from agent_containers.native_transport import _ports


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(lease, "ensure_state_dir", lambda: None)
    for key, value in {
        "_LOCK_FILE": "lock", "LEASE_FILE": "leases.json", "_LEASE_DETAILS_FILE": "details.json",
        "_DEPLOY_HOLDS_FILE": "holds.json", "_SESSION_ADMISSIONS_FILE": "admissions.json",
    }.items():
        monkeypatch.setattr(lease, key, tmp_path / value)
    return tmp_path


def test_native_claim_survives_transport_loss_and_blocks_acp_and_lifecycle(state):
    identity = ("execution", "generation", "owner")
    native_claims.reserve("fixture", identity, "container-id")
    native_claims.reserve("fixture", identity, "container-id")
    with pytest.raises(lease.ProviderAdmissionError):
        native_claims.reserve("fixture", identity, "replacement-container-id")
    with pytest.raises(lease.ProviderAdmissionError):
        with lease.session_admission("fixture"):
            pytest.fail("ACP acquired native-owned venue")
    with pytest.raises(lease.ProviderAdmissionError):
        with lease.deploy_hold("fixture", "remove"):
            pytest.fail("lifecycle acquired native-owned venue")
    with lease.session_admission("fixture", native_identity=identity):
        pass
    native_claims.mark_launch("fixture", identity)
    with pytest.raises(lease.ProviderAdmissionError):
        native_claims.retire("fixture", identity)
    with pytest.raises(lease.ProviderAdmissionError):
        native_claims.retire("fixture", ("execution", "other", "owner"), {"retired": True})
    native_claims.retire("fixture", identity, {"retired": True, "executionId": "execution", "generation": "generation"})
    assert native_claims.retirement("fixture", identity)["containerId"] == "container-id"
    with lease.session_admission("fixture"):
        pass
    with pytest.raises(lease.ProviderAdmissionError):
        native_claims.reserve("fixture", identity, "container-id")


def test_native_claim_loss_is_not_an_empty_container(state):
    native_claims.reserve("fixture", ("execution", "generation", "owner"), "container-id")
    native_claims._path().unlink()
    with pytest.raises(lease.ProviderAdmissionError, match="lost"):
        with lease.session_admission("fixture"):
            pass


def test_unlaunched_native_abort_requires_confirmed_infrastructure_cleanup(state):
    identity = ("execution", "generation", "owner")
    native_claims.reserve("fixture", identity, "container-id")
    native_claims.infrastructure("fixture", identity, stopped=False)
    with pytest.raises(lease.ProviderAdmissionError, match="cleanup proof"):
        native_claims.retire("fixture", identity)
    native_claims.infrastructure("fixture", identity, stopped=True)
    native_claims.retire("fixture", identity)
    assert native_claims.retirement("fixture", identity)["noLaunch"] is True


def test_native_requires_descriptor_without_falling_back_to_acp():
    with pytest.raises(SystemExit):
        main(["native-transport", "fixture", "--owner", "owner", "--execution-id", "execution", "--generation", "generation"])


@pytest.mark.parametrize("values", [["0:1"], ["65536:1"], ["1:1", "1:2"], ["host:1:2"]])
def test_native_container_forward_validation(values):
    with pytest.raises(ValueError):
        _ports(values)
