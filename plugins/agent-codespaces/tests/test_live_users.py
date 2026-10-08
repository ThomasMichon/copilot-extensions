"""Live-local-user probe: a CodeSpace is in use whenever a live process rides it,
regardless of whether the lease's recorded pid is still alive."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_codespaces import live_users as lu
from agent_codespaces.live_users import ProcInfo

NAME = "probable-space-x1y2z3"
CFG = f"/home/u/.ssh-manager/codespace-config/{NAME}.config"
SOCK = "/home/u/.ssh-manager/sockets/cs.probable-abc123"


def _master(pid=101, ppid=1, cfg=CFG):
    return ProcInfo(pid, ppid, (
        "ssh", "-F", cfg, "-o", f"ControlPath={SOCK}", "-o", "ControlMaster=yes",
        "-o", "ControlPersist=yes", "-N", "cs.host",
    ))


def _table(*procs):
    return [ProcInfo(1, 0, ("/sbin/init",)), *procs]


@pytest.fixture(autouse=True)
def _no_lock(monkeypatch):
    monkeypatch.setattr(lu, "lock_holder", lambda name, table=None: None)


def test_detached_control_master_is_a_live_user():
    users = lu.live_users(NAME, table=_table(_master()))
    assert [u.role for u in users] == [lu.ROLE_CONTROL_MASTER]
    assert f"ControlPath={SOCK}" in users[0].detail
    assert "detached" in users[0].detail


def test_roles_classified_and_transient_control_commands_ignored():
    table = _table(
        _master(),
        ProcInfo(102, 50, ("ssh", "-F", CFG, "-N", "-o", "ExitOnForwardFailure=yes",
                           "-L", "1:localhost:1", "cs.host")),
        ProcInfo(103, 50, ("ssh", "-F", CFG, "-o", f"ControlPath={SOCK}", "cs.host", "ls")),
        ProcInfo(104, 50, ("ssh", f"-F{CFG}", "cs.host", "ls")),
        ProcInfo(105, 50, ("ssh", "-F", CFG, "-o", f"ControlPath={SOCK}", "-O", "check", "x")),
        ProcInfo(50, 1, ("python",)),
    )
    roles = {u.pid: u.role for u in lu.live_users(NAME, table=table)}
    assert roles == {
        101: lu.ROLE_CONTROL_MASTER,
        102: lu.ROLE_FORWARD,
        103: lu.ROLE_MUX,
        104: lu.ROLE_SSH,
    }


def test_other_codespaces_and_unrelated_processes_do_not_count():
    other_cfg = "/home/u/.ssh-manager/codespace-config/other-space.config"
    table = _table(
        _master(cfg=other_cfg),
        ProcInfo(200, 1, ("gh", "codespace", "ssh", "-c", "other-space")),
        ProcInfo(201, 1, ("gh", "codespace", "list")),
        ProcInfo(202, 1, ("vim", CFG)),
    )
    assert lu.live_users(NAME, table=table) == []


def test_gh_codespace_session_counts_but_proxy_child_of_counted_ssh_does_not():
    table = _table(
        ProcInfo(300, 1, ("/usr/bin/gh", "cs", "ssh", "-c", NAME)),
    )
    assert [u.role for u in lu.live_users(NAME, table=table)] == [lu.ROLE_GH]

    proxied = _table(
        _master(),
        ProcInfo(301, 101, ("gh", "cs", "ssh", "-c", NAME, "--stdio")),
        ProcInfo(302, 1, ("gh", "codespace", "ssh", "--codespace", NAME, "--stdio")),
    )
    assert [u.pid for u in lu.live_users(NAME, table=proxied)] == [101]


def test_own_process_is_never_reported():
    me = ProcInfo(os.getpid(), 1, ("ssh", "-F", CFG, "cs.host", "true"))
    assert lu.live_users(NAME, table=_table(me)) == []


def test_lock_holder_listed_first_and_not_duplicated(monkeypatch):
    holder = lu.LiveUser(101, lu.ROLE_LOCK, "python -m bridge", "op=session-host")
    monkeypatch.setattr(lu, "lock_holder", lambda name, table=None: holder)
    users = lu.live_users(NAME, table=_table(_master(pid=101), _master(pid=102)))
    assert [(u.pid, u.role) for u in users] == [
        (101, lu.ROLE_LOCK), (102, lu.ROLE_CONTROL_MASTER),
    ]


def test_unreadable_process_table_degrades_to_lock_only(monkeypatch):
    monkeypatch.setattr(lu, "process_table", lambda: None)
    assert lu.live_users(NAME) == []
    assert lu.codespaces_in_use([NAME]) == {}


def test_codespaces_in_use_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("ps exploded")
    monkeypatch.setattr(lu, "process_table", boom)
    assert lu.codespaces_in_use([NAME]) == {}


def test_real_lock_holder_is_reported(monkeypatch, tmp_path):
    """The actual ssh_manager TargetLock holder (another live pid) is a user."""
    monkeypatch.undo()
    import ssh_manager.locks as locks

    monkeypatch.setattr(locks, "locks_dir", lambda: tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tmp_path / f"{NAME}.lock").write_text(json.dumps({
            "pid": child.pid, "op": "session-host", "target": NAME,
            "started_at": time.time() - 5,
        }))
        holder = lu.lock_holder(NAME, table=[])
        assert holder is not None and holder.pid == child.pid
        assert holder.role == lu.ROLE_LOCK and "op=session-host" in holder.detail
    finally:
        child.kill()
        child.wait()
    assert lu.lock_holder(NAME, table=[]) is None  # dead holder -> not a user


def test_claim_orphaned_requires_no_live_users(tmp_path):
    gone = str(tmp_path / "gone")
    user = lu.LiveUser(1, lu.ROLE_CONTROL_MASTER, "ssh")
    assert lu.claim_orphaned(gone, []) is True
    assert lu.claim_orphaned(gone, [user]) is False
    assert lu.claim_orphaned(str(tmp_path), []) is False


def test_busy_report_names_the_holder(monkeypatch):
    master = lu.LiveUser(101, lu.ROLE_CONTROL_MASTER, "ssh -F x", f"ControlPath={SOCK}")
    monkeypatch.setattr(lu, "live_users", lambda name, table=None: [master])
    msg = lu.busy_report(NAME, "SSH target is busy: held by pid 9")
    assert msg.startswith("[BUSY] SSH target is busy")
    assert "pid 101 [ssh-control-master]" in msg and SOCK in msg
    assert "ssh -O exit" in msg and f"in-use {NAME}" in msg


def test_cmd_in_use_exit_codes_and_json(monkeypatch, capsys):
    monkeypatch.setattr(lu, "process_table", lambda: _table(_master()))
    assert lu.cmd_in_use(SimpleNamespace(name=NAME, json_output=True)) == 75
    out = json.loads(capsys.readouterr().out)
    assert out["in_use"] is True and out["live_users"][0]["pid"] == 101
    assert lu.cmd_in_use(SimpleNamespace(name="idle-box", json_output=False)) == 0
    assert "not in use" in capsys.readouterr().out


def test_in_use_cli_wired(monkeypatch, capsys):
    from agent_codespaces.__main__ import main

    monkeypatch.setattr(lu, "process_table", lambda: _table(_master()))
    assert main(["in-use", NAME, "--json"]) == 75
    assert json.loads(capsys.readouterr().out)["in_use"] is True


def test_pool_box_with_live_user_is_in_use_and_claim_not_orphaned(tmp_path):
    from agent_codespaces import pool
    from agent_codespaces.lease import Lease
    from agent_codespaces.lifecycle import CodespaceInfo

    cs = CodespaceInfo(
        name=NAME, display_name="", repository="o/r", branch="main",
        state="Available", machine="basicLinux32gb", last_used_at="", account="",
    )
    gone = str(tmp_path / "gone-worktree")
    lease = Lease(NAME, "", 999999, "h", 0.0, time.time(), worktree=gone)
    user = lu.LiveUser(101, lu.ROLE_CONTROL_MASTER, "ssh")
    common = dict(codespaces=[cs], markers={}, l2_leases={}, clean_records={})

    (m,), _ = pool.build_pool(leases=[lease], live={NAME: [user]}, **common)
    assert m.orphaned is False and m.to_dict()["live_users"][0]["pid"] == 101
    (m,), _ = pool.build_pool(leases=[lease], live={}, **common)
    assert m.orphaned is True
    (m,), _ = pool.build_pool(leases=[], live={NAME: [user]}, **common)
    assert m.disposition == pool.IN_USE
