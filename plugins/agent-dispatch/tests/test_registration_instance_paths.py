"""Registration cwd spelling cannot reject an otherwise valid admission root."""

from types import SimpleNamespace

import pytest
from agent_procutil.daemon_instances import (
    cancel_instance_reservation, inspect_instances, reserve_instance,
)

from agent_dispatch import supervisor_registration as registration


@pytest.mark.parametrize("selection", ["parent", "linked"])
def test_registration_root_uses_real_admission(tmp_path, monkeypatch, selection):
    state = tmp_path / "state"
    state.mkdir()
    launch = tmp_path / "launch"
    launch.mkdir()
    root = launch / ".." / "state"
    if selection == "linked":
        root = launch / "linked"
        try:
            root.symlink_to(state, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"native directory symlink unavailable: {error}")
    admitted = []
    process = SimpleNamespace()

    def spawn(command, *, instance, **kwargs):
        assert kwargs["cwd"] == str(instance.runtime_root)
        receipt = reserve_instance(instance)
        try:
            row = next(row for row in inspect_instances(receipt.registry_root)
                       if row["instance_id"] == receipt.instance_id)
            assert row["runtime_root"] == str(state) and row["state"] == "reserved"
            admitted.append(instance.runtime_root)
        finally:
            cancel_instance_reservation(receipt, creation_refused=True)
        return process

    monkeypatch.setattr(registration, "spawn_instance", spawn)
    launcher = registration.SubprocessLauncher(cwd=str(root))
    if selection == "linked":
        with pytest.raises((OSError, ValueError, RuntimeError)):
            launcher.launch({}, ["synthetic-registration"])
        assert admitted == []
    else:
        assert launcher.launch({}, ["synthetic-registration"]) is process
        assert admitted == [state]
