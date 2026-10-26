"""Explicit authenticated Manager caller enters its existing install lifecycle."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

import runtime_source_build as builds
from worktree_manager import self_install


@pytest.mark.parametrize("target_matches", [True, False])
def test_manager_source_caller_keeps_custody_through_health_and_publication(tmp_path, monkeypatch, target_matches):
    root = tmp_path / "manager"
    payload = tmp_path / "authenticated"
    owner = {"pluginId": "worktree-manager", "runtimeRoot": str(root)}
    plan = SimpleNamespace(policy=lambda: {"owner": dict(owner)})
    deadline = time.monotonic() + 60
    python = tmp_path / "selected-python"
    uv = tmp_path / "selected-uv"
    uv.touch()
    held, events = [], []

    def verify():
        assert held == [True]
        events.append("verify")

    source = SimpleNamespace(
        payload=payload, identity=SimpleNamespace(plugin="worktree-manager"),
        wheels=object(), local_packages=("copilot-extensions-worktree-manager",),
        profile=SimpleNamespace(record=lambda: {"target": {"environment": {"fixture": "target"}}}),
        verify=verify,
    )

    @contextmanager
    def retain(actual_plan, **options):
        assert actual_plan is plan and options == {
            "installation": "actual-owner", "deadline": deadline, "python": python, "uv": str(uv),
        }
        held.append(True)
        try:
            yield source
        finally:
            held.pop()
            events.append("released")

    result = self_install.SelfInstallResult("1.0", "installed", str(root))

    def install(actual_payload, **options):
        assert actual_payload == payload
        assert options["root"] == root and options["dry_run"] is False
        assert options["_wheel_inputs"] is source.wheels
        assert options["_local_packages"] == source.local_packages
        assert options["_base_python"] == python
        assert options["_uv"] == uv
        options["_validate_candidate"](root / "versions" / "fixture")
        events.append("health")
        options["_before_publish"]()
        events.append("publication")
        return result

    monkeypatch.setattr(builds, "retain_source_runtime_inputs", retain)
    monkeypatch.setattr(self_install, "self_install", install)
    monkeypatch.setattr(builds.engine.runtime, "slot_python", lambda root, version: root / version / "python")

    def environment(python, **options):
        assert python == root / "fixture" / "python"
        assert options == {"deadline": deadline}
        return {"fixture": "target" if target_matches else "different"}

    monkeypatch.setattr(builds, "_query_marker_environment", environment)
    if target_matches:
        assert builds.install_manager_source_runtime(
            plan, installation="actual-owner", deadline=deadline, python=python, uv=uv,
        ) is result
        assert events == ["verify", "verify", "health", "verify", "publication", "verify", "released"]
    else:
        with pytest.raises(ValueError, match="selected target"):
            builds.install_manager_source_runtime(
                plan, installation="actual-owner", deadline=deadline, python=python, uv=uv,
            )
        assert events == ["verify", "verify", "released"]
    assert held == []


@pytest.mark.parametrize("owner,extras", [
    ("foreign", ()), ("worktree-manager", ("server",)),
])
def test_foreign_or_unsupported_manager_request_refuses_before_source_build(
    tmp_path, monkeypatch, owner, extras,
):
    plan = SimpleNamespace(policy=lambda: {
        "owner": {"pluginId": owner, "runtimeRoot": str(tmp_path)},
    })
    monkeypatch.setattr(builds, "retain_source_runtime_inputs", lambda *args, **options: pytest.fail(
        "unsupported owner reached source acquisition",
    ))
    with pytest.raises(ValueError, match="base installation owner"):
        builds.install_manager_source_runtime(plan, extras=extras)


def test_foreign_authenticated_source_never_reaches_manager_installation(tmp_path, monkeypatch):
    plan = SimpleNamespace(policy=lambda: {
        "owner": {"pluginId": "worktree-manager", "runtimeRoot": str(tmp_path)},
    })

    @contextmanager
    def retain(*args, **options):
        yield SimpleNamespace(identity=SimpleNamespace(plugin="foreign"))

    monkeypatch.setattr(builds, "retain_source_runtime_inputs", retain)
    monkeypatch.setattr(self_install, "self_install", lambda *args, **options: pytest.fail(
        "foreign authenticated source reached Manager",
    ))
    with pytest.raises(ValueError, match="foreign source identity"):
        builds.install_manager_source_runtime(plan)


def test_source_policy_invalidation_refuses_before_publication(tmp_path, monkeypatch):
    owner = {"pluginId": "worktree-manager", "runtimeRoot": str(tmp_path)}
    plan = SimpleNamespace(policy=lambda: {"owner": dict(owner)})
    events = []

    def verify():
        events.append("verify")
        if len(events) == 3:
            raise ValueError("current source policy changed")

    @contextmanager
    def retain(*args, **options):
        yield SimpleNamespace(
            identity=SimpleNamespace(plugin="worktree-manager"), payload=Path(tmp_path),
            wheels=object(), local_packages=("copilot-extensions-worktree-manager",),
            profile=SimpleNamespace(record=lambda: {"target": {"environment": {}}}),
            verify=verify,
        )

    def install(*args, **options):
        options["_validate_candidate"](tmp_path)
        options["_before_publish"]()
        pytest.fail("invalidated source reached publication")

    monkeypatch.setattr(builds, "retain_source_runtime_inputs", retain)
    monkeypatch.setattr(self_install, "self_install", install)
    monkeypatch.setattr(builds.engine.runtime, "slot_python", lambda root, version: tmp_path / "python")
    monkeypatch.setattr(builds, "_query_marker_environment", lambda *args, **options: {})
    with pytest.raises(ValueError, match="current source policy changed"):
        builds.install_manager_source_runtime(plan, deadline=time.monotonic() + 60)
    assert events == ["verify"] * 3


@pytest.mark.parametrize("repairable", [False, True])
def test_receipt_health_repair_releases_custody_before_reentering_real_source_admission(
    tmp_path, monkeypatch, repairable,
):
    plan = SimpleNamespace(policy=lambda: {
        "owner": {"pluginId": "worktree-manager", "runtimeRoot": str(tmp_path)},
    })
    events = []
    (tmp_path / "selected-uv").touch()
    receipt = builds.engine.WheelReuseInputs("generation", "{}", time.monotonic() + 60)
    source = SimpleNamespace(
        identity=SimpleNamespace(plugin="worktree-manager"), payload=tmp_path,
        wheels=receipt, local_packages=("copilot-extensions-worktree-manager",),
        verify=lambda: events.append("verify"),
    )

    @contextmanager
    def existing(*args, **options):
        events.append("retain-receipt")
        try:
            yield source
        finally:
            events.append("release-receipt")

    @contextmanager
    def cold(*args, **options):
        assert repairable
        assert events[-1] == "release-receipt"
        events.append("admit-source")
        yield SimpleNamespace(**(vars(source) | {"wheels": object()}))

    result = self_install.SelfInstallResult("1.0", "installed", str(tmp_path))
    installs = []

    def install(*args, **options):
        assert options["_raise_candidate_errors"] is True
        assert options["_uv"] == tmp_path / "selected-uv"
        installs.append(options["_wheel_inputs"])
        if len(installs) == 1:
            if repairable:
                raise builds.engine.HealthRepairRequired("installed health needs repair")
            raise ValueError("current input integrity refused")
        return result

    monkeypatch.setattr(builds, "_manager_source_inputs", existing)
    monkeypatch.setattr(builds, "retain_source_runtime_inputs", cold)
    monkeypatch.setattr(self_install, "self_install", install)
    if repairable:
        assert builds.install_manager_source_runtime(
            plan, deadline=time.monotonic() + 60, uv=tmp_path / "selected-uv",
        ) is result
        assert len(installs) == 2 and installs[0] is receipt
    else:
        with pytest.raises(ValueError, match="integrity refused"):
            builds.install_manager_source_runtime(
                plan, deadline=time.monotonic() + 60, uv=tmp_path / "selected-uv",
            )
        assert len(installs) == 1 and "admit-source" not in events


@pytest.mark.parametrize("raise_errors", [False, True])
def test_explicit_caller_may_retain_typed_candidate_error_without_changing_default_reporting(
    tmp_path, monkeypatch, raise_errors,
):
    @contextmanager
    def fail(*args, **options):
        raise builds.engine.HealthRepairRequired("synthetic installed health")
        yield

    monkeypatch.setattr(self_install, "payload_version", lambda payload: "1.0")
    monkeypatch.setattr(self_install, "needs_install", lambda version, root: True)
    monkeypatch.setattr(self_install, "_runtime_installer", lambda: SimpleNamespace(candidate=fail))
    if raise_errors:
        with pytest.raises(builds.engine.HealthRepairRequired, match="synthetic installed health"):
            self_install.self_install(
                tmp_path, root=tmp_path / "runtime", dry_run=False, _raise_candidate_errors=True,
            )
    else:
        result = self_install.self_install(tmp_path, root=tmp_path / "runtime", dry_run=False)
        assert result.action == "error" and result.reason == "synthetic installed health"
