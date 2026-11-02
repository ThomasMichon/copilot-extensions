"""Engine repair explicitly exports live driver custody to its native child."""

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import update_driver

from worktree_manager import update_bootstrap


@pytest.mark.parametrize("scoped", [False, True])
def test_repair_exports_custody_before_either_publication_boundary(
    tmp_path, monkeypatch, scoped,
):
    events = []
    witness = {"UPDATE_DRIVER_WITNESS": "synthetic-export"}

    def export():
        events.append("export")
        return witness

    @contextmanager
    def lease(**kwargs):
        events.append("lease")
        yield {"PUBLICATION_FIXTURE": "scoped"}

    installation = update_bootstrap._Installation(
        tmp_path / "runtime", tmp_path / "payload", {}, tmp_path / "sdk",
        context_arguments={} if scoped else None,
        primitive=SimpleNamespace(runtime_publication_lease=lease) if scoped else None,
    )
    environment = {"UNCHANGED": "value"}
    monkeypatch.setattr(update_driver, "participant_environment", export)

    def wait(command, child, cwd):
        events.append("child")
        assert child["UPDATE_DRIVER_WITNESS"] == witness["UPDATE_DRIVER_WITNESS"]
        assert child["UNCHANGED"] == "value"
        return 17

    monkeypatch.setattr(update_bootstrap, "_wait", wait)
    assert update_bootstrap._invoke(installation, "update", environment) == 17
    assert events == (["export", "lease", "child"] if scoped else ["export", "child"])
    assert environment == {"UNCHANGED": "value"}


def test_repair_lost_custody_refuses_before_publication_or_child(tmp_path, monkeypatch):
    def lost():
        raise update_driver.ParticipantError("participant owner lost custody")

    def forbidden(*args, **kwargs):
        pytest.fail("lost custody reached publication or child execution")

    installation = update_bootstrap._Installation(
        tmp_path / "runtime", tmp_path / "payload", {}, tmp_path / "sdk",
        context_arguments={},
        primitive=SimpleNamespace(runtime_publication_lease=forbidden),
    )
    monkeypatch.setattr(update_driver, "participant_environment", lost)
    monkeypatch.setattr(update_bootstrap, "_wait", forbidden)
    with pytest.raises(update_driver.ParticipantError, match="lost custody"):
        update_bootstrap._invoke(installation, "update", {})


@pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="real IPC and subprocess handoff; not promotion-unit evidence",
)
def test_consumed_witness_reaches_real_worktrees_installer_participant(tmp_path, monkeypatch):
    from single_instance_lease.operation import OperationLock
    from update_driver.participant import Witness

    repo = Path(__file__).resolve().parents[2]
    root = tmp_path / "driver"
    root.mkdir()
    payload = tmp_path / "payload"
    payload.mkdir()
    sdk = tmp_path / "sdk"
    (sdk / "scripts").mkdir(parents=True)
    # Probe the shipped participant in a real repair child, without installing.
    (sdk / "scripts/command_binding.py").write_text(
        "import os,runpy,sys\n"
        f"sys.path[:0] = {list(map(str, sys.path))!r}\n"
        "from update_driver import core\n"
        f"core.default_root = lambda: __import__('pathlib').Path({str(root)!r})\n"
        f"probe = runpy.run_path({str(repo / 'plugins/agent-worktrees/scripts/runtime_install_driver.py')!r})\n"
        "with probe['custody'](required=True) as owner:\n"
        "    owner.assert_owned()\n"
        "    assert 'UPDATE_DRIVER_WITNESS' not in os.environ\n"
        "sys.exit(17)\n",
        encoding="utf-8",
    )
    installation = update_bootstrap._Installation(
        tmp_path / "runtime", payload, {}, sdk,
    )
    key = "UPDATE_DRIVER_WITNESS"
    with open(os.devnull, "rb") as stdin, OperationLock(root / "execution.lock"):
        monkeypatch.setattr(sys, "stdin", stdin)
        witness = Witness(root, "synthetic-request")
        try:
            monkeypatch.setenv(key, witness.environment()[key])
            with update_driver.borrowed_custody(root=root) as custody:
                assert key not in os.environ
                assert update_bootstrap._invoke(
                    installation, "update", dict(os.environ),
                ) == 17
                custody.assert_owned()
                assert key not in os.environ
        finally:
            witness.close()
    assert update_driver.participant_environment() == {}
