"""Finite, isolated real dispatch servers for the Windows cutover regression."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from zdd.diagnostics import process_start_time, terminate_pid_if_identity


def publish(path, payload):
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(payload))
    pending.replace(path)


def reap_fixture_processes(owned, *, keep_pids, list_procs, reap_fn):
    """Never signal a host process merely because it reused a fixture PID."""
    expected = dict(owned)

    def identified():
        return [
            proc for proc in list_procs()
            if expected.get(proc.pid)
            and process_start_time(proc.pid) == expected[proc.pid]
        ]

    def terminate(pid):
        token = expected.get(pid)
        if not token:
            return False
        # Re-check through the shared native handle at the destructive boundary,
        # not only before enumeration: the candidate can exit/reuse in between.
        return bool(terminate_pid_if_identity(pid, token).get("killed"))

    return reap_fn(keep_pids=keep_pids, list_procs=identified, terminate=terminate)


def server():
    # Even if its owning test is interrupted, the escaped fixture is finite.
    watchdog = threading.Timer(110, lambda: os._exit(91))
    watchdog.daemon = True
    watchdog.start()
    root = Path(os.environ["AGENT_DISPATCH_INSTALL_DIR"])
    publish(root / f"owned-{os.getpid()}.json", {
        "pid": os.getpid(), "token": process_start_time(os.getpid()),
    })
    from agent_dispatch import __main__ as main

    args = main.build_parser().parse_args(sys.argv[sys.argv.index("serve"):])
    raise SystemExit(main._cmd_serve(args))


def caller(root: Path):
    from agent_dispatch import __main__ as main, coordinator_cli, procutil, reap
    from agent_dispatch.cutover_process import verified_pair
    from zdd import routing

    procutil.resolve_own_runtime_python = lambda: sys.executable
    owned = {}
    real_popen = subprocess.Popen
    real_list = reap.iter_coordinator_processes

    def spawn(argv, **kwargs):
        if argv[1:4] != ["-m", "agent_dispatch", "serve"]:
            return real_popen(argv, **kwargs)
        # Only add the fixture's finite-lifetime wrapper; launch options and
        # actual CLI/server/bootstrap remain production code.
        proc = real_popen([argv[0], __file__, "server", *argv[1:]], **kwargs)
        owned[proc.pid] = process_start_time(proc.pid)
        publish(root / f"owned-{proc.pid}.json", {
            "pid": proc.pid, "token": owned[proc.pid],
        })
        return proc

    subprocess.Popen = spawn
    main._reap_superseded_coordinators = lambda result: coordinator_cli._reap_superseded_coordinators(result)
    real_reap = reap.reap_superseded_coordinators

    def isolated_reap(**kwargs):
        # Census is read-only; NEVER hand the production reaper host PIDs.
        active = routing.read_table(root / "routing")["active"]
        owned.update(verified_pair(active["pid"], active["process_start_time"]))
        return reap_fixture_processes(
            owned, **kwargs, list_procs=real_list, reap_fn=real_reap,
        )

    reap.reap_superseded_coordinators = isolated_reap
    started = time.monotonic()
    cutovers = []
    for _ in range(2):
        args = main.build_parser().parse_args([
            "deploy", "--json", "--health-timeout", "60", "--drain-timeout", "10",
        ])
        assert main._cmd_cutover(args) == 0
        published = routing.read_table(root / "routing", strict=True)["active"]
        assert verified_pair(published["pid"], published["process_start_time"])
        cutovers.append(published)
    active = cutovers[-1]
    assert active["generation"] > cutovers[0]["generation"]
    assert (active["pid"], active["process_start_time"]) != (
        cutovers[0]["pid"], cutovers[0]["process_start_time"],
    )
    pair = verified_pair(active["pid"], active["process_start_time"])
    launcher_pid = next(pid for pid in pair if pid != active["pid"])
    # This deliberately old record is not current transition authority.
    sweep = reap.reap_abandoned_passive_backstop(root / "routing", record={
        "new_pid": launcher_pid, "started_at": "2000-01-01T00:00:00Z", "state": "flipped",
    }, grace_seconds=0)
    assert not sweep["reaped"] and sweep.get("refused") is True, sweep
    assert sweep["reason"] == "cutover identity changed; stale passive-reap evidence refused", sweep
    assert routing.read_table(root / "routing", strict=True)["active"] == active
    assert verified_pair(active["pid"], active["process_start_time"]) == pair
    publish(root / "ready.json", {
        "active": active, "pair": pair,
        "cutovers": cutovers, "stale_reap": sweep,
        "elapsed": time.monotonic() - started,
        "owned": owned,
    })
    deadline = time.monotonic() + 30
    while not (root / "pinned").exists():
        assert time.monotonic() < deadline, "parent never pinned fixture identities"
        time.sleep(.02)


if __name__ == "__main__":
    if sys.argv[1] == "server":
        server()
    else:
        caller(Path(sys.argv[2]))
