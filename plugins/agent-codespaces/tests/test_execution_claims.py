"""A native incumbent cannot be displaced by ACP/SSH fallback or stale cleanup."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agent_codespaces import __main__ as cli
from agent_codespaces import execution_claims as modes
from agent_codespaces import lease


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(lease, "LEASE_FILE", tmp_path / "leases.json")
    monkeypatch.setattr(lease, "_LOCK_FILE", tmp_path / "lease.lock")
    monkeypatch.setattr(lease, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(lease, "ensure_runtime_dir", lambda: None)
    monkeypatch.setattr("ssh_manager.locks.locks_dir", lambda: tmp_path / "locks")
    return tmp_path


def native():
    modes.reserve("example-space", "owner", "native-one", "generation-one", "native")


@pytest.mark.parametrize("owner,force", [("owner", False), ("owner", True), ("other", False), ("other", True)])
def test_native_blocks_all_unqualified_claims_before_distributed_mutation(state, monkeypatch, owner, force):
    native()
    before = modes._path().read_bytes()
    monkeypatch.setattr(lease.coordination, "acquire", lambda *a: pytest.fail("must not mutate L2"))
    with pytest.raises(lease.ClaimConflict):
        lease.claim("example-space", owner, force=force, holder_ref="example/owner/worktree")
    assert modes._path().read_bytes() == before


@pytest.mark.parametrize("disabled", [False, True])
def test_claim_cli_cannot_fall_back_to_acp_for_same_owner(state, monkeypatch, disabled):
    native()
    if disabled:
        monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
    else:
        monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    assert cli.main([
        "claim", "example-space", "--owner", "owner", "--force-claim",
        "--interaction-mode", "acp", "--execution-id", "acp-one", "--generation", "acp-one",
    ]) == 75
    assert modes.get("example-space")["mode"] == "native"


def test_diagnostic_and_raw_terminal_cannot_bypass_native_incumbent(state, monkeypatch):
    native()
    monkeypatch.setattr(cli, "_gh_binary_available", lambda: True)
    monkeypatch.setattr(cli, "CodespaceSource", lambda *a, **k: object())
    monkeypatch.setattr("agent_codespaces.lifecycle.account_for_codespace", lambda _: None)
    monkeypatch.setattr(cli, "load_merged_config", lambda: SimpleNamespace(
        credentials=SimpleNamespace(relay_port=9857),
    ))
    for operation in (["--remote-cmd", "true"], ["--interactive-command", "true"]):
        assert cli.main([
            "ssh", "example-space", "--no-relay", "--no-provision", "--force", "--force-claim", *operation,
        ]) == 75


def test_retirement_is_identity_checked_and_replay_is_tombstoned(state):
    native()
    with pytest.raises(lease.ClaimConflict):
        modes.release("example-space", "owner", ("native-one", "wrong"), proof={"retired": True})
    with pytest.raises(lease.CoordinationRejected):
        modes.release("example-space", "owner", ("native-one", "generation-one"))
    assert modes.release("example-space", "owner", ("native-one", "generation-one"), proof={"retired": True})
    cached = modes.retirement("example-space", "owner", ("native-one", "generation-one"))
    assert cached["retired"] is True
    with pytest.raises(lease.CoordinationRejected):
        native()
    assert modes.reserve("example-space", "owner", "acp-next", "acp-next", "acp")


@pytest.mark.parametrize("corruption", ["invalid", "missing"])
def test_authority_loss_does_not_mean_empty_venue(state, corruption):
    native()
    if corruption == "invalid":
        modes._path().write_text("{invalid")
    else:
        modes._path().unlink()
    with pytest.raises(lease.CoordinationRejected):
        lease.claim("example-space", "owner")


def test_mode_race_has_exactly_one_winner(state):
    def reserve(mode):
        try:
            modes.reserve("example-space", "owner", mode, mode, mode)
            return True
        except (lease.ClaimConflict, RuntimeError):
            return False
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(reserve, ["acp", "native"]))
    assert sum(results) == 1
    assert modes.get("example-space")["mode"] in {"native", "acp"}


def test_only_clean_unlaunched_infrastructure_can_abort(state):
    native()
    identity = ("native-one", "generation-one")
    assert modes.get("example-space")["infrastructureStopped"] is True
    modes.mark("example-space", "owner", identity, infrastructureStopped=False)
    with pytest.raises(lease.CoordinationRejected):
        modes.abort_unlaunched("example-space", "owner", identity)
    modes.mark("example-space", "owner", identity, infrastructureStopped=True)
    assert modes.abort_unlaunched("example-space", "owner", identity)
    assert modes.retirement("example-space", "owner", identity)["noLaunch"]
    assert json.loads(modes._path().read_text())["claims"] == {}
