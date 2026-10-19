"""Installer activation uses real completed targets without cold-start effects."""

import json
import os
import subprocess
from contextlib import ExitStack, nullcontext
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs
from test_watch_control import _receiver
from test_watch_driver import _begin, _driver
from test_watch_installation import _policy
from test_watch_invocation import _PROBE, _probe, installed as installed  # noqa: F401
from agent_pull_requests import watch_install

pytestmark = pytest.mark.timeout(180)

_ACTIVATE = _PROBE[:_PROBE.index("print(json.dumps({")] + """
from agent_pull_requests.watch_install import activate
from agent_pull_requests.watch_invocation import WatchInvocation
target = WatchInvocation(installation, runtime, str(installation.receipt))
print(json.dumps(activate(target)))
"""


def test_installer_cold_activation_selects_actual_target_without_starting(installed):
    installation, runtime, _, _ = installed
    result = _probe(installed, script=_ACTIVATE)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "ready", "activated": True, "ownerStarted": False,
    }
    assert not installation.state_root.exists()
    assert not (installation.run_root / "active.json").exists()
    for name in ("current-version", "last-known-good"):
        assert (installation.plugin_root / name).read_text().strip() == runtime.runtime_version
    manifest = json.loads((installation.plugin_root / "deploy-manifest.json").read_text())
    assert manifest["source"]["version"] == runtime.payload_version
    assert manifest["venv"] == str(runtime.runtime_root)
    transaction = json.loads((installation.run_root / "watch-owner-transaction.json").read_text())
    assert transaction["phase"] == "complete" and transaction["successor"] is None
    assert (installation.run_root / "watch-owner-history" / f'{transaction["transactionId"]}.json').is_file()


def test_installer_refuses_actual_unattributed_writer_without_cold_selection(installed):
    installation = installed[0]
    with _receiver(installed, active=True):
        result = _probe(installed, script=_ACTIVATE)
        assert result.returncode != 0
        assert "AlreadyRunningError" in result.stderr
        assert not (installation.plugin_root / "current-version").exists()
        assert not (installation.run_root / "watch-owner-transaction.json").exists()


def test_installer_replay_keeps_actual_known_owner_and_terminal_custody(installed):
    installation = installed[0]
    with ExitStack() as children:
        with installation.transaction(timeout=1, environment={}) as guard:
            journal = _begin(installed, guard)
            driver = _driver(installed, journal, children)
            assert driver.run().ok
        before = (installation.plugin_root / "deploy-manifest.json").read_bytes()
        result = _probe(installed, script=_ACTIVATE)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["ownerStarted"] is False
        assert (installation.plugin_root / "deploy-manifest.json").read_bytes() == before
        assert driver.successor.health()["effectsEnabled"] is True
        current = json.loads((installation.run_root / "watch-owner-transaction.json").read_text())
        assert current["transactionId"] == journal.transaction_id
        assert current["successor"] == driver.successor.instance.evidence()


def test_installer_governance_refusal_never_selects_or_starts(installed):
    installation = installed[0]
    _policy(installation.os_profile, enabled=False)
    result = _probe(installed, script=_ACTIVATE)
    assert result.returncode != 0
    assert not (installation.plugin_root / "current-version").exists()
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


@pytest.mark.skipif(os.name != "nt", reason="POSIX physical invocation needs isolated OS account")
@pytest.mark.parametrize("identity", ["exact", "snapshot", "version"])
def test_actual_installer_module_requires_exact_running_target(installed, identity):
    installation, runtime, python, _ = installed
    environment = dict(os.environ, USERPROFILE=str(installation.os_profile))
    result = subprocess.run(
        [
            str(python), "-I", "-m", "agent_pull_requests.watch_install",
            "--context", str(installation.receipt),
            "--payload-root", str(installation.payload_root),
            "--snapshot-id", runtime.snapshot_id if identity != "snapshot" else "foreign",
            "--runtime-version", runtime.runtime_version if identity != "version" else "foreign",
        ],
        env=environment, capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    if identity == "exact":
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["activated"] is True
    else:
        assert result.returncode != 0 and "actual running slot" in result.stderr
        assert not (installation.plugin_root / "current-version").exists()
    assert not installation.state_root.exists()
    assert not (installation.run_root / "active.json").exists()


@pytest.mark.parametrize("failure", [
    TimeoutError("unscoped timeout"), PermissionError("identity changed"),
    RuntimeError("activation rolled back"),
])
def test_unscoped_activation_failures_never_reconcile(monkeypatch, failure):
    def activate(*_a, **_k):
        raise failure

    monkeypatch.setattr(watch_install, "activate", activate)
    monkeypatch.setattr(watch_install, "_reconcile_committed", lambda *_a, **_k: (
        pytest.fail("unscoped error must not reconcile")
    ))
    with pytest.raises(type(failure), match=str(failure)):
        watch_install.activate_with_reconciliation(object())


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True])
def test_installer_reconciliation_rejects_invalid_budget_before_actions(monkeypatch, timeout):
    monkeypatch.setattr(watch_install, "activate", lambda *_a, **_k: (
        pytest.fail("invalid budget reached activation")
    ))
    with pytest.raises(ValueError):
        watch_install.activate_with_reconciliation(object(), timeout=timeout)


def test_scoped_reconciliation_is_once_visible_and_keeps_original_deadline(monkeypatch, capsys):
    clock = [10.0]
    monkeypatch.setattr(watch_install, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    document = {key: {"comparison": key} for key in watch_install._INTENT_FIELDS}
    error = watch_install._PendingActivationTimeout(TimeoutError("original cutover timeout"), document)
    calls = []

    def activate(target, *, timeout):
        calls.append(("activate", target, timeout))
        clock[0] = 130
        raise error

    def recover(target, expected, *, deadline):
        calls.append(("recover", target, expected, deadline))
        assert "original cutover timeout" in capsys.readouterr().err
        raise TimeoutError("reconciliation failed")

    target = object()
    monkeypatch.setattr(watch_install, "activate", activate)
    monkeypatch.setattr(watch_install, "_reconcile_committed", recover)
    with pytest.raises(TimeoutError, match="reconciliation failed"):
        watch_install.activate_with_reconciliation(target)
    assert calls == [
        ("activate", target, 120.0), ("recover", target, document, 310.0),
    ]


def test_late_initial_ready_is_not_success_or_retry(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(watch_install, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def activate(*_a, **_k):
        clock[0] = 311
        return {"status": "ready"}

    monkeypatch.setattr(watch_install, "activate", activate)
    monkeypatch.setattr(watch_install, "_reconcile_committed", lambda *_a, **_k: (
        pytest.fail("late success must not create recovery authority")
    ))
    with pytest.raises(TimeoutError, match="installer activation deadline expired"):
        watch_install.activate_with_reconciliation(object())


@pytest.mark.parametrize("phase,successor", [
    ("prepared", {}), ("publishing", {}), ("committed", None),
    ("committed", {"instanceId": "exact"}), ("complete", {"instanceId": "exact"}),
])
def test_only_own_committed_nonnull_successor_timeout_is_scoped(monkeypatch, phase, successor):
    runtime = object()
    document = dict(
        transactionId="own", installation={}, target={}, successor=successor, phase=phase,
    )
    installation = SimpleNamespace(transaction=lambda **_k: nullcontext(object()))
    target = SimpleNamespace(
        require_current=lambda: None, fresh_installation=lambda: installation, runtime=runtime,
    )
    journal = SimpleNamespace(_current=lambda: document)
    monkeypatch.setattr(watch_install, "incumbent", lambda *_a, **_k: SimpleNamespace(
        instance=SimpleNamespace(runtime=object()),
    ))
    monkeypatch.setattr(watch_install, "_manifest", lambda *_a: b"fixture")
    monkeypatch.setattr(watch_install, "WatchJournal", SimpleNamespace(
        begin=lambda *_a, **_k: journal,
    ))
    monkeypatch.setattr(watch_install, "run_target", lambda *_a, **_k: (
        (_ for _ in ()).throw(TimeoutError("own attempt timeout"))
    ))
    with pytest.raises(TimeoutError, match="own attempt timeout") as caught:
        watch_install.activate(target)
    assert isinstance(caught.value, watch_install._PendingActivationTimeout) == (
        phase in {"committed", "complete"} and successor is not None
    )


def test_reconciliation_ready_after_original_caller_deadline_is_rejected(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(watch_install, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    document = {key: {} for key in watch_install._INTENT_FIELDS}

    def activate(*_a, **_k):
        raise watch_install._PendingActivationTimeout(TimeoutError("late activation"), document)

    def late_recovery(*_a, **_k):
        clock[0] = 311
        return {"status": "ready"}

    monkeypatch.setattr(watch_install, "activate", activate)
    monkeypatch.setattr(watch_install, "_reconcile_committed", late_recovery)
    with pytest.raises(TimeoutError, match="installer activation deadline expired"):
        watch_install.activate_with_reconciliation(object())


@pytest.mark.parametrize("field,value", [
    ("writerHeld", False), ("readReady", False), ("effectsEnabled", None),
    ("effectsEnabled", 0), ("effectsEnabled", 1),
])
def test_unconfirmed_status_refuses_before_controller_rotation(monkeypatch, field, value):
    installation = SimpleNamespace(
        evidence=lambda: {"installation": "exact"},
        transaction=lambda **_k: nullcontext(object()),
    )
    runtime = SimpleNamespace(evidence=lambda: {"runtime": "exact"})
    target = SimpleNamespace(
        installation=installation, runtime=runtime,
        require_current=lambda: None, fresh_installation=lambda: installation,
    )
    document = dict(
        transactionId="own", installation=installation.evidence(),
        target=runtime.evidence(), successor={"instanceId": "exact"}, phase="committed",
    )
    status = dict(writerHeld=True, readReady=True, effectsEnabled=True)
    status[field] = value
    monkeypatch.setattr(watch_install, "read_known_owner", lambda *_a, **_k: document)
    monkeypatch.setattr(watch_install, "_target_selection", lambda *_a: None)
    monkeypatch.setattr(watch_install, "_client", lambda *_a: SimpleNamespace(
        health=lambda **_k: status,
    ))
    monkeypatch.setattr(watch_install, "WatchJournal", SimpleNamespace(
        recover=lambda *_a, **_k: pytest.fail("unconfirmed status rotated controller custody"),
    ))
    expected = {key: document[key] for key in watch_install._INTENT_FIELDS}
    with pytest.raises(PermissionError, match="owner is unconfirmed"):
        watch_install._reconcile_committed(
            target, expected, deadline=watch_install.time.monotonic() + 300,
        )


@pytest.mark.parametrize("phase,damage", [
    ("committed", None), ("complete", None),
    ("complete", "transactionId"), ("complete", "target"), ("complete", "successor"),
    ("complete", "governance"), ("complete", "unreachable"),
    ("complete", "changed-after-status"),
])
def test_completed_prefix_reconciles_only_exact_live_committed_owner(
    installed, monkeypatch, phase, damage,
):
    """Inject a lost result; keep actual prefix, IPC, journal and native owner proof."""
    installation = installed[0]
    with ExitStack() as children:
        with installation.transaction(timeout=1, environment={}) as guard:
            journal = _begin(installed, guard)
            driver = _driver(installed, journal, children)
            if phase == "committed":
                with monkeypatch.context() as interrupted:
                    interrupted.setattr(journal, "finish", lambda **_k: (
                        (_ for _ in ()).throw(TimeoutError("fixture lost committed result"))
                    ))
                    with pytest.raises(TimeoutError, match="lost committed result"):
                        driver.run()
            else:
                assert driver.run().ok
        current_path = installation.run_root / "watch-owner-transaction.json"
        before = current_path.read_bytes()
        route = (installation.run_root / "active.json").read_bytes()
        if damage == "governance":
            _policy(installation.os_profile, enabled=False)
        if damage == "unreachable":
            children.close()
        script = _ACTIVATE[:_ACTIVATE.index("print(json.dumps(activate(target)))")] + f"""
from agent_pull_requests import watch_install
from agent_pull_requests.watch_journal import read_known_owner
document = read_known_owner(installation, environment={{}})
expected = watch_install._PendingActivationTimeout(TimeoutError("fixture lost result"), document)
damage = {damage!r}
if damage == "transactionId":
    expected.intent["transactionId"] = "changed"
elif damage == "target":
    expected.intent["target"]["runtimeVersion"] = "changed"
elif damage == "successor":
    expected.intent["successor"]["instanceId"] = "changed"
def lost_result(*args, **kwargs):
    raise expected
def no_spawn(*args, **kwargs):
    raise AssertionError("reconciliation must never launch or begin another owner")
watch_install.activate = lost_result
watch_install.run_target = no_spawn
watch_install.WatchJournal.begin = no_spawn
if damage == "changed-after-status":
    from agent_pull_requests.watch_private import write_private
    client = watch_install._client
    def changed_after_status(*args):
        owner = client(*args)
        health = owner.health
        def changed(**kwargs):
            result = health(**kwargs)
            replacement = dict(document, authorization="f" * 64, revision=document["revision"] + 1)
            write_private(installation.run_root / "watch-owner-transaction.json", replacement)
            return result
        owner.health = changed
        return owner
    watch_install._client = changed_after_status
print(json.dumps(watch_install.activate_with_reconciliation(target)))
"""
        result = _probe(installed, script=script)
        if damage is not None:
            assert result.returncode != 0
            if damage == "changed-after-status":
                assert "changed before controller rotation" in result.stderr
                assert json.loads(current_path.read_bytes()) == dict(
                    json.loads(before), authorization="f" * 64,
                    revision=json.loads(before)["revision"] + 1,
                )
            else:
                assert current_path.read_bytes() == before
        else:
            assert result.returncode == 0, result.stderr
            assert "fixture lost result" in result.stderr
            value = json.loads(result.stdout)
            assert value["reconciled"] and value["ownerStarted"] is False
            assert value["initialOutcome"] == "activation-timeout"
            current = json.loads(current_path.read_bytes())
            assert current["phase"] == "complete"
            assert current["transactionId"] == journal.transaction_id
            assert current["successor"] == driver.successor.instance.evidence()
            assert current["authorization"] != json.loads(before)["authorization"]
            assert driver.successor.health()["effectsEnabled"] is True
            assert (installation.run_root / "watch-owner-history" / f'{journal.transaction_id}.json').is_file()
        assert (installation.run_root / "active.json").read_bytes() == route
