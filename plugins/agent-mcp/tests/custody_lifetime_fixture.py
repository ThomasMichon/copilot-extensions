"""Finite native lifetime actors; only the existing hosted test owner admits them."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import agent_procutil as pu
from agent_procutil._daemon_instance_identity import birth_identity


def wait_for(path):
    deadline = time.monotonic() + 40
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("native lifetime actor release did not arrive")
        time.sleep(.02)


def main():
    mode, actor, root, fixed, config, private = sys.argv[1:7]
    area = Path(private)
    if mode == "owner":
        birth = birth_identity(os.getpid())
        with subprocess.Popen(
            [sys.executable, "-I", __file__, "shim", actor, root, fixed, config, private,
             str(os.getpid()), birth],
            **pu.no_window_kwargs(),
        ) as shim:
            area.joinpath("owner.json").write_text(
                json.dumps({"pid": os.getpid(), "birth": birth}), encoding="utf-8",
            )
            if shim.wait(timeout=40) != 0:
                raise RuntimeError("native lifetime shim failed")
        area.joinpath("shim-exited").write_text("settled", encoding="ascii")
        wait_for(area / "owner-release")
        return
    if mode == "shim":
        child = subprocess.Popen(
            [sys.executable, "-I", actor, "conduit-launcher", root, fixed, fixed, config,
             *sys.argv[7:9]],
            **pu.no_window_kwargs(),
        )
        area.joinpath("conduit.json").write_text(
            json.dumps({"pid": child.pid, "birth": birth_identity(child.pid)}), encoding="utf-8",
        )
        wait_for(area / "shim-release")
        if child.poll() is not None:
            raise RuntimeError("conduit exited before its interposed parent")
        return
    if mode == "nested":
        with (area / "nested.log").open("wb") as log:
            child, job = pu.spawn_sync_in_kill_on_close_job(
                [sys.executable, "-I", __file__, "refused", actor, root, fixed, config, private],
                require_job=True, allow_breakaway=True,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **pu.no_window_kwargs(),
            )
        try:
            if child.wait(timeout=30) != 0:
                raise RuntimeError("restrictive outer refusal actor failed")
        finally:
            job.close()
        return

    from agent_mcp import incoming, sockio
    from agent_mcp.custody_launch import attach

    try:
        connection = attach(fixed, config, incoming.ensure_current(fixed))
    except pu.DaemonSpawnError as error:
        if mode != "refused":
            raise
        area.joinpath("refused.json").write_text(json.dumps({
            "cleanup_confirmed": error.cleanup_confirmed,
            "admission_sent": error.admission_sent,
        }), encoding="utf-8")
        return
    if mode == "refused":
        sockio._close(connection)
        raise RuntimeError("restrictive caller Job admitted an escaping custodian")
    try:
        connection.sendall(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        reply = json.loads(sockio._read_ack_line(connection, deadline=time.monotonic() + 10))
        control = subprocess.Popen(
            [sys.executable, "-I", "-c", "import time; time.sleep(120)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **pu.no_window_kwargs(),
        )
        area.joinpath("setup.json").write_text(json.dumps({
            "upstream": reply["result"]["pid"], "control": control.pid,
            "control_birth": birth_identity(control.pid),
        }), encoding="utf-8")
        wait_for(area / "setup-release")
    finally:
        sockio._close(connection)


if __name__ == "__main__":
    main()
