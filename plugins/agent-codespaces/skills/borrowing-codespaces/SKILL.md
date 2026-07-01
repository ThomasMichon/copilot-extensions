---
name: borrowing-codespaces
description: >
  Advisory borrow/check-in of a GitHub CodeSpace to an effort so parallel
  same-machine agents don't collide on it, plus patient startup waits for a
  slow-booting CodeSpace. Use when asked to "borrow a CodeSpace", "check out a
  CodeSpace for this effort", "release a CodeSpace", "who's using which
  CodeSpace", "wait for a CodeSpace to come up", or when a CodeSpace is slow to
  provision. For the effort<->CodeSpace binding + dispatch loop, see the
  control-plane delegation skill (e.g. dispatching-work); for local containers,
  see borrowing-containers / containers-fleet.
  Trigger phrases include:
  - 'borrow a codespace'
  - 'check out a codespace'
  - 'release a codespace'
  - 'codespace lease'
  - 'who is using this codespace'
  - 'wait for a codespace'
  - 'codespace slow to start'
  - 'codespace not coming up'
---

# Borrowing CodeSpaces (advisory lease + startup tolerance)

Two related mechanics from `agent-codespaces` that keep parallel agents from
colliding on a CodeSpace and keep a slow boot from being mistaken for a dead
one. This skill owns the **generic CLI mechanics**; the *effort ↔ CodeSpace
binding* (recording the borrow in the effort file, the dispatch/monitoring loop)
is owned by the control-plane delegation skill (e.g. `dispatching-work` /
`working-cross-repo`), which calls these commands.

> **CodeSpace vs container:** a CodeSpace is the default for cloud feature work;
> borrow a **local container** (`borrowing-containers` / `containers-fleet`) for
> fast local iteration. The lease model is the same shape in both plugins.

---

## Advisory lease (borrow / release / leases)

The lease is **host-local advisory state** in
`~/.agent-codespaces/leases.json` (exclusive-locked for race-safety across
parallel worktree agents on one box). It records that a given **effort/worktree**
is borrowing a CodeSpace so a second agent on the same machine doesn't dispatch
to it concurrently. A CodeSpace is addressed **by name** (unlike the container
fleet, there is no "pick a free one").

```bash
agent-codespaces borrow <effort> <codespace>   # check out (prints the name)
agent-codespaces release <effort|codespace>    # check in
agent-codespaces leases                         # CODESPACE  EFFORT  HOST  PID
```

- **Idempotent** for the same effort (refreshes the heartbeat, preserves
  `acquired_at`).
- **Conflict:** borrowing a CodeSpace already held by a *different* live effort
  **errors** with the holder's effort/host/pid. Take it over with `--force`
  (the escape hatch for a stale or buggy holder).
- **TTL-based reclamation:** a lease is held by an *effort* (a logical entity),
  not the CLI process, so a forgotten lease self-expires after the TTL (24h
  default). Always `release` explicitly so the CodeSpace frees immediately.

### Check-out / check-in wiring (automatic)

- **Check-out on connect:** `agent-codespaces ssh <name> --effort <effort>`
  records/refreshes the lease before connecting. It is **non-blocking** — a
  conflicting live lease **warns** but still connects; use `borrow --force` to
  take over explicitly. (`ssh --force`, the SSH-lock takeover, also forces the
  lease takeover.)
- **Check-in on teardown:** `agent-codespaces delete <name>` and
  `finalize <name> --delete` **auto-release** the lease. Releasing by effort
  name frees whatever CodeSpace it held.

> **Cross-machine limitation (v1):** the lease store is **per-machine**. A
> CodeSpace is a cloud resource that could be borrowed from dev6 *and* book2 /
> cloud1; a host-local file only coordinates the common **same-machine** case.
> **Planned multi-machine coordination** layers a **cloud-global beacon** on top:
> since `gh codespace list` is visible from any machine, the borrowing worktree's
> 4-hex id is suffixed onto the CodeSpace **display name** (`gh codespace edit
> --display-name`) so any machine sees who holds it with zero SSH; the rich
> host-local lease stays the source of truth, and a machine seeing a *foreign*
> suffix can SSH that one machine for detail. (GitHub exposes no arbitrary
> CodeSpace metadata API — display-name is the only settable cloud-global field.)
> Until that lands, treat a lease as a same-box advisory only.

---

## Startup tolerance (wait)

CodeSpace create/provision is finicky — a slow boot must **never** be mistaken
for a dead CodeSpace (which would trigger a wasteful redundant create). Use the
patient waiter instead of a fixed short timeout:

```bash
agent-codespaces wait <name>                 # up to 20 min by default
agent-codespaces wait <name> --timeout 1800  # widen the ceiling
```

It **distinguishes "still provisioning" from "genuinely dead":**

- **Available** → exit `0`.
- **Terminal-failed** state (`Failed` / `Unavailable` / `Deleted` / `Moved` /
  `Archived`) → **fail fast**, exit `2` — it will not become Available on its
  own; diagnose before recreating.
- **Timeout** while still pending (Provisioning/Starting/Queued/…) → exit `124`
  — it may still be coming up; **wait longer**, don't declare it dead.

**Backgrounding a slow boot:** run `agent-codespaces wait <name> --timeout 1800`
as a background task and continue other work; you'll be notified when it exits.
A `Shutdown` (stopped-but-healthy) CodeSpace is treated as *pending* — it boots
on connect via `agent-codespaces ssh`, so prefer connecting over polling once
you know it exists.

---

## Surfacing borrowed-CodeSpace status

When reporting status, join each lease's `EFFORT` to the active efforts to show
which effort holds which CodeSpace, and flag any lease whose effort is no longer
active (a candidate for `release`):

```bash
agent-codespaces leases
```

---

## Edge cases

- **Conflict on borrow:** pick a different CodeSpace, `release` the current
  holder, or `--force` if it's stale.
- **Effort spans a CodeSpace *and* a container:** independent dispatch targets
  (`codespace:<name>` vs `container:<name>`); record both in the effort file.
- **Stale lease (effort gone):** `leases` shows it; `release <effort>` frees it,
  or it self-expires after the TTL.
- **CodeSpace slow to appear in `gh codespace list`:** `wait` tolerates transient
  list errors (retries); it only reports `FAILED` on an actual terminal state.
