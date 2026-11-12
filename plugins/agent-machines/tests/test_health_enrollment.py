"""Actor-free enrollment controls; native task/installed closure has its own lane."""

from contextlib import contextmanager, nullcontext
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
from xml.etree import ElementTree as ET

import pytest

from agent_machines import health_enrollment as enrollment, health_jobs

SID = "S-1-5-21-1-2-3-1000"
TOKEN = "a" * 64


class References:
    def __init__(self):
        self.owners = {}
        self.pending = None

    def reference_transaction_custody(self, *_args, **_kwargs):
        return nullcontext()

    def _read_runtime_reference_state(self, *_args, **_kwargs):
        return copy.deepcopy(self.owners), self.pending

    def read_runtime_references(self, *_args):
        assert self.pending is None
        return copy.deepcopy(self.owners)

    def begin_runtime_reference_transaction(self, _root, owner):
        self.pending = self.pending or {"owner": owner, "token": "b" * 64}
        assert self.pending["owner"] == owner
        return {"token": self.pending["token"], "owners": copy.deepcopy(self.owners)}

    def commit_runtime_reference_transaction(self, _root, owner, token, updates, *, expected):
        assert self.pending == {"owner": owner, "token": token}
        assert {key: self.owners.get(key, []) for key in expected} == expected
        self.owners.update(updates)
        self.pending = None

    def replace_runtime_references(self, _root, owner, versions, *, expected):
        assert self.pending is None
        assert self.owners.get(owner, []) == expected
        self.owners[owner] = versions


class Task:
    def __init__(self):
        self.xml = None
        self.calls = []
        self.fail = None
        self.refuse_read = False

    def snapshot(self):
        configured = self.xml is not None
        return {
            "configured": configured, "xml": self.xml,
            "digest": hashlib.sha256(self.xml.encode()).hexdigest() if configured else "absent",
            "enabled": configured and enrollment.signature(self.xml)["Settings/Enabled"] == "true",
            "instances": [], "account_sid": SID, "state": 3,
        }

    def call(self, operation, *, expected="", definition=""):
        if operation == "read":
            if self.refuse_read:
                raise RuntimeError("read unavailable")
            return self.snapshot()
        assert self.snapshot()["digest"] == expected
        self.calls.append(operation)
        if self.fail == operation:
            self.fail = None
            raise RuntimeError("native reply unavailable")
        if operation in {"create", "replace"}:
            self.xml = definition
        elif operation == "disable":
            self.xml = enrollment.disabled(self.xml)
        elif operation == "remove":
            self.xml = None
        elif operation == "enable":
            root = ET.fromstring(self.xml)
            root.find(f"{{{health_jobs.NS}}}Settings/{{{health_jobs.NS}}}Enabled").text = "true"
            self.xml = ET.tostring(root, encoding="unicode")
        return self.snapshot()


@pytest.fixture
def case(tmp_path, monkeypatch):
    refs, native = References(), Task()
    runtime = SimpleNamespace(
        _runtime_component=lambda _name: refs, retention_lease=lambda *_args: nullcontext(),
    )
    desired = {"store": str(tmp_path / "store"), "policy_token": TOKEN, "user_id": SID, "enabled": True}
    monkeypatch.setattr(enrollment.health_run, "policy_preview", lambda _store: ({}, "", TOKEN))

    def installed(version="1.0"):
        def definition(wanted, owner, *, enabled):
            return health_jobs.installed_definition(
                slot=f"C:\\runtime\\versions\\{version}", python=f"C:\\runtime\\versions\\{version}\\Scripts\\python.exe",
                gui_python="C:\\Python\\pythonw.exe", identity=version[0] * 32 + ":" + "c" * 64,
                store="C:\\fixture\\store", policy_token=wanted["policy_token"], user_id=wanted["user_id"],
                windows_root="C:\\Windows", owner=owner, enabled=enabled,
            )
        return SimpleNamespace(
            root=tmp_path, slot=tmp_path / "versions" / version, runtime=runtime,
            identity=version[0] * 32 + ":" + "c" * 64, definition=definition,
        )

    selection = [installed()]
    owner = enrollment.Enrollment(
        selection[0], backend=native,
        selector=lambda _root, version=None: installed(version) if version else selection[0],
        qualifier=lambda *_args: None,
    )
    return owner, desired, native, refs, selection, installed


def adopt(owner, desired):
    preview = owner.configure(desired)
    return owner.configure(desired, apply=True, expected=preview["preview_token"])


def test_preview_is_inert_and_matching_convergence_does_not_reset_cadence(case):
    owner, desired, native, refs, *_ = case
    preview = owner.configure(desired)
    assert preview["status"] == "preview"
    assert not owner.path.exists() and not native.calls and not refs.owners
    result = owner.configure(desired, apply=True, expected=preview["preview_token"])
    assert result["status"] == "converged" and result["enabled"]
    assert native.calls == ["create", "enable"]
    assert refs.owners[enrollment.REFERENCE] == ["1.0"]
    native.calls.clear()
    assert owner.converge()["status"] == "unchanged"
    assert not native.calls


def test_no_local_opt_in_never_registers(case):
    owner, _, native, refs, *_ = case
    assert owner.converge()["status"] == "not-adopted"
    assert not owner.path.exists() and not native.calls and not refs.owners


def test_policy_account_and_preview_drift_refuse_before_native_effects(case, monkeypatch):
    owner, desired, native, refs, *_ = case
    preview = owner.configure(desired)
    with pytest.raises(ValueError, match="preview-changed"):
        owner.configure({**desired, "enabled": False}, apply=True, expected=preview["preview_token"])
    with pytest.raises(ValueError, match="account-mismatch"):
        owner.configure({**desired, "user_id": "S-1-5-21-1-2-3-1001"})
    identity = owner.owner
    owner.owner = "urn:copilot-extensions:health-job:" + "e" * 64
    with pytest.raises(ValueError, match="preview-changed"):
        owner.configure(desired, apply=True, expected=preview["preview_token"])
    owner.owner = identity
    monkeypatch.setattr(enrollment.health_run, "policy_preview", lambda _store: ({}, "", "d" * 64))
    with pytest.raises(ValueError, match="policy-changed"):
        owner.configure(desired, apply=True, expected=preview["preview_token"])
    assert not owner.path.exists() and not native.calls and not refs.owners


def test_foreign_task_is_not_adopted_or_overwritten(case):
    owner, desired, native, refs, _, installed = case
    native.xml = installed().definition(desired, owner.owner, enabled=False)
    with pytest.raises(ValueError, match="foreign-or-drifted"):
        adopt(owner, desired)
    assert not native.calls and not refs.owners and not owner.path.exists()


def test_changed_installation_identity_cannot_create_a_second_task_from_copied_state(case):
    owner, desired, native, refs, *_ = case
    adopt(owner, desired)
    owner.owner = "urn:copilot-extensions:health-job:" + "e" * 64
    native.xml = None
    native.calls.clear()
    with pytest.raises(ValueError, match="foreign-or-drifted"):
        owner.converge()
    assert not native.calls and refs.owners[enrollment.REFERENCE] == ["1.0"]


def test_replacement_retains_new_generation_and_disable_remove_preserve_store(case):
    owner, desired, native, refs, selection, installed = case
    Path(desired["store"]).mkdir()
    sentinel = Path(desired["store"]) / "retained-history"
    sentinel.write_text("unchanged")
    adopt(owner, desired)
    selection[0] = installed("2.0")
    native.calls.clear()
    assert owner.converge()["status"] == "converged"
    assert native.calls == ["disable", "replace", "enable"]
    assert refs.owners[enrollment.REFERENCE] == ["2.0"]
    assert owner.disable()["status"] == "disabled"
    assert not owner.read()["desired"]["enabled"]
    assert owner.converge(remove=True)["status"] == "removed"
    assert not native.xml and not refs.owners[enrollment.REFERENCE]
    assert sentinel.read_text() == "unchanged"


def test_failed_replacement_restores_known_good_definition_and_references(case):
    owner, desired, native, refs, selection, installed = case
    adopt(owner, desired)
    original = enrollment.signature(native.xml)
    selection[0] = installed("2.0")
    native.fail = "enable"
    result = owner.converge()
    assert result["status"] == "rolled-back" and result["rollback"] == "confirmed"
    assert enrollment.signature(native.xml) == original
    assert refs.owners[enrollment.REFERENCE] == ["1.0"]
    assert owner.read()["confirmed"]["version"] == "1.0"
    assert not owner.read()["pending"]


def test_recovery_refuses_cached_action_outside_its_qualified_runtime(case):
    owner, desired, native, _, selection, installed = case
    adopt(owner, desired)
    selection[0] = installed("2.0")
    call = native.call

    def lose_enable_reply(operation, **fields):
        if operation == "enable":
            native.refuse_read = True
            raise RuntimeError("reply unavailable")
        return call(operation, **fields)

    native.call = lose_enable_reply
    assert owner.converge()["status"] == "unknown"
    state = owner.read()
    before = state["pending"]["before"]
    root = ET.fromstring(before["definition"])
    root.find(f"{{{health_jobs.NS}}}Actions/{{{health_jobs.NS}}}Exec/{{{health_jobs.NS}}}Command").text = (
        r"C:\DifferentRuntime\pythonw.exe"
    )
    before["definition"] = ET.tostring(root, encoding="unicode")
    enrollment.slots._write(owner.path, state)
    native.call = call
    native.refuse_read = False
    calls = list(native.calls)
    with pytest.raises(ValueError, match="recovery-closure-changed"):
        owner.converge()
    assert native.calls == calls and not native.snapshot()["enabled"]
    assert owner.converge(remove=True)["status"] == "removed"


def test_support_loss_cannot_reenable_or_replace_incumbent(case):
    owner, desired, native, refs, *_ = case
    adopt(owner, desired)
    native.calls.clear()

    def unsupported(*_args):
        raise ValueError("installed-job-collection-unsupported")

    owner.selector = unsupported
    with pytest.raises(ValueError, match="unsupported"):
        owner.converge()
    assert not native.calls and refs.owners[enrollment.REFERENCE] == ["1.0"]
    # Safety withdrawal does not depend on collection support or policy readiness.
    assert owner.disable()["status"] == "disabled"
    assert owner.converge(remove=True)["status"] == "removed"


def test_failed_state_settlement_is_read_back_without_duplicate_registration(case, monkeypatch):
    owner, desired, native, refs, *_ = case
    write = enrollment.slots._write
    count = [0]

    def fail_settlement(path, value):
        count[0] += 1
        if count[0] == 2:
            raise OSError("disk unavailable")
        write(path, value)

    monkeypatch.setattr(enrollment.slots, "_write", fail_settlement)
    assert adopt(owner, desired)["status"] == "unknown"
    assert owner.read()["pending"] is not None
    assert refs.owners[enrollment.REFERENCE] == ["1.0"]
    calls = list(native.calls)
    assert owner.converge()["status"] == "recovered-candidate"
    assert native.calls == calls
    assert not owner.read()["pending"]


def test_native_utc_boundary_normalization_preserves_semantics(case):
    owner, desired, _, _, _, installed = case
    xml = installed().definition(desired, owner.owner, enabled=True)
    normalized = xml.replace("2000-01-01T00:00:15Z", "2000-01-01T00:00:15+00:00")
    assert enrollment.signature(normalized) == enrollment.signature(xml)
    changed = normalized.replace("2000-01-01T00:00:15+00:00", "2000-01-01T00:00:15+01:00")
    assert enrollment.signature(changed) != enrollment.signature(xml)


def test_resource_converges_only_existing_local_consent(case, monkeypatch):
    from agent_machines.resources import HANDLERS, ResolvedResource

    owner, _, native, refs, *_ = case
    monkeypatch.setattr(enrollment, "select", lambda: owner.installation)
    monkeypatch.setattr(enrollment, "Enrollment", lambda *_args: owner)
    handler = HANDLERS["health-job"]
    resolved = ResolvedResource("health-job", "health-job", ("health-job",), {"state": "present"}, [])
    monkeypatch.setenv("AGENT_RT_ROOT", str(owner.root))
    result = handler.apply(resolved, SimpleNamespace(home=owner.root), False)
    assert result.ok and not result.changed and not native.calls and not refs.owners


def test_native_omitted_enabled_default_can_be_disabled(case):
    owner, desired, _, _, _, installed = case
    root = ET.fromstring(installed().definition(desired, owner.owner, enabled=True))
    settings = root.find(f"{{{health_jobs.NS}}}Settings")
    settings.remove(settings.find(f"{{{health_jobs.NS}}}Enabled"))
    xml = ET.tostring(root, encoding="unicode")
    assert enrollment.signature(xml)["Settings/Enabled"] == "true"
    assert enrollment.signature(enrollment.disabled(xml))["Settings/Enabled"] == "false"


def test_scheduled_invocation_refuses_lost_support_before_launching_child(tmp_path, monkeypatch):
    from agent_machines import health_job
    from agent_procutil import owned_process

    environment = tmp_path / "runtime" / "versions" / "1.0" / ".venv"
    package = environment / "Lib" / "site-packages" / "agent_machines"
    package.mkdir(parents=True)
    (package / "health-collection-v1.json").write_text(json.dumps({
        "schema": "agent-machines.health.collection", "version": 1,
        "operations": ["preview", "apply"],
    }))
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(environment / "Scripts" / "python.exe"))
    monkeypatch.setattr(owned_process, "run_caller_owned", lambda *_args, **_kwargs: pytest.fail("child launched"))
    assert health_job.main(["--store", str(tmp_path / "store"), "--expect-policy", TOKEN]) == 2


def test_candidate_stays_retained_from_readiness_through_native_reference_settlement(case):
    owner, desired, native, _, *_ = case
    active = []

    @contextmanager
    def retain(_root, version):
        active.append(version)
        try:
            yield
        finally:
            active.pop()

    def qualify(installation, _desired):
        assert active == [installation.slot.name]

    call = native.call

    def guarded(operation, **fields):
        if operation != "read":
            assert active == ["1.0"]
        return call(operation, **fields)

    owner.runtime.retention_lease = retain
    owner.qualifier = qualify
    native.call = guarded
    assert adopt(owner, desired)["status"] == "converged"
    assert not active
