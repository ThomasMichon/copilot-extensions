"""Actor-free enrollment controls; native task/installed closure has its own lane."""

from contextlib import contextmanager, nullcontext
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import subprocess
import time
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
        self.instances = []
        self.settle_instances = True

    def snapshot(self):
        configured = self.xml is not None
        return {
            "configured": configured, "xml": self.xml,
            "digest": hashlib.sha256(self.xml.encode()).hexdigest() if configured else "absent",
            "enabled": configured and enrollment.signature(self.xml)["Settings/Enabled"] == "true",
            "instances": list(self.instances), "account_sid": SID, "state": 3,
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
            if self.settle_instances:
                self.instances.clear()
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


def test_failed_removal_never_reenables_and_later_convergence_finishes_withdrawal(case):
    owner, desired, native, refs, *_ = case
    adopt(owner, desired)
    native.calls.clear()
    native.fail = "remove"
    result = owner.converge(remove=True)
    assert result["status"] == "unknown" and not result["enabled"]
    assert owner.read()["pending"]["candidate"] is None
    assert refs.owners[enrollment.REFERENCE] == ["1.0"]
    assert "enable" not in native.calls
    assert owner.converge()["status"] == "removed"
    assert not native.xml and not refs.owners[enrollment.REFERENCE]
    assert "enable" not in native.calls


def test_repeated_removal_is_noop_only_after_references_are_settled(case):
    owner, desired, native, refs, *_ = case
    adopt(owner, desired)
    assert owner.converge(remove=True)["changed"]
    native.calls.clear()
    assert owner.converge(remove=True)["changed"] is False
    assert not native.calls and refs.pending is None
    refs.owners[enrollment.REFERENCE] = ["1.0"]
    assert owner.converge(remove=True)["changed"] is True
    assert not refs.owners[enrollment.REFERENCE]


def test_failed_disabled_configuration_does_not_restore_enabled_incumbent(case):
    owner, desired, native, _, selection, installed = case
    adopt(owner, desired)
    selection[0] = installed("2.0")
    native.calls.clear()
    native.fail = "replace"
    result = adopt(owner, {**desired, "enabled": False})
    assert result["status"] == "rolled-back" and not result["enabled"]
    assert "enable" not in native.calls
    assert owner.read()["desired"]["enabled"] is False


def test_interrupted_disable_recovers_without_replacement_or_enable(case):
    owner, desired, native, _, *_ = case
    adopt(owner, desired)
    call = native.call

    def lose_disable_reply(operation, **fields):
        result = call(operation, **fields)
        if operation == "disable":
            raise RuntimeError("reply unavailable")
        return result

    native.call = lose_disable_reply
    assert owner.disable()["status"] == "unknown"
    native.call = call
    native.calls.clear()
    assert owner.converge()["status"] == "disabled"
    assert native.calls == ["disable"] and not native.snapshot()["enabled"]
    native.calls.clear()
    assert owner.converge()["status"] == "unchanged"
    assert not native.calls


def test_initial_disabled_enrollment_failure_does_not_claim_success(case):
    owner, desired, native, _, *_ = case
    native.fail = "create"
    result = adopt(owner, {**desired, "enabled": False})
    assert result["status"] == "rolled-back" and result["rollback"] == "confirmed"
    assert not result["configured"] and not result["adopted"]


@pytest.mark.parametrize("withdrawal", ["disable", "remove"])
def test_unsettled_enable_blocks_withdrawal_until_original_cleanup_settles(case, withdrawal):
    from agent_procutil.owned_process import OwnedCleanupError

    owner, desired, native, _, selection, installed = case
    adopt(owner, desired)
    selection[0] = installed("2.0")
    call = native.call
    cleanup = OwnedCleanupError(None, cleanup_retry=lambda _deadline: None)

    def unconfirmed_enable(operation, **fields):
        if operation == "enable":
            raise cleanup
        return call(operation, **fields)

    native.call = unconfirmed_enable
    assert owner.converge()["status"] == "unknown"
    assert owner.native_cleanup_error is cleanup
    assert owner.read()["pending"]["native_unsettled"]
    native.call = call
    native.calls.clear()
    action = owner.disable if withdrawal == "disable" else lambda: owner.converge(remove=True)
    for action in (action, owner.converge):
        with pytest.raises(ValueError, match="cleanup-unconfirmed"):
            action()
    assert owner.read()["pending"]["intent"] == withdrawal
    successor = enrollment.Enrollment(
        installed(), backend=native, selector=owner.selector, qualifier=owner.qualifier,
    )
    with pytest.raises(ValueError, match="cleanup-unconfirmed"):
        successor.converge()
    assert not native.calls
    # The retained native mutator can still finish late before its cleanup.
    call("enable", expected=native.snapshot()["digest"])
    assert native.snapshot()["enabled"]
    cleanup.retry_cleanup(deadline=time.monotonic() + 5)
    assert not owner.read()["pending"]["native_unsettled"]
    assert owner.converge()["status"] == ("disabled" if withdrawal == "disable" else "removed")
    assert not native.snapshot()["enabled"]


def test_confirmed_disabled_config_overrides_pending_enabled_replacement(case):
    owner, desired, native, _, selection, installed = case
    adopt(owner, desired)
    selection[0] = installed("2.0")
    # Fail before any native effect and lose the immediate readback too.
    call = native.call

    def interrupt(operation, **fields):
        if operation == "disable":
            native.refuse_read = True
            raise RuntimeError("replacement interrupted")
        return call(operation, **fields)

    native.call = interrupt
    assert owner.converge()["status"] == "unknown"
    native.refuse_read = False
    native.call = call
    native.calls.clear()
    result = adopt(owner, {**desired, "enabled": False})
    assert result["status"] == "disabled" and not result["enabled"]
    assert native.calls == ["disable"]


@pytest.mark.parametrize("surface", ["cli", "resource"])
@pytest.mark.parametrize("confirmed", [False, True])
def test_boundaries_retain_native_owner_through_bounded_cleanup(case, monkeypatch, capsys, surface, confirmed):
    from agent_machines import cli_health
    from agent_machines.resources import HANDLERS, ResolvedResource
    from agent_procutil.owned_process import OwnedCleanupError

    owner, desired, native, _, selection, installed = case
    adopt(owner, desired)
    selection[0] = installed("2.0")
    call = native.call
    attempts = []

    def retry(deadline):
        assert time.monotonic() < deadline <= time.monotonic() + 10
        attempts.append(deadline)
        if not confirmed:
            raise RuntimeError("cleanup still unconfirmed")

    cleanup = OwnedCleanupError(None, cleanup_retry=retry)

    def interrupt(operation, **fields):
        if operation == "enable":
            raise cleanup
        return call(operation, **fields)

    native.call = interrupt
    monkeypatch.setattr(enrollment, "select", lambda **_fields: owner.installation)
    monkeypatch.setattr(enrollment, "Enrollment", lambda *_args: owner)
    monkeypatch.setenv("AGENT_RT_ROOT", str(owner.root))
    if surface == "cli":
        assert cli_health.run_job(SimpleNamespace(job_operation="converge")) == 2
        capsys.readouterr()
    else:
        result = HANDLERS["health-job"].apply(
            ResolvedResource("health-job", "health-job", ("health-job",), {"state": "present"}),
            SimpleNamespace(home=owner.root), False,
        )
        assert not result.ok
    assert len(attempts) == 1
    assert owner.read()["pending"]["native_unsettled"] is not confirmed
    native.call = call
    if confirmed:
        assert owner.native_cleanup_error is None
        assert owner.disable()["status"] == "disabled"
    else:
        assert owner.native_cleanup_error is cleanup
        with pytest.raises(ValueError, match="cleanup-unconfirmed"):
            owner.disable()


@pytest.mark.parametrize("after_effect", [False, True])
def test_removal_supersedes_pending_replacement_before_any_effect(case, after_effect):
    owner, desired, native, refs, selection, installed = case
    adopt(owner, desired)
    selection[0] = installed("2.0")
    call = native.call

    def interrupt_enable(operation, **fields):
        if operation == "enable":
            native.refuse_read = True
            raise RuntimeError("interrupted replacement")
        return call(operation, **fields)

    native.call = interrupt_enable
    assert owner.converge()["status"] == "unknown"
    native.refuse_read = False

    def interrupt_remove(operation, **fields):
        if operation == "remove":
            assert owner.read()["pending"]["intent"] == "remove"
            if after_effect:
                call(operation, **fields)
            raise RuntimeError("interrupted withdrawal")
        return call(operation, **fields)

    native.call = interrupt_remove
    with pytest.raises(RuntimeError, match="interrupted withdrawal"):
        owner.converge(remove=True)
    assert owner.read()["pending"]["intent"] == "remove"
    native.call = call
    native.calls.clear()
    assert owner.converge()["status"] == "removed"
    assert not native.xml and "enable" not in native.calls
    assert not refs.owners[enrollment.REFERENCE]


@pytest.mark.parametrize("after_effect", [False, True])
def test_disable_recovery_preserves_intent_without_readiness_and_requires_settlement(case, after_effect):
    owner, desired, native, refs, *_ = case
    adopt(owner, desired)
    call = native.call

    def interrupt_disable(operation, **fields):
        if operation == "disable":
            if after_effect:
                call(operation, **fields)
            raise RuntimeError("interrupted disable")
        return call(operation, **fields)

    native.call = interrupt_disable
    assert owner.disable()["status"] == "unknown"
    assert owner.read()["pending"]["intent"] == "disable"
    owner._policy = owner.qualifier = lambda *_args: pytest.fail("disable required collection readiness")
    native.call = call
    native.instances = ["owned-instance"]
    native.settle_instances = False
    with pytest.raises(ValueError, match="settlement-unconfirmed"):
        owner.converge()
    assert owner.read()["pending"]["intent"] == "disable"
    assert refs.owners[enrollment.REFERENCE] == ["1.0"]
    native.settle_instances = True
    native.calls.clear()
    assert owner.converge()["status"] == "disabled"
    assert native.calls == ["disable"] and not native.snapshot()["instances"]
    assert owner.read()["desired"]["enabled"] is False


@pytest.mark.parametrize("kind", ["timeout", "cleanup"])
def test_cli_and_resource_subprocess_failures_preserve_recovery_state(case, monkeypatch, capsys, kind):
    from agent_machines import cli_health
    from agent_machines.resources import HANDLERS, ResolvedResource
    from agent_procutil.owned_process import OwnedCleanupError

    owner, desired, native, *_ = case
    adopt(owner, desired)
    native.fail = "remove"
    assert owner.converge(remove=True)["status"] == "unknown"
    before = owner.path.read_bytes()

    def fail(*_args, **_fields):
        if kind == "timeout":
            raise subprocess.TimeoutExpired(["fixture"], 1)
        raise OwnedCleanupError(None)

    monkeypatch.setattr(owner, "converge", fail)
    monkeypatch.setattr(enrollment, "select", lambda **_fields: owner.installation)
    monkeypatch.setattr(enrollment, "Enrollment", lambda *_args: owner)
    monkeypatch.setenv("AGENT_RT_ROOT", str(owner.root))
    result = HANDLERS["health-job"].apply(
        ResolvedResource("health-job", "health-job", ("health-job",), {"state": "present"}),
        SimpleNamespace(home=owner.root), False,
    )
    assert not result.ok
    assert cli_health.run_job(SimpleNamespace(job_operation="converge")) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "job-lifecycle-unavailable"
    assert owner.path.read_bytes() == before


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
        if value["pending"] is None and not count[0]:
            count[0] += 1
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


@pytest.mark.parametrize("location", ["legacy", "selected", "cell"])
@pytest.mark.parametrize("strategy", ["enforce", "ensure-present", "managed-block"])
def test_generic_file_resources_cannot_adopt_local_collection(tmp_path, monkeypatch, location, strategy):
    from agent_machines.resources import HANDLERS, ResolvedResource

    home = tmp_path / "home"
    selected = tmp_path / "selected-install"
    monkeypatch.setenv("AGENT_RT_ROOT", str(selected))
    roots = {
        "legacy": home / ".agent-machines",
        "selected": selected,
        "cell": home / ".copilot-extensions" / "marketplaces" / "example" / "plugins" / "agent-machines",
    }
    target = roots[location] / enrollment.STATE
    declaration = {
        "path": str(target), "format": "json", "strategy": strategy,
        "content": {"schema": enrollment.SCHEMA, "desired": {"enabled": True}},
    }
    result = HANDLERS["file"].apply(
        ResolvedResource("file", "attempted-consent", (str(target),), declaration),
        SimpleNamespace(home=home, repo_paths={}), False,
    )
    assert result.status == "blocked" and not target.exists()
    ordinary = tmp_path / "ordinary.json"
    result = HANDLERS["file"].apply(
        ResolvedResource("file", "ordinary", (str(ordinary),), {
            "path": str(ordinary), "format": "json", "strategy": "enforce",
            "content": {"ordinary": True},
        }),
        SimpleNamespace(home=home, repo_paths={}), False,
    )
    assert result.ok and json.loads(ordinary.read_text()) == {"ordinary": True}


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
