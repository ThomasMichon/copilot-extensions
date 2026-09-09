# Phase 4b(ii) — Convergence design + adversarial mock implementation

Sibling design doc for [`plugin-process-hygiene`](README.md) Phase 4b, extending
the vision behaviors landed in PR #2300
(`process-count-scales-with-services-not-sessions`,
`hooks-and-callbacks-are-transient`) into a concrete, shared shape every
`agent-*` plugin's hooks and extension callbacks can adopt, validated by an
adversarial mock **before** any per-plugin implementation lands.

## Why a shared design, not four independent fixes

Phase 4b already measured the problem for agent-worktrees (nine `sessionStart`
commands → one bounded client, 76.6ms median) and #737/#918 already gave the
suite a shared `single-instance-lease` + reaper library. What is still missing
is the **client-side half** of that shape, generalized so agent-dispatch,
agent-bridge, and agent-ssh don't each reinvent it: a hook or extension
callback in *any* plugin should resolve identity, reach *a* live daemon, and
exit, using the same discovery/timeout/degrade contract regardless of which
daemon it's talking to.

## Design: the transient-callback client shape

A **transient hook client** (see `visions/plugin-services` §*Transient hook /
extension callback*) does, in order, with a hard wall-clock budget (default
~300ms, configurable per call site):

1. **Resolve identity** — session id, worktree/cwd, repo, whatever the calling
   hook needs — from already-available env/CLI state. Never re-derives this by
   re-running a full CLI subcommand tree (the anti-pattern #1788 fixed for
   agent-worktrees).
2. **Discover the live daemon** — via the plugin's existing rendezvous
   mechanism (a routing-table file, an `endpoint.json`, whatever that plugin
   already uses for client discovery). **Never spawns one to make sure.** A
   discovery miss is a normal outcome, not a retry trigger.
3. **Post one bounded packet** — a single request carrying the resolved
   identity plus the hook's payload. No polling loop, no held connection
   beyond the single request/response.
4. **Optionally read back guidance** — the response may carry follow-up
   instructions for the calling agent turn; the client relays it verbatim and
   exits. It never interprets or acts on the guidance itself.
5. **Exit.** Always, on every path: success, discovery miss, timeout, or daemon
   error. A transient client process outliving its own budget is itself a
   Phase 4b regression, not a acceptable degraded mode.

Degradation (step 2 or 3 fails): the hook falls back to whatever inline/no-op
behavior is correct for that call site (per *degrade-gracefully*) and exits
with the same latency budget. It never blocks waiting for a daemon to appear,
and it never promotes itself into the missing daemon (no "become the daemon if
none found" fallback in the *client* — that responsibility belongs only to
whichever code path is explicitly the daemon's own bootstrap, guarded by
`single-instance-lease`).

This is a **contract**, not a new library requirement: a plugin already
shaped this way (agent-worktrees post-#918) needs no code change from this
design — it is the reference implementation the mock validates against.

## The adversarial mock

A synthetic, stdlib-only harness (living beside the existing clean-room probes
once built, `tools/clean-room/scenarios/plugin-process-hygiene-convergence/`)
that does **not** exercise a real plugin. It exercises the *shape* above
against a minimal mock daemon, so it can run fast, in CI, without provisioning
any real plugin runtime — the counterpart to unit tests for a design that
spans plugins.

### Mock components

- **Mock daemon**: a tiny stdlib HTTP server that (a) acquires
  `single-instance-lease` for a synthetic service name, (b) answers a
  `/hook` POST with a canned guidance payload, (c) can be told to introduce
  latency, drop the connection mid-response, or refuse to bind (simulating a
  port conflict), and (d) publishes/withdraws its own rendezvous file on
  command so tests can simulate "daemon absent" precisely.
- **Mock transient client**: a minimal implementation of the 5-step contract
  above, parameterized so a test can inject: a fake identity resolver, a fake
  rendezvous reader, and the wall-clock budget.
- **Adversary driver**: spawns **N concurrent mock clients** (real OS
  processes, not threads/asyncio tasks — the whole point is *process* count)
  under each of the scenarios below and asserts on the *system*, not just each
  client's return value.

### Scenarios (each a PASS/FAIL check, mirroring the existing clean-room probe
convention of `PROBE: <name> PASS|FAIL <detail>`)

1. **`flood-against-live-daemon`** — one mock daemon already up; spawn N=50
   concurrent clients. Assert: exactly 1 daemon process before and after
   (never fluctuates), all 50 clients exit within the latency budget, all 50
   receive the canned guidance, zero client processes remain after the flood
   settles.
2. **`flood-against-absent-daemon`** — no daemon running, no rendezvous file.
   Spawn N=50 concurrent clients. Assert: **zero** new daemon processes are
   ever created by any client (the client contract forbids self-promotion),
   every client falls back to its inline/no-op path and exits within budget,
   and the OS process count after the flood equals the count before it.
3. **`daemon-appears-mid-flood`** — start the flood against an absent daemon,
   then publish a real daemon + rendezvous file partway through. Assert:
   clients that raced ahead of publication degrade correctly (scenario 2's
   guarantee); clients whose discovery step lands after publication reach the
   daemon (scenario 1's guarantee); no client hangs waiting to retry
   discovery across the transition.
4. **`daemon-dies-mid-packet`** — daemon accepts the connection, then is
   killed (or the mock is told to drop the socket) before responding. Assert:
   every in-flight client hits its wall-clock budget and exits with a clean
   degrade — none hang past the budget, none crash uncaught.
5. **`concurrent-daemon-race`** — no daemon and no rendezvous file; spawn M
   (e.g. 10) concurrent processes that each attempt to **become** the mock
   daemon (this is the daemon-bootstrap path, guarded by
   `single-instance-lease`, not the client contract) at the same instant.
   Assert: exactly one wins the lease and starts listening; the other M-1
   stand down without racing a rival listener or crashing; a subsequent client
   flood (scenario 1's shape) reaches the single winner.
6. **`process-count-invariant-under-repeated-floods`** — run scenarios 1-5 in
   sequence, five times each, without any explicit cleanup between rounds
   other than what the contract itself guarantees. Assert the OS process
   count returns to its pre-suite baseline after every round — the empirical
   form of *process-count-scales-with-services-not-sessions*: repeated agent
   activity (the floods) never leaves the daemon count, or the transient
   client count, higher than it started.

### Acceptance threshold

All six scenarios PASS, with the wall-clock budget (default 300ms) never
exceeded by more than 2x under scenario 4's induced failure (a generous bound
— the point is "bounded", not a specific number), and **zero** leftover
processes (of either kind) after the full suite, verified by the same
before/after OS process census technique used to find the original
11-coordinator/68-conhost finding (issue #2301).

## Relationship to #2301

Issue #2301 is the reality-gap audit against real plugins. This design +ock
harness is the shared contract that audit should measure each plugin
*against* — a plugin passes #2301's audit for a given hook/callback exactly
when that hook/callback provably follows the 5-step contract above (which the
mock validates in the abstract; the per-plugin audit confirms concretely, with
citations to already-landed evidence where it exists — see the reconciliation
note in the effort Journal for what's already covered by #737/#738/#739/#918).

## Plan for this sub-phase

- [ ] Land this design doc + effort/README updates as the reviewed plan (this
      PR) before any code.
- [ ] Build the mock daemon + mock transient client + adversary driver as a
      new clean-room scenario.
- [ ] Run the six scenarios locally; fix the design (not the vision) if a
      scenario reveals the contract itself is underspecified.
- [ ] Land the mock harness as its own PR, referencing this doc.
- [ ] Use the harness's PASS/FAIL evidence, plus #2301's per-plugin citations,
      to decide which (if any) plugin needs an actual code change versus
      already conforming.
