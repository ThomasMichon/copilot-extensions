"""Phase-0 go/no-go orchestrator for the Session-Host survive-and-reattach spike.

Runs the whole control test on the current OS and prints a PASS/FAIL table for
the three assertions from aperture-labs #1761:

  (1) the child survives the front's mid-turn crash (PID unchanged, running);
  (2) the mid-turn turn keeps streaming to completion while no front is
      attached, and the reattached front sees ``turn_complete``;
  (3) the reattached front resumes from the last-acked ``seq`` with no gap and
      no re-stream (delivery-cursor stability).

Child modes:
  * ``synthetic`` (default) -- deterministic streaming stand-in; proves all
    three assertions with precise timing control.
  * ``real`` -- a real ``copilot --acp --stdio`` child; proves assertion (1)
    (survival of the actual binary + its cmd->pwsh->copilot tree on Windows).

Windows survival toggles model agent-bridge's kill-on-close Job on the front:
  ``--front-job`` arms it; ``--front-breakaway-ok`` decides whether the host is
  permitted to escape (omit it for the negative control -> child should die).
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import osutil

HERE = Path(__file__).resolve().parent


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _find_copilot() -> str:
    exe = shutil.which("copilot") or shutil.which("copilot.exe")
    if not exe:
        raise SystemExit("real mode: 'copilot' not found on PATH")
    return exe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", choices=("synthetic", "real"), default="synthetic")
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--interval", type=float, default=0.2)
    ap.add_argument("--cutoff", type=int, default=3)
    ap.add_argument("--pause", type=float, default=1.2,
                    help="seconds with NO front attached (mid-turn decoupling window)")
    ap.add_argument("--front-job", action="store_true")
    ap.add_argument("--front-breakaway-ok", action="store_true")
    ap.add_argument("--host-breakaway", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep temp state dir")
    args = ap.parse_args()

    real = args.child == "real"
    tmp = Path(tempfile.mkdtemp(prefix="sh-spike-"))
    state_file = tmp / "host.json"
    r1 = tmp / "front1.json"
    r2 = tmp / "front2.json"
    port = _free_port()

    if real:
        child_cmd = [_find_copilot(), "--acp", "--stdio"]
    else:
        child_cmd = [sys.executable, str(HERE / "child_sim.py"),
                     "--frames", str(args.frames), "--interval", str(args.interval)]

    print(f"[spike] os={sys.platform} child={args.child} port={port} tmp={tmp}")
    print(f"[spike] front-job={args.front_job} front-breakaway-ok={args.front_breakaway_ok} "
          f"host-breakaway={args.host_breakaway}")

    # --- frontend 1: spawns host, attaches fresh, crashes mid-turn ---------
    f1_cmd = [sys.executable, str(HERE / "frontend.py"),
              "--role", "1", "--state-file", str(state_file), "--result-file", str(r1),
              "--spawn-host", "--port", str(port), "--child-cmd", json.dumps(child_cmd),
              "--cutoff", str(args.cutoff)]
    if real:
        f1_cmd += ["--crash-after", "3.0", "--no-prompt"]
    if args.front_job:
        f1_cmd += ["--front-job"]
    if args.front_breakaway_ok:
        f1_cmd += ["--front-breakaway-ok"]
    if args.host_breakaway:
        f1_cmd += ["--host-breakaway"]

    f1 = subprocess.Popen(f1_cmd)
    f1.wait()
    print(f"[spike] frontend-1 exited (code={f1.returncode}) -- simulated mid-turn crash")

    if not r1.exists():
        print("[spike] FATAL: frontend-1 produced no result")
        return 2
    res1 = json.loads(r1.read_text())
    child_pid = res1["child_pid"]
    host_pid = res1["host_pid"]
    last_acked = res1["last_acked"]
    print(f"[spike] frontend-1: child_pid={child_pid} host_pid={host_pid} "
          f"acked_seqs={res1['seqs']} last_acked={last_acked}")

    # --- assertion (1a): child + host alive right after the front crash ----
    time.sleep(0.3)
    child_alive_after_crash = osutil.pid_alive(child_pid)
    host_alive_after_crash = osutil.pid_alive(host_pid)
    print(f"[spike] after crash: child_alive={child_alive_after_crash} "
          f"host_alive={host_alive_after_crash}")

    # --- mid-turn window with NO front attached ----------------------------
    time.sleep(args.pause)
    child_alive_during_gap = osutil.pid_alive(child_pid)

    negative_control = args.front_job and not args.front_breakaway_ok and osutil.IS_WIN

    res2: dict = {}
    if not negative_control:
        # --- frontend 2: fresh process reattaches from last_acked ----------
        f2_cmd = [sys.executable, str(HERE / "frontend.py"),
                  "--role", "2", "--state-file", str(state_file), "--result-file", str(r2),
                  "--last-acked", str(last_acked)]
        if real:
            f2_cmd += ["--read-for", "3.0"]
        f2 = subprocess.Popen(f2_cmd)
        f2.wait()
        res2 = json.loads(r2.read_text()) if r2.exists() else {}
        print(f"[spike] frontend-2: reattached_from={last_acked} seqs={res2.get('seqs')} "
              f"hello_child={res2.get('hello_child')} saw_complete={res2.get('saw_complete')}")
    else:
        print("[spike] negative control: host+child expected dead; skipping reattach")

    # --- explicit terminate (the only sanctioned reap) ---------------------
    _terminate_host(port)
    time.sleep(0.5)
    child_alive_end = osutil.pid_alive(child_pid)

    # --- evaluate ----------------------------------------------------------
    results = _evaluate(real, res1, res2, child_pid,
                        child_alive_after_crash, host_alive_after_crash,
                        child_alive_during_gap, child_alive_end, args)

    print("\n=== Phase-0 assertions ===")
    all_pass = True
    for name, ok, detail in results:
        all_pass = all_pass and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    summary = {
        "os": sys.platform, "child": args.child, "toggles": {
            "front_job": args.front_job, "front_breakaway_ok": args.front_breakaway_ok,
            "host_breakaway": args.host_breakaway,
        },
        "front1": res1, "front2": res2,
        "assertions": [{"name": n, "pass": ok, "detail": d} for n, ok, d in results],
        "verdict": "PASS" if all_pass else "FAIL",
    }
    (tmp / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\n[spike] VERDICT: {'PASS' if all_pass else 'FAIL'}")
    print(f"[spike] summary: {tmp / 'summary.json'}")

    _terminate_host(port)
    if not args.keep:
        _cleanup(child_pid, host_pid, tmp)
    return 0 if all_pass else 1


def _terminate_host(port: int) -> None:
    import wire
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=2.0)
        wire.send_msg(s, wire.ATTACH, wire.pack_u64(0))
        wire.recv_msg(s)  # HELLO
        wire.send_msg(s, wire.TERMINATE)
        s.close()
    except OSError:
        pass


def _cleanup(child_pid: int | None, host_pid: int | None, tmp: Path) -> None:
    for pid in (child_pid, host_pid):
        if pid and osutil.pid_alive(pid):
            try:
                if osutil.IS_WIN:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    import os
                    import signal
                    os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    shutil.rmtree(tmp, ignore_errors=True)


def _evaluate(real, res1, res2, child_pid, child_alive_after_crash,
              host_alive_after_crash, child_alive_during_gap, child_alive_end, args):
    results = []
    negative_control = args.front_job and not args.front_breakaway_ok and osutil.IS_WIN

    if negative_control:
        # Expectation inverts: without breakaway_ok the child SHOULD die with
        # the front's job -- proving the job coupling is real and breakaway is
        # the thing that fixes it.
        results.append((
            "negative-control: child dies without breakaway",
            not child_alive_after_crash,
            f"child_alive_after_crash={child_alive_after_crash} (expected dead)",
        ))
        return results

    # (1) child survived the front crash, PID unchanged
    pid_unchanged = (res2.get("hello_child") == child_pid) if res2 else False
    results.append((
        "1: child survives front crash (PID unchanged, running)",
        child_alive_after_crash and child_alive_during_gap and pid_unchanged,
        f"alive_after_crash={child_alive_after_crash} alive_in_gap={child_alive_during_gap} "
        f"pid_unchanged={pid_unchanged} (child_pid={child_pid})",
    ))
    # host survived too
    results.append((
        "1b: session host survives the front crash",
        host_alive_after_crash,
        f"host_alive_after_crash={host_alive_after_crash}",
    ))

    if not real:
        seqs2 = res2.get("seqs") or []
        # (2) mid-turn completed
        results.append((
            "2: mid-turn turn streamed to completion (turn_complete seen)",
            bool(res2.get("saw_complete")),
            f"saw_complete={res2.get('saw_complete')} last_seq={res2.get('last_seq')}",
        ))
        # (3) no gap, no re-stream
        first = res2.get("first_seq")
        contiguous = all(seqs2[i] + 1 == seqs2[i + 1] for i in range(len(seqs2) - 1))
        no_restream = bool(seqs2) and min(seqs2) > res1["last_acked"]
        no_gap = (first == res1["last_acked"] + 1)
        results.append((
            "3: reattach resumes from last-ack, no gap + no re-stream",
            no_gap and no_restream and contiguous,
            f"first_seq={first} expected={res1['last_acked'] + 1} contiguous={contiguous} "
            f"no_restream={no_restream}",
        ))
    else:
        # real copilot: assertion (1) is the meaningful one; also confirm the
        # reattached front could still reach the same live child.
        results.append((
            "2/3 (real): reattached front reached the same live copilot child",
            bool(res2) and res2.get("hello_child") == child_pid and child_alive_end is not None,
            f"hello_child={res2.get('hello_child')} child_pid={child_pid}",
        ))
    return results


if __name__ == "__main__":
    sys.exit(main())
