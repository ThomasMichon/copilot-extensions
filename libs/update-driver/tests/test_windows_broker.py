"""Broker adoption borrows the real driver owner, never a second mutation lock."""

import os
from contextlib import nullcontext
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from update_driver import RequestSpec, TrustedRunner, drain, enqueue, status
from update_driver import core, windows_broker
from update_driver.participant import ENVIRONMENT_KEY, ParticipantError, borrowed_custody

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows broker adoption adapter")


@pytest.fixture
def adapter(area, monkeypatch):
    from agent_procutil import windows_broker_installation as installation
    from agent_procutil import windows_task_broker as broker

    root, runtime = area / "driver", area / "installed"
    runtime.mkdir()
    desired = SimpleNamespace(config_path=runtime / "windows-launch-broker.json")
    factory, recover, reconcile = Mock(return_value=desired), Mock(), Mock()
    monkeypatch.setattr(installation, "registration", factory)
    monkeypatch.setattr(broker, "recover", recover)
    monkeypatch.setattr(broker, "reconcile", reconcile)
    monkeypatch.delenv(ENVIRONMENT_KEY, raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    return SimpleNamespace(
        root=root, runtime=runtime, slot=runtime / "versions/1.0.0",
        desired=desired, factory=factory, recover=recover, reconcile=reconcile,
    )


def test_adoption_without_actual_custody_has_no_installation_effects(adapter):
    with pytest.raises(core.StateError, match="live installation custody"):
        windows_broker.adopt(adapter.runtime, adapter.slot, root=adapter.root)
    adapter.factory.assert_not_called()
    adapter.recover.assert_not_called()
    adapter.reconcile.assert_not_called()
    assert not adapter.root.exists()


def test_ambient_but_invalid_witness_is_not_permission(adapter, monkeypatch):
    monkeypatch.setenv(ENVIRONMENT_KEY, '{"version":1}')
    with pytest.raises(ParticipantError):
        windows_broker.adopt(adapter.runtime, adapter.slot, root=adapter.root)
    adapter.factory.assert_not_called()
    assert not adapter.root.exists()


def test_contained_adoption_refuses_before_reading_installation(adapter, monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    with pytest.raises(core.StateError, match="contained"):
        windows_broker.adopt(adapter.runtime, adapter.slot, root=adapter.root)
    adapter.factory.assert_not_called()
    adapter.recover.assert_not_called()
    adapter.reconcile.assert_not_called()


def test_fixed_installer_entry_preserves_live_custody_requirement(adapter, capsys):
    with pytest.raises(core.StateError, match="live installation custody"):
        windows_broker.main([
            "--runtime-root", str(adapter.runtime), "--slot", str(adapter.slot),
            "--execution-root", str(adapter.root),
        ])
    adapter.factory.assert_not_called()
    adapter.reconcile.assert_not_called()
    assert not adapter.root.exists()
    assert not capsys.readouterr().out


@pytest.mark.parametrize("missing", ["--runtime-root", "--slot", "--execution-root"])
def test_fixed_installer_entry_requires_explicit_paths(adapter, missing):
    options = {
        "--runtime-root": str(adapter.runtime), "--slot": str(adapter.slot),
        "--execution-root": str(adapter.root),
    }
    with pytest.raises(SystemExit) as error:
        windows_broker.main([part for flag, path in options.items() if flag != missing
                             for part in (flag, path)])
    assert error.value.code == 2
    adapter.factory.assert_not_called()
    adapter.reconcile.assert_not_called()


def test_fixed_installer_entry_uses_same_real_transaction(adapter, monkeypatch, capsys):
    request = enqueue(
        RequestSpec("broker-adoption-entry-fixture"),
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(adapter.runtime)), root=adapter.root,
    )

    def executor(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        return windows_broker.main([
            "--runtime-root", str(adapter.runtime), "--slot", str(adapter.slot),
            "--execution-root", str(adapter.root),
        ])

    assert drain(root=adapter.root, executor=executor) == 0
    assert status(request.id, root=adapter.root).state == "completed"
    adapter.factory.assert_called_once_with(adapter.runtime, adapter.slot)
    adapter.recover.assert_called_once_with(adapter.desired)
    adapter.reconcile.assert_called_once_with(adapter.desired, previous=None)
    assert json.loads(capsys.readouterr().out) == {"adopted": True}
    assert ENVIRONMENT_KEY not in os.environ


@pytest.mark.parametrize("already_scoped", [False, True])
@pytest.mark.parametrize("existing", ["absent", "same", "previous"])
def test_real_driver_custody_covers_recovery_and_reconciliation(
    adapter, monkeypatch, already_scoped, existing,
):
    from agent_procutil import windows_broker_installation as installation
    from single_instance_lease.operation import OperationLock, OperationLockedError

    events = []
    previous = adapter.desired if existing == "same" else object()
    loaded = Mock(return_value=previous)
    monkeypatch.setattr(installation, "load", loaded)
    if existing != "absent":
        adapter.desired.config_path.touch()
    request = enqueue(
        RequestSpec("broker-adoption-fixture"),
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(adapter.runtime)), root=adapter.root,
    )

    def executor(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        outer = borrowed_custody(root=adapter.root) if already_scoped else nullcontext()
        with outer:
            def reconcile(config, **kwargs):
                transaction.assert_owned()
                contender = OperationLock(adapter.root / "execution.lock")
                with pytest.raises(OperationLockedError):
                    contender.acquire()
                events.append("reconcile")
                assert config is adapter.desired
                assert kwargs["previous"] is (previous if existing != "absent" else None)

            adapter.recover.side_effect = lambda config: events.append("recover")
            adapter.reconcile.side_effect = reconcile
            assert windows_broker.adopt(
                adapter.runtime, adapter.slot, root=adapter.root,
            ) is adapter.desired
        return 0

    assert drain(root=adapter.root, executor=executor) == 0
    assert status(request.id, root=adapter.root).state == "completed"
    assert events == ["recover", "reconcile"]
    assert ENVIRONMENT_KEY not in os.environ
    assert loaded.call_count == (existing != "absent")


def test_failed_adoption_keeps_canonical_interrupted_fence(adapter, monkeypatch):
    request = enqueue(
        RequestSpec("broker-adoption-fixture"),
        runner=TrustedRunner((sys.executable, "-c", "pass"), str(adapter.runtime)), root=adapter.root,
    )
    adapter.reconcile.side_effect = OSError("fixture adoption failure")

    def executor(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        windows_broker.adopt(adapter.runtime, adapter.slot, root=adapter.root)
        return 0

    with pytest.raises((OSError, ParticipantError)):
        drain(root=adapter.root, executor=executor)
    result = status(request.id, root=adapter.root)
    assert result.state == "interrupted" and result.unresolved
    with pytest.raises(core.InterruptedError):
        enqueue(
            RequestSpec("broker-adoption-fixture"),
            runner=TrustedRunner((sys.executable, "-c", "pass"), str(adapter.runtime)),
            root=adapter.root,
        )
