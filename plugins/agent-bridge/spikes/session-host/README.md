# Session-Host survive-and-reattach spike (Phase 0)

Throwaway spike for the **agent-bridge Session-Host decoupling** effort
(aperture-labs [#1759](https://gitea.michon.ski/tmichon/aperture-labs/issues/1759) /
Phase-0 go/no-go [#1761](https://gitea.michon.ski/tmichon/aperture-labs/issues/1761)).

It proves the load-bearing primitive **before** any real code is built: a
Copilot `--acp` child can be owned by a **Session Host** that outlives the
agent-bridge frontend, so a front can crash mid-turn and a fresh front
**reattaches** to the still-living child with delivery cursors intact.

Nothing here ships. The files live under `spikes/` and are **not** imported by
the `agent_bridge` package (packaging is `where = ["src"]`) and **not** collected
by pytest (no `test_*.py` names).

## What it demonstrates

Three assertions, matching the design's operator goals:

1. **Child survives the front's mid-turn crash** — PID unchanged, still running.
2. **The mid-turn turn keeps streaming to completion** while *no* front is
   attached (the host keeps reading + buffering), and the reattached front sees
   `turn_complete`.
3. **Reattach resumes from the last-acked frame `seq`** — no gap, no re-stream
   (delivery-cursor stability).

Plus the **Windows crux**: the Session Host must **break away** from the
front's kill-on-close Job Object (`CREATE_BREAKAWAY_FROM_JOB`, permitted by the
front's job carrying `JOB_OBJECT_LIMIT_BREAKAWAY_OK`) or it is force-killed on
front exit. A **negative control** proves that without breakaway the child
correctly dies — i.e. the coupling is real and breakaway is the fix.

## Components

| File | Role |
|------|------|
| `wire.py` | Length-prefixed control+data protocol (`ATTACH`/`HELLO`/`FRAME`/`ACK`/`WRITE`/`TERMINATE`/`LIVENESS`). |
| `osutil.py` | Cross-platform liveness + per-OS survival adapters (POSIX new-session; Windows Job Object with optional breakaway-ok, `CREATE_BREAKAWAY_FROM_JOB`). |
| `child_sim.py` | Synthetic ACP-ish child: streams N newline-delimited JSON frames then `turn_complete`. Deterministic timing. |
| `session_host.py` | Stub Session Host: owns the child + pipes, relays ACP **1:1**, buffers unacked frames, serves reattach over loopback. Does **no** ACP semantic parsing. |
| `frontend.py` | Stub frontend: role 1 spawns the host + crashes mid-turn; role 2 reattaches from the last-acked `seq`. |
| `run_spike.py` | Orchestrator: runs the control test on the current OS and prints a PASS/FAIL table + writes `summary.json`. |

## Running

```bash
# POSIX / WSL — baseline (proves all three assertions)
python3 run_spike.py --child synthetic

# POSIX / WSL — survival with the real copilot binary (assertion 1)
python3 run_spike.py --child real
```

```powershell
# Windows — baseline
python run_spike.py --child synthetic

# Windows — THE CRUX: front arms a kill-on-close job that permits breakaway;
# host breaks away into its own job and survives the front crash
python run_spike.py --child synthetic --front-job --front-breakaway-ok --host-breakaway

# Windows — negative control: front's job forbids breakaway; child SHOULD die
python run_spike.py --child synthetic --front-job

# Windows — real copilot survival through job-breakaway
python run_spike.py --child real --front-job --front-breakaway-ok --host-breakaway
```

Exit code is `0` on PASS, `1` on FAIL. `--keep` retains the temp state dir
(`summary.json`, per-front result JSON).

## Results (2026-07-03, lambda-core)

| Scenario | OS | Verdict |
|----------|----|---------|
| synthetic baseline | Windows | PASS (1,2,3) |
| job-breakaway (front job + breakaway-ok + host breakaway) | Windows | PASS (1,2,3) — **crux holds** |
| negative control (front job, no breakaway) | Windows | PASS (child dies as expected) |
| real copilot survival + job-breakaway | Windows | PASS (survival) |
| synthetic baseline | WSL/POSIX | PASS (1,2,3) |
| real copilot survival | WSL/POSIX | PASS (survival) |

**Go/no-go: GO.** The Windows job-breakaway design implication — agent-bridge's
own Job Object must carry `JOB_OBJECT_LIMIT_BREAKAWAY_OK` so the Session Host
can escape it — is confirmed by the positive/negative pair and carries into
Phase 1.
