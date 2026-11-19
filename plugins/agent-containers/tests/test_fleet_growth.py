"""On-demand fleet growth: ``borrow`` grows a ``max_size`` fleet by one member."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import yaml

from agent_containers import config as config_mod
from agent_containers import fleet as fleet_mod
from agent_containers import fleet_growth
from agent_containers import lease as lease_mod
from agent_containers import plugin_config
from agent_containers.config import ContainersConfig, FleetConfig, load_config
from agent_containers.fleet_results import FleetOperationResult
from agent_containers.lifecycle import DockerContainerInfo


@dataclass
class FakeProvisioner:
    """Stands in for the ``up`` path; records each requested target count."""

    members: list[DockerContainerInfo]
    state_dir: Path
    calls: list[int | None] = field(default_factory=list)
    fail: str | None = None
    delay: float = 0.0
    # Runs while a new member is visible under its temporary pre-rename name.
    during: Callable[[], None] | None = None
    # Fail after the member exists, like a failed post-create setup step.
    fail_after_create: str | None = None

    def reconcile_up(
        self, config: ContainersConfig, fleet_name: str, count: int | None = None,
        recreate: bool = False, force_abandon: bool = False,
    ) -> FleetOperationResult:
        self.calls.append(count)
        if self.fail:
            raise RuntimeError(self.fail)
        time.sleep(self.delay)
        existing = [c for c in self.members if c.fleet == fleet_name]
        created = []
        for idx in fleet_mod._next_indices(existing, fleet_name, (count or 0) - len(existing)):
            name = f"{fleet_name}-{idx}"
            member = DockerContainerInfo(
                f"tmp-{idx}", name, "img", "running", "", fleet=fleet_name,
                labels={"agent-containers.instance": name},
            )
            self.members.append(member)
            if self.during is not None:
                self.during()
            if self.fail_after_create:
                raise RuntimeError(self.fail_after_create)
            member.name = name  # like the devcontainer path's final rename
            created.append(name)
        return FleetOperationResult(created=created)


def _redirect_lease_state(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    for attr, name in (
        ("LEASE_FILE", "leases.json"), ("_LOCK_FILE", "leases.lock"),
        ("_LEASE_DETAILS_FILE", "lease-details.json"),
        ("_DEPLOY_HOLDS_FILE", "deploy-holds.json"),
        ("_SESSION_ADMISSIONS_FILE", "session-admissions.json"),
    ):
        monkeypatch.setattr(lease_mod, attr, root / name)
    monkeypatch.setattr(lease_mod, "STATE_DIR", root)
    monkeypatch.setattr(lease_mod, "ensure_state_dir", lambda: None)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeProvisioner:
    """Lease state in tmp; one running ``example-1`` member; a fake ``up`` path."""
    _redirect_lease_state(monkeypatch, tmp_path)
    provisioner = FakeProvisioner(
        [DockerContainerInfo("example-1", "i1", "img", "running", "", fleet="example")],
        tmp_path,
    )

    def members(config: ContainersConfig, name: str) -> list[DockerContainerInfo]:
        return [c for c in provisioner.members if c.fleet == name]

    monkeypatch.setattr(lease_mod, "list_containers", lambda config: list(provisioner.members))
    monkeypatch.setattr(fleet_mod, "_fleet_members", members)
    monkeypatch.setattr(fleet_mod, "reconcile_up", provisioner.reconcile_up)
    return provisioner


def _config(max_size: int | None = None) -> ContainersConfig:
    return ContainersConfig(fleets={"example": FleetConfig(image="img", max_size=max_size)})


def test_without_max_size_the_refusal_is_unchanged(fake: FakeProvisioner) -> None:
    config = _config()
    lease_mod.borrow(config, "a", fleet="example")
    with pytest.raises(RuntimeError, match="All fleet containers are currently leased"):
        lease_mod.borrow(config, "b", fleet="example")
    assert fake.calls == []
    assert len(fake.members) == 1


def test_borrow_grows_by_one_then_refuses_at_max_size(fake: FakeProvisioner) -> None:
    config = _config(max_size=2)
    assert lease_mod.borrow(config, "a", fleet="example").container == "example-1"

    grown = lease_mod.borrow(config, "b", fleet="example")

    assert (grown.container, grown.effort) == ("example-2", "b")
    assert fake.calls == [2]
    with pytest.raises(RuntimeError, match=r"max_size \(2\)") as refused:
        lease_mod.borrow(config, "c", fleet="example")
    assert "All fleet containers are currently leased" in str(refused.value)
    assert fake.calls == [2]
    assert {lease.effort for lease in lease_mod.list_leases()} == {"a", "b"}


def test_unscoped_borrow_grows_the_only_fleet_and_no_grow_opts_out(
    fake: FakeProvisioner,
) -> None:
    config = _config(max_size=3)
    lease_mod.borrow(config, "a")
    with pytest.raises(RuntimeError, match="All fleet containers"):
        lease_mod.borrow(config, "b", grow=False)
    assert lease_mod.borrow(config, "b").container == "example-2"


def test_unscoped_borrow_does_not_guess_between_fleets(fake: FakeProvisioner) -> None:
    config = _config(max_size=3)
    config.fleets["other"] = FleetConfig(image="img", max_size=2)
    lease_mod.borrow(config, "a")
    with pytest.raises(RuntimeError, match="All fleet containers"):
        lease_mod.borrow(config, "b")
    assert fake.calls == []


def test_released_grown_member_stays_warm_and_is_reused(fake: FakeProvisioner) -> None:
    config = _config(max_size=2)
    lease_mod.borrow(config, "a", fleet="example")
    lease_mod.borrow(config, "b", fleet="example")
    assert lease_mod.release("b")
    assert lease_mod.borrow(config, "c", fleet="example").container == "example-2"
    assert fake.calls == [2]


def test_member_being_provisioned_is_hidden_until_the_grower_leases_it(
    fake: FakeProvisioner,
) -> None:
    config = _config(max_size=2)
    lease_mod.borrow(config, "a", fleet="example")
    checked: list[str] = []

    def during() -> None:
        # Visible to inventory under its temporary name, but not borrowable.
        assert any(c.name == "tmp-2" for c in fake.members)
        with pytest.raises(RuntimeError, match="All fleet containers"):
            lease_mod.borrow(config, "intruder", fleet="example", grow=False)
        with pytest.raises(lease_mod.ProviderAdmissionError, match="still being provisioned"):
            with lease_mod.session_admission("example-2"):
                pass
        checked.append("during")

    fake.during = during
    grown = lease_mod.borrow(config, "b", fleet="example")

    assert checked == ["during"]
    assert (grown.container, grown.effort) == ("example-2", "b")
    assert fleet_growth._read_markers(fake.state_dir) == {}
    with lease_mod.session_admission("example-2"):
        pass


def test_marker_stays_live_past_its_heartbeat_ttl_during_a_long_build(
    fake: FakeProvisioner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fleet_growth, "MARKER_HEARTBEAT_INTERVAL", 0.02)
    monkeypatch.setattr(fleet_growth, "MARKER_HEARTBEAT_TTL", 0.2)
    config = _config(max_size=2)
    lease_mod.borrow(config, "a", fleet="example")

    def during() -> None:
        time.sleep(0.6)  # three heartbeat TTLs of an active build
        with pytest.raises(RuntimeError, match="All fleet containers"):
            lease_mod.borrow(config, "intruder", fleet="example", grow=False)

    fake.during = during
    assert lease_mod.borrow(config, "b", fleet="example").container == "example-2"


def _removing(fake: FakeProvisioner, fail: bool) -> Callable[..., None]:
    def remove(name: str, force: bool = False) -> None:
        if fail:
            raise RuntimeError("example-driver remove failed")
        fake.members[:] = [m for m in fake.members if m.container_id != name]
    return remove


def test_failed_growth_removes_the_partial_member(
    fake: FakeProvisioner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fleet_growth.lifecycle_mod, "remove_container", _removing(fake, False))
    fake.fail_after_create = "dotfiles setup failed"
    config = _config(max_size=2)
    lease_mod.borrow(config, "a", fleet="example")

    with pytest.raises(RuntimeError, match="dotfiles setup failed"):
        lease_mod.borrow(config, "b", fleet="example")

    assert [m.name for m in fake.members] == ["example-1"]
    assert fleet_growth._read_markers(fake.state_dir) == {}
    assert [lease.effort for lease in lease_mod.list_leases()] == ["a"]


def test_partial_member_is_quarantined_when_removal_fails(
    fake: FakeProvisioner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fleet_growth.lifecycle_mod, "remove_container", _removing(fake, True))
    fake.fail_after_create = "dotfiles setup failed"
    config = _config(max_size=3)
    lease_mod.borrow(config, "a", fleet="example")
    with pytest.raises(RuntimeError, match="dotfiles setup failed"):
        lease_mod.borrow(config, "b", fleet="example")
    assert [m.name for m in fake.members] == ["example-1", "tmp-2"]

    with pytest.raises(RuntimeError, match="All fleet containers"):
        lease_mod.borrow(config, "c", fleet="example", grow=False)
    for name in ("tmp-2", "example-2"):
        with pytest.raises(lease_mod.ProviderAdmissionError, match="still being provisioned"):
            with lease_mod.session_admission(name):
                pass
    assert [lease.effort for lease in lease_mod.list_leases()] == ["a"]


def test_provisioning_failure_leaves_no_lease_and_reports_the_error(
    fake: FakeProvisioner,
) -> None:
    fake.fail = "example-driver provision failed: out of memory"
    config = _config(max_size=3)
    lease_mod.borrow(config, "a", fleet="example")

    with pytest.raises(RuntimeError) as refused:
        lease_mod.borrow(config, "b", fleet="example")

    message = str(refused.value)
    assert "All fleet containers are currently leased" in message
    assert "out of memory" in message
    assert fake.calls == [2]
    assert [lease.effort for lease in lease_mod.list_leases()] == ["a"]
    assert fleet_growth._read_markers(fake.state_dir) == {}


def test_concurrent_borrows_never_overshoot_max_size(fake: FakeProvisioner) -> None:
    fake.delay = 0.05
    config = _config(max_size=3)
    leased: list[str] = []
    refused: list[str] = []
    guard = threading.Lock()
    start = threading.Barrier(6)

    def worker(effort: str) -> None:
        start.wait()
        try:
            lease = lease_mod.borrow(config, effort, fleet="example")
        except RuntimeError as exc:
            with guard:
                refused.append(str(exc))
        else:
            with guard:
                leased.append(lease.container)

    threads = [threading.Thread(target=worker, args=(f"e{i}",)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(leased) == ["example-1", "example-2", "example-3"]
    assert len(refused) == 3
    assert all("max_size (3)" in message for message in refused)
    assert len(fake.members) == 3
    assert fake.calls == [2, 3]


# -- configuration -----------------------------------------------------------


def _load_fleet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fleet: dict[str, object],
) -> FleetConfig:
    path = tmp_path / "containers.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 2, "fleets": {"example": fleet}}))
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    monkeypatch.setattr(plugin_config, "_active_plugin_roots", lambda *_: [])
    return load_config(strict=True).fleets["example"]


def test_max_size_is_optional_and_may_equal_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _load_fleet(tmp_path, monkeypatch, {"image": "img"}).max_size is None
    fleet = _load_fleet(tmp_path, monkeypatch, {"image": "img", "size": 2, "max_size": 2})
    assert (fleet.size, fleet.max_size) == (2, 2)


@pytest.mark.parametrize(("extra", "match"), [
    ({"size": 3, "max_size": 2}, r"max_size \(2\) must be a positive integer >= size \(3\)"),
    ({"size": 0, "max_size": 0}, r"max_size \(0\) must be a positive integer"),
    ({"max_size": "4"}, "max_size must be an integer"),
    ({"max_size": 2.5}, "max_size must be an integer"),
    ({"max_size": True}, "max_size must be an integer"),
    ({"lifecycle_owner": "external", "max_size": 2}, "max_size requires a fleet"),
])
def test_invalid_max_size_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: dict[str, object], match: str,
) -> None:
    with pytest.raises(RuntimeError, match=match):
        _load_fleet(tmp_path, monkeypatch, {"image": "img", **extra})


def test_max_size_composes_over_a_plugin_fragment_and_validates_composed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_mod, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config_mod, "_knowledge_overlay_config", lambda: None)
    plugin = tmp_path / "plugins" / "example-harness"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(json.dumps(
        {"name": "example-harness", plugin_config.MANIFEST_FIELD: "containers.yaml"},
    ))
    (plugin / "containers.yaml").write_text(yaml.safe_dump(
        {"fleets": {"example": {"image": "img", "size": 1, "max_size": 2}}},
    ))
    monkeypatch.setattr(
        plugin_config, "_active_plugin_roots", lambda *_: [("example-harness@example", [plugin])],
    )
    operator = tmp_path / "operator.yaml"
    operator.write_text("schema_version: 2\nfleets:\n  example:\n    max_size: 4\n")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(operator))

    config = load_config()

    fleet = config.fleets["example"]
    assert (fleet.size, fleet.max_size) == (1, 4)
    provenance = config.configuration_provenance
    assert provenance["/fleets/example/max_size"]["id"] == str(operator)
    assert provenance["/fleets/example/size"]["id"] == "plugin:example-harness@example"

    # The bound is checked against the composed size, whichever layer set it.
    operator.write_text("schema_version: 2\nfleets:\n  example:\n    size: 5\n")
    with pytest.raises(RuntimeError, match=r"max_size \(2\) must be a positive integer >= size"):
        load_config()
