"""Synthetic receipt admission; the separate native lane drives real owners."""
from dataclasses import replace
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

from packaging.markers import default_environment
import pytest

import runtime_source_reuse as reuse
from installer_wheel_snapshot import WheelReuseInputs
from runtime_profile import SCHEMA, ProfileError
from runtime_source_authentication import AuthenticatedSource
from runtime_source_lock import SourceConfiguration
from test_runtime_source_lock import source as source  # noqa: F401


def canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(record):
    return "sha256:" + hashlib.sha256(canonical(record).encode()).hexdigest()


def wheel(name, version="1.0"):
    return {
        "name": name, "version": version, "filename": name.replace("-", "_") + f"-{version}-py3-none-any.whl",
        "sha256": "sha256:" + "8" * 64, "requires_python": "",
        "provides_extras": [], "selected_extras": [], "requires_dist": [],
    }


@pytest.fixture
def existing(tmp_path, source):
    payload, identity = source
    root = tmp_path / "runtime"
    target = {"environment": default_environment(), "tags": ["py3-none-any"], "device": "cpu"}
    policy = {
        "owner": {"runtimeRoot": str(root), "pluginId": identity.plugin},
        "selection": {"backend": "cpu", "allowSourceFallback": True}, "policyId": "sha256:" + "1" * 64,
    }
    configuration = {
        "schema": "copilot-extensions.source-configuration", "version": 1, "mode": "source",
        "source": identity.document(), "trigger": "release-index-unavailable", "policy": policy,
        "target": target, "component": "fixture", "extras": [], "python": str(Path(sys.executable).resolve()),
        "retained_source_sha256": "sha256:" + reuse.engine.runtime.fingerprint_source(
            reuse.engine._source_files(reuse.engine._package_roots(payload)),
        ), "configuration_policy": "sha256:" + "2" * 64, "build_roots": ["packaging==1.0"],
    }
    events = []
    authenticated = AuthenticatedSource(
        payload, identity, canonical({"source": identity.document(), "owner_policy": policy["policyId"]}).encode(),
        lambda: events.append("authenticate"),
    )
    captured = SourceConfiguration(
        canonical(configuration).encode(), payload, None, (), tmp_path / "unused",
        lambda: events.append("configuration"),
    )
    locked = {
        **configuration, "schema": "copilot-extensions.source-lock-candidate",
        "source_authentication": authenticated.record(),
        "acquisition": {"policy_id": "sha256:" + "3" * 64},
        "native_lock_sha256": "sha256:" + "4" * 64,
    }
    profile = {
        "schema": SCHEMA, "version": 1, "mode": "source", "artifact": None,
        "release": {"identity": "source:" + identity.source_commit, "sha256": "sha256:" + identity.assembly_hash},
        "target": target, "component": "fixture", "backend": "cpu",
        "approval_source": policy["policyId"], "acquisition_policy": digest(locked),
        "roots": ["fixture-source==1.0.0"], "wheels": [wheel("fixture-source", "1.0.0")],
    }
    build_profile = {
        **profile, "roots": configuration["build_roots"], "wheels": [wheel("packaging")],
        "approval_source": "configured-source:" + hashlib.sha256(authenticated.canonical).hexdigest(),
        "acquisition_policy": locked["acquisition"]["policy_id"],
    }
    binding = {
        "profile": profile, "source_lock": locked, "build_profile": build_profile,
        "build_toolchain": {"packages": {"packaging": "1.0"}, "admitted_profile": build_profile},
    }
    receipt = {
        "construction": "closed-wheels", "lock_id": digest(binding), "binding": binding,
        "distributions": {item["name"]: {key: item[key] for key in ("version", "filename", "sha256")}
                          for item in profile["wheels"]},
    }
    generation = identity.runtime_version + ".profile." + receipt["lock_id"][7:23]
    slot = reuse.engine.runtime.slot(root, generation)
    frozen = reuse.engine.freeze_inputs(payload, slot / "payload")
    local_packages = ("fixture-source",)
    inputs = WheelReuseInputs(generation, canonical(receipt), time.monotonic() + 60)
    payload_hash = reuse.engine._wheel_construction_hash(
        reuse.engine._construction_hash(reuse.engine._source_files(frozen.packages), (), local_packages), inputs,
    )
    reuse.engine.runtime.mark_complete(root, generation, payload_hash=payload_hash)
    (root / "current-version").write_text(generation + "\n")
    proof = slot / ".runtime-input-origins.json"
    evidence = {
        "schema": "installer-engine.frozen-input-origins", "version": 2,
        "packages": {"fixture-source": "file:///synthetic.whl#sha256=" + "8" * 64},
        "wheel_inputs": receipt, "local_packages": list(local_packages),
    }
    proof.write_text(canonical(evidence))
    return SimpleNamespace(
        root=root, slot=slot, proof=proof, evidence=evidence, receipt=receipt,
        authenticated=authenticated, captured=captured, events=events,
    )


def retain(existing, **changes):
    options = dict(max_receipt_bytes=1 << 20, max_tags=100, deadline=time.monotonic() + 60)
    return reuse.retain_source_runtime_reuse(
        existing.authenticated, existing.captured, **(options | changes),
    )


def test_existing_receipt_admits_only_after_current_live_owners_and_completed_binding(existing):
    before = existing.proof.read_bytes()
    with retain(existing) as source:
        assert isinstance(source.wheels, WheelReuseInputs)
        assert source.wheels.receipt() == existing.receipt
        assert source.wheels.generation == existing.slot.name
        assert source.local_packages == ("fixture-source",)
        assert source.payload == existing.captured.payload
        assert "authenticate" in existing.events and "configuration" in existing.events
        source.verify()
    assert existing.proof.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("configuration_policy", "sha256:" + "5" * 64), ("component", "other"),
    ("extras", ["feature"]), ("python", "other-interpreter"),
    ("trigger", "artifact-unavailable"), ("source", {"source_commit": "c" * 40}),
])
def test_changed_current_selection_is_unavailable_not_approved_by_old_receipt(existing, field, value):
    current = existing.captured.record()
    current[field] = value
    existing.captured = replace(existing.captured, canonical=canonical(current).encode())
    with retain(existing) as source:
        assert source is None


@pytest.mark.parametrize("corruption", [
    "missing", "malformed", "duplicate", "binding", "distributions", "selection", "source", "origins",
])
def test_corrupt_supplied_evidence_never_degrades_to_source_availability(existing, corruption):
    evidence = existing.evidence
    if corruption == "binding":
        evidence["wheel_inputs"]["binding"]["source_lock"]["configuration_policy"] = "sha256:" + "5" * 64
        evidence["wheel_inputs"]["lock_id"] = digest(evidence["wheel_inputs"]["binding"])
    elif corruption == "distributions":
        evidence["wheel_inputs"]["distributions"]["fixture-source"]["version"] = "9.0"
    elif corruption == "selection":
        evidence["local_packages"] = []
    elif corruption == "source":
        (existing.slot / "payload" / "fixture_backend.py").write_text("corrupted stored source")
    elif corruption == "origins":
        evidence["packages"] = {}
    if corruption == "missing":
        existing.proof.unlink()
    elif corruption == "malformed":
        existing.proof.write_text("{")
    elif corruption == "duplicate":
        existing.proof.write_text('{"version":2,"version":2}')
    else:
        existing.proof.write_text(canonical(evidence))
    with pytest.raises(ProfileError, match="evidence|local-package selection"):
        with retain(existing):
            pytest.fail("corrupt evidence became available or triggered construction")


@pytest.mark.parametrize("revoked", ["receipt", "profile", "source", "native-lock", "build-profile", "runtime-wheel"])
def test_current_deny_evidence_is_checked_before_reuse(existing, revoked):
    binding = existing.receipt["binding"]
    blocked = {
        "receipt": existing.receipt["lock_id"], "profile": digest(binding["profile"]),
        "source": "sha256:" + existing.authenticated.identity.assembly_hash,
        "native-lock": binding["source_lock"]["native_lock_sha256"],
        "build-profile": digest(binding["build_profile"]),
        "runtime-wheel": next(iter(existing.receipt["distributions"].values()))["sha256"],
    }[revoked]
    with pytest.raises(ProfileError, match="revoked"):
        with retain(existing, revoked_inputs=lambda: frozenset({blocked})):
            pytest.fail("revoked evidence entered reuse")


def test_locked_runtime_only_wheel_denial_is_visible_to_build_receipt(existing):
    receipt = existing.receipt
    locked = receipt["binding"]["source_lock"]
    digest = "sha256:" + "7" * 64
    locked["files"] = [{"sha256": digest}]
    assert digest not in {wheel["sha256"] for wheel in receipt["distributions"].values()}
    with pytest.raises(ProfileError, match="revoked"):
        reuse.verify_not_revoked(receipt, lambda: frozenset({digest}))


def test_revocation_is_rechecked_at_consumption_not_just_initial_admission(existing):
    denied = set()
    with pytest.raises(ProfileError, match="revoked"):
        with retain(existing, revoked_inputs=lambda: frozenset(denied)) as source:
            denied.add(existing.receipt["lock_id"])
            source.verify()


@pytest.mark.parametrize("denied", [set(), frozenset({"bad-digest"}), ("sha256:" + "9" * 64,)])
def test_malformed_deny_evidence_refuses_before_reuse(existing, denied):
    with pytest.raises(ProfileError, match="revocation evidence"):
        with retain(existing, revoked_inputs=lambda: denied):
            pytest.fail("malformed policy evidence became absence")


def test_receipt_read_is_bounded(existing):
    with pytest.raises(ProfileError, match="byte budget"):
        with retain(existing, max_receipt_bytes=1):
            pytest.fail("unbounded evidence was admitted")


def test_missing_selected_runtime_is_unavailable(existing):
    (existing.root / "current-version").unlink()
    with retain(existing) as source:
        assert source is None


def test_unpublished_selected_runtime_is_unavailable(existing):
    reuse.engine.runtime.marker_path(existing.root, existing.slot.name).unlink()
    with retain(existing) as source:
        assert source is None


def test_absent_selected_slot_is_unavailable(existing):
    existing.slot.rename(existing.slot.with_name(existing.slot.name + ".saved"))
    with retain(existing) as source:
        assert source is None


def test_nonordinary_selected_generation_refuses_before_path_consumption(existing):
    (existing.root / "current-version").write_text("../other")
    with pytest.raises(ProfileError, match="selector evidence"):
        with retain(existing):
            pytest.fail("unsafe selected generation was consumed")


@pytest.mark.parametrize("corruption", ["file", "link", "dangling"])
@pytest.mark.parametrize("entry", ["retained", "manager"])
def test_corrupt_slot_never_degrades_to_source_availability(existing, monkeypatch, corruption, entry):
    saved = existing.slot.with_name(existing.slot.name + ".saved")
    existing.slot.rename(saved)
    if corruption == "file":
        existing.slot.write_text("not a runtime directory")
    else:
        try:
            existing.slot.symlink_to(saved if corruption == "link" else saved / "absent", target_is_directory=True)
        except OSError:
            if os.name == "nt":
                pytest.skip("native Windows symlink creation needs elevation")
            raise
    context = retain(existing)
    if entry == "manager":
        import runtime_source_build as builds

        @contextmanager
        def authenticate(*args, **kwargs):
            yield existing.authenticated

        @contextmanager
        def configuration(*args, **kwargs):
            yield existing.captured

        monkeypatch.setattr(builds, "retain_authenticated_source", authenticate)
        monkeypatch.setattr(builds, "retain_source_configuration", configuration)
        monkeypatch.setattr(builds, "retain_source_runtime_inputs", lambda *args, **kwargs: pytest.fail(
            "corrupt selected slot reached cold source admission",
        ))
        plan = SimpleNamespace(policy=lambda: {"owner": {"runtimeRoot": str(existing.root)}})
        context = builds._manager_source_inputs(
            plan, revoked_inputs=None, installation={}, target={}, component="fixture",
            uv=Path("uv"), python=Path(sys.executable), deadline=time.monotonic() + 60,
            max_snapshot_bytes=1 << 20, wheel_limits=SimpleNamespace(max_tags=100),
        )
    with pytest.raises(ProfileError, match="slot evidence"):
        with context:
            pytest.fail("corrupt slot triggered cold source admission")


@pytest.mark.parametrize("corruption", ["malformed", "duplicate", "version", "directory", "encoding"])
def test_corrupt_completion_never_degrades_to_source_availability(existing, corruption):
    marker = reuse.engine.runtime.marker_path(existing.root, existing.slot.name)
    if corruption == "directory":
        marker.unlink()
        marker.mkdir()
    elif corruption == "encoding":
        marker.write_bytes(b"\xff")
    else:
        marker.write_text({
            "malformed": "{", "duplicate": '{"version":1,"version":1}',
            "version": '{"version":"other","completed_at":"","pid":1}',
        }[corruption])
    with pytest.raises(ProfileError, match="completion evidence"):
        with retain(existing):
            pytest.fail("corrupt completion triggered cold source admission")


def test_completion_is_rechecked_during_consumption(existing):
    marker = reuse.engine.runtime.marker_path(existing.root, existing.slot.name)
    with pytest.raises(ProfileError, match="completion evidence changed"):
        with retain(existing) as source:
            marker.write_text("{")
            source.verify()


@pytest.mark.parametrize("code", [1, 74])
def test_origin_refusal_is_not_installed_health_repair(existing, monkeypatch, code):
    engine = reuse.engine
    with retain(existing) as source:
        inputs = engine.FrozenInputs(
            source.payload, engine._package_roots(source.payload), "fixture",
            local_packages=source.local_packages, wheel_inputs=source.wheels,
        )

        def failed(argv, **kwargs):
            assert "receipt-reuse" in argv
            raise subprocess.CalledProcessError(code, argv, stderr="synthetic isolated failure")

        monkeypatch.setattr(engine, "_child", failed)
        expected = engine.DependencyPolicyError if code == 74 else engine.HealthRepairRequired
        with pytest.raises(expected):
            engine._verify_installed(
                inputs, existing.slot, Path(sys.executable), "fixture_source", 0, 30,
                require_frozen_origin=False,
            )


def test_same_engine_refuses_construction_from_existing_receipt(existing, monkeypatch):
    engine = reuse.engine
    receipt = WheelReuseInputs("missing", canonical(existing.receipt), time.monotonic() + 60)
    monkeypatch.setattr(engine, "_construct", lambda *args, **options: pytest.fail("receipt constructed a runtime"))
    with pytest.raises(engine.HealthRepairRequired, match="cannot construct"):
        with engine.numbered_candidate(
            existing.root, "missing", plugin=existing.captured.payload, uv=Path("uv"),
            base_python=Path(sys.executable), health_module="fixture_source",
            wheel_inputs=receipt, local_packages=("fixture-source",),
        ):
            pytest.fail("missing runtime was admitted from its receipt")
    assert not engine.runtime.version_dir(existing.root, "missing").exists()


def test_same_engine_reuses_under_retention_without_constructing_or_rewriting(existing, monkeypatch):
    engine = reuse.engine
    health = []
    monkeypatch.setattr(engine, "_construct", lambda *args, **options: pytest.fail("warm runtime was constructed"))
    monkeypatch.setattr(engine, "_verify_installed", lambda *args, **options: health.append(options))
    with retain(existing) as source, engine.numbered_candidate(
        existing.root, existing.slot.name, plugin=source.payload, uv=Path("uv"),
        base_python=Path(sys.executable), health_module="fixture_source",
        wheel_inputs=source.wheels, local_packages=source.local_packages,
    ) as candidate:
        assert candidate.reused and candidate.slot == existing.slot
    assert len(health) == 1 and health[0]["require_frozen_origin"] is False
