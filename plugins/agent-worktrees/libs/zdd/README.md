# zdd -- zero-downtime cutover library

Shared active/passive redeploy primitives for Copilot CLI plugins and multi-machine
services. Extracted from agent-bridge (which proved the design in production) so
multiple consumers reuse one implementation instead of reinventing it.

- **Distribution:** `agent-zdd` (the `agent-` prefix avoids PyPI
  dependency-confusion; the package is never published to an index -- consumers
  install it from a local path or a pinned Git ref).
- **Import module:** `zdd`

## What's inside

### `zdd.routing` -- the routing table (no proxy)

A file-based, client-read routing table (`active.json`) that decouples *which
port is live* from static config. Short-lived clients re-read it every
invocation; the daemon publishes its endpoint on startup and flips
`active`/`previous` atomically on cutover. Readers self-heal: a dead `active`
falls back to `previous`, then to the caller's static config.

Why a table rather than a front proxy: a proxy holding a stable port is itself a
long-lived process you must update, which re-introduces the very downtime it was
meant to remove (and demands socket hand-off between proxy generations --
hardest on Windows). A file has no process to update.

A **watchdog counterpart**, `reap_stale_active`, complements the client-side
self-heal above for a process that polls on its own schedule rather than only
reading the table per-request: it retires an `active` that names a dead pid/port
(promoting a live `previous`, or clearing the table), and separately heals a
table that has **no `active` claim at all** -- the shape a clean shutdown
(`clear_if_owner`) leaves behind when no successor ever publishes itself, which
otherwise strands every consumer indefinitely. Both promotions require the
candidate `previous` to have both a live listener *and* a positive recorded
pid confirmed alive, reducing (though not eliminating -- pid reuse races
remain a residual, narrower risk) the chance an unrelated service that later
reuses the same port gets mistaken for the real daemon.

Key API: `Endpoint`, `read_active_endpoint`, `publish_active`,
`clear_if_owner`, `reap_stale_active`, `routing_table_path`.

Publications for a live PID also record its `process_start_time`, establishing
the process identity when the route is written rather than sampling a potentially
reused PID during later adoption. The three publication APIs accept an optional
spawn-time `process_start_time` baseline and refuse a mismatched or unverifiable
baseline before changing the route. Active/previous endpoints and guarded
publication preserve and compare this optional token.

Legacy routes without the field remain readable. If identity cannot be read,
ordinary publication logs a warning and retains the existing routing behavior;
an identity-bound supervisor must reject that unverified route, not substitute
a fresh token. This field does not itself implement a singleton manager, provide
Windows handle custody, or change listener-based client fallback.

The same routing owner maintains `publication_history` in `active.json`.
Every serialized publish, restore, clear and stale-route mutation preserves
sticky nonsecret PID/start/address/generation evidence. Removing a daemon from
both current rows does not erase its promotion. A passive launcher captures
`publication_witness` under native routing authority **before** spawning;
breadcrumbs preserve this epoch and the candidate's birth-time process token.
Missing/malformed history, a changed epoch, an unknown birth token or a matching
PID/port publication cannot authorize disposal. PID/port reuse conservatively
blocks cleanup instead of weakening custody.
Conservative custody is durably committed while the old route remains visible,
before publishing a route that can admit work. Linux writes flush the file and
parent directory; Windows uses a flushed file plus native write-through
replacement. Other POSIX platforms, including macOS, retain flushed-file
atomic replacement without the Linux-only directory barrier; power-loss
durability and native identity-bound conditional retirement are not validated
there. A failed final flip may retain an extra protected candidate; it
must never manufacture a false negative after a crash.
Replacement visibility and durability are separate outcomes. A post-replace
directory-flush failure raises `PublicationCommitUncertain`; cutover retains
both generations and an owner-recovery breadcrumb rather than entering an
unflipped disposal path. The result exposes `publication_uncertain: true`.
An unpublished passive's shutdown skips routing mutation when it does not own
the active claim, so native exit confirmation can keep the termination fence
without blocking that passive's own `clear_if_owner()` cleanup.

Storage is capped at 2,048 retained entries. No remembered publication is pruned;
capacity exhaustion sets a sticky saturation flag and disables negative proofs
for all other candidates. Verified owner-controlled handoffs remain available,
but unproven orphan cleanup stays refused. A legacy writer that drops the
history invalidates earlier witnesses; later initialization cannot certify an
already-running candidate. The passive spawn contract must actually block work
admission before promotion, not merely refrain from publishing a route.

Generic diagnostics now retains promoted or unverifiable generations for native
owner retirement, even if classified as duplicates or superseded. It may
dispose only a fresh, identity-matched candidate with a pre-birth witness and
complete negative publication evidence, rechecked under native routing authority
at the termination point. A valid current owner alone is not disposal authority.
Generic diagnostic terminators must accept the `exit_timeout` keyword and
confirm exit through the same native handle within five seconds while the
routing fence remains held. Legacy signal-only callbacks, nonboolean evidence
and unconfirmed exits are blocked outcomes, not successful retirement.
An unavailable or failed process census is never an empty inventory; occupied
slot replacement remains refused when native candidate absence is unverified.
Ordinary cutover also refuses unreadable or unknown recovery states rather than
overwriting them with a new attempt.
An unreadable/corrupt routing table is a typed `ActivePublicationRefused` for
writers and a failed result before spawn for ordinary cutover. Read-only
client fallback remains tolerant; mutation must not infer an empty namespace
from unavailable custody evidence.
Passive adapters bind termination to the breadcrumb's original process token,
not a newly sampled token. Their five-second exit confirmation waits on the
same pidfd/Windows handle used to signal; acceptance alone is not successful
reaping. Unconfirmed exit and termination errors are explicit refusals.
The standalone installer retains the version slot and marker when passive
custody, recovery or exit cannot be verified rather than recopying an occupied
slot. Fresh absent slots require no unrelated retirement and can still be
materialized without touching the existing generation or its recovery record;
this does not assert activation succeeded. `terminate_pid_if_identity(...,
exit_timeout=...)` exposes this optional
native wait with a finite zero-to-30-second budget; existing signal-only callers
retain their original result shape.

### `zdd.cutover` -- the cutover orchestrator

`CutoverOrchestrator` drives one active/passive cutover: spawn the new daemon on
a fresh port, health-gate it, flip the routing table, drain the old daemon, then
retire it. The sequence is reversible up to an explicit commit point, with
rollback and commit-forward (if the old endpoint is unreachable, it commits to
the healthy new one rather than stranding clients).

#### Conditional owner operation (partial lifecycle support)

`CutoverOrchestrator.run(conditional=ConditionalCutover(...))` adds an opt-in
stale-runtime repair contract without changing ordinary callers. It acquires
the existing cutover lock **before** validating the exact raw active endpoint
(PID, publication-time start token, generation, version and address). Missing
identity, changed ownership, unresolved breadcrumbs, equal/older selected
versions or unprovable ordering refuse before spawn or routing/breadcrumb
mutation. The adopter supplies `selected_is_newer(selected, serving)` using
its own version scheme; unknown order must refuse, never use lexical ordering.

`ConditionalCutover.capture_runtime(old_endpoint)` runs under that authority.
It returns a `RuntimeSnapshot`, or `None` when authoritative effective state is
unavailable. Snapshot callbacks hold the **same resolved** database, install/
configuration roots, configured executable, persistent service settings and
sensitive environment in memory. They implement passive spawn, health
attestation and control clients. Neither ambient caller environment,
`service.env` alone nor `/proc` environment is an authoritative snapshot.
Capture must derive effective state from the serving owner, including in-memory
configuration overrides. The snapshot's nonsecret `identity` covers every
compatibility-relevant setting; credentials must not be included in it.
Callbacks must redact credential-bearing errors before raising `CutoverError`.

`RuntimeAttestation` binds that identity to the actual PID/start token/version
and readiness. The predecessor must attest before spawn; the successor must
attest both before flip and before predecessor shutdown. Sensitive environment
stays in callbacks, never in public health, routing, breadcrumbs or result JSON.
Bridge health is anonymous; Dispatch health may require its configured token.
Each adopter owns those distinct authentication contracts and the authenticated
capture channel; there is no generic health-based configuration fabrication.

`routing_lock_timeout` bounds each native `active.lock` acquisition, including
the process-local lock. It is not a whole-attempt deadline: capture, health,
drain and retirement are distinct operations, and a later lock acquisition
receives its own budget. Overall production completion bounds must be supplied
separately by the adopter, not by consuming this budget during startup.
Routing persistence does not mutate caller-supplied tables; witness
initialization reads the persisted custody back under the routing lock.
Pre-spawn contention refuses unchanged; publication
contention attempts disposal only of a verified never-promoted candidate;
unavailable cleanup authority retains it with a nonterminal breadcrumb.
Disposal rechecks publication and process identity under the routing lock and
holds that fence through exact native exit confirmation (at most five seconds,
capped by `retirement_timeout`). Signal acceptance alone retains recovery state.
After publication,
rollback CAS-restores only the attempt's own route, revalidates the predecessor,
and undrains it through captured credentials. Changed ownership or inability to
restore under the bounded lock leaves both processes alive and retains the
nonterminal breadcrumb for owner-led recovery. It never overwrites a later
winner, forces retirement, or converts failed repair into success. Ordinary
unconditional callers retain their existing unbounded routing-lock behavior.
Restoration must attest ready after undrain. A previously promoted successor
may have admitted work, so rollback retains it with unresolved recovery state
rather than terminating it. Only never-promoted candidates can be disposed.
The snapshot's required `retire(old, successor)` callback must enforce expected
PID/start identity at the receiving daemon or through an identity-bound native
handle, with a bounded operation. Address-based shutdown alone is insufficient.
The receiver must atomically verify the exact successor still owns the active
route before retiring its predecessor; re-promotion of the predecessor refuses
retirement. Never-promoted candidate disposal uses a verified pidfd/native
handle, not a numeric-PID `terminate()` request.
The required `drain_predecessor(old, successor, timeout)` callback must validate
the predecessor's PID/start identity and the exact successor route under the
receiver's native routing authority before opening its drain gate. Both
published endpoint identities are supplied; caller-side validation alone does
not close a publication race. Ordinary address-based drain is never used by
the conditional path. Production IPC must supply this receiver-bound operation.
`undrain_predecessor(restored)` and `adopt_successor(successor)` likewise validate
the exact receiver process and expected active publication under native routing
authority before reopening admission or adopting the relay. Ordinary client
methods are not used for conditional control. Drain evidence must contain boolean
`drained`, `clean` and `forced` fields; successful retirement requires clean,
unforced completion. Public results allowlist these flags and a busy-work count,
not arbitrary captured response values or work identifiers.
Failed retirement requests preserve a nonterminal breadcrumb and the successor
route; later conditional repair refuses until owner-led recovery resolves it.
The required `confirm_retired(endpoint, timeout)` callback must confirm the
exact predecessor's exit through an authoritative native process handle.
`retirement_timeout` defaults to 30 seconds and must be finite and nonnegative.
False or unavailable exit evidence leaves the attempt failed and its recovery
breadcrumb nonterminal, even after an accepted shutdown request.
Conditional breadcrumbs use distinct `conditional-started`,
`conditional-flipped`, and `conditional-draining` states. Legacy recovery,
passive reaping, and ordinary cutover refuse those states without probing or
undraining either generation. Only the conditional owner may resolve them.
Legacy recovery and passive reaping acquire native transition authority
non-blockingly before reading or mutating recovery state, closing the race
where an earlier legacy observation could overwrite a newly-started conditional
attempt. Contention is an explicit refusal, not a stale-state repair.
Legacy recovery preserves the originating orchestrator PID and transition
identity when recording its terminal outcome. A subsequent guarded reap can
consume the original observation only while the freshly locked record still
describes that same transition; a replacement transition remains refused.
Malformed refusal reasons are logged as outcome-contract failures and remain
nonzero CLI outcomes. Invalid candidate PIDs cannot pass health or authorize
disposal. Conditional cleanup uses the public deadline-bound `routing_lock`.
Passive reaping revalidates the current transition identity and both active
and previous routing publications while holding native routing authority
through disposal. Caller-captured PID/record observations cannot authorize
reaping a newly promoted process. Conditional spawn exceptions produce a
redacted failed result and retain the unresolved owner breadcrumb.
Relay-adoption exception values and malformed response values are never copied
into logs or result JSON; failures expose only their exception class.

The supported in-process adapter is `run_conditional_cutover(config_dir,
version=..., request=...)`. Bridge deployment/service restart and Dispatch
deployment accept the request plus the **observed** `routing_dir` as keyword
arguments, bypassing ambient configuration, legacy recovery preludes and
post-cutover force-reaping. These are internal owner operations, not JSON/argv
serialization of sensitive callbacks. Conditional results add
`serving_endpoint`, `retirement_requested` and `retirement_confirmed`; an
accepted shutdown request is **not** confirmed process exit.

**Remaining adapter obligation:** production daemons do not yet implement
authoritative effective-config capture and matching health attestation.
Legacy daemons must therefore return no snapshot and refuse conditional
auto-retirement. This primitive and adapter threading do not finish automatic
stale-runtime repair, service-manager adoption or a watchdog apply path.
Explicit unconditional cutover remains supported. Linux lock behavior is
directly tested; Windows uses bounded native byte-lock retries but still needs
real Windows process-boundary validation. macOS uses flock; native process
identity availability remains required and unverified identity refuses.

### `zdd.diagnostics` -- daemon-health audit + repair

An unavailable native/process census is reported as `census_unavailable`,
with `census_available: false` and `candidate_count: null`, rather than a
healthy empty census. Repair stops at any failed fresh census, and the
cutover guard is released even when candidate enumeration fails.

`audit_daemon_health()` and `apply_daemon_health()` let a consumer surface the
field-proven abnormal cutover states uniformly through its own doctor/health
command: duplicate resident daemons, stranded old survivors from an aborted
cutover, never-promoted abandoned passives, and stale superseded generations
that should already have self-retired. Destructive repairs are gated on the
same two rules everywhere: first confirm a validated live owner from the
consumer's lock/routing state, then terminate only through an identity-bound
OS handle tied to the target process's start time.
On Linux, portable Python builds without Python-level pidfd exports reuse the
existing native libc pidfd adapter. If neither backend exists, identity-bound
termination remains explicitly unavailable; there is no numeric-PID fallback.
Generic diagnostics acquires native cutover authority before applying repairs.
Conditional or unverifiable recovery state is non-repairable and requires its
owner, regardless of reachability or duplicate-process classification; retained
promoted generations cannot be discarded by the generic cleanup path.

**Generations, not PIDs.** On Windows a daemon started through a venv
`python.exe`/`pythonw.exe` redirector appears twice in a process census: the
redirector (launcher) and the base interpreter it starts (worker). A consumer
whose daemons can run that way sets `DiagnosticContext.group_launchers=True`
(optionally with `is_launcher_image`, default `zdd.generations.is_venv_redirector`)
and fills each `DaemonCandidate`'s `ppid` and `image`. The doctor then groups
the census structurally (`zdd.generations.group_generations`): a candidate and
its parent form one generation only when the parent is a launcher image, the
child is not, the parent has no other candidate child, neither is part of a
longer candidate chain, and the child provably started after the parent. A
route, lock or breadcrumb PID naming either member identifies the whole
generation, so the validated owner's launcher is never a target, and a
`duplicate_resident` finding names a second *generation* (worker first, then
launcher, each with a `role`). Reports add `generation_count`, `generations`,
`grouping_uncertain` and `grouping_uncertain_reason`. When grouping is
uncertain (an unreadable parent or executable, a launcher-image candidate with
no captured worker, or a parent/child relation that fails those checks), every
terminating finding is reported with `repairable: false` /
`blocked_reason: "generation grouping uncertain"`, and
`apply_daemon_health()` refuses every termination with a `generation_grouping`
action whenever the initial census or any fresh re-census is uncertain:
diagnostics reports what it sees but never kills on a guess. Consumers
that do not opt in keep one generation per candidate.

### `zdd.singleton_manager` -- Linux supervision across cutover and exec

`SingletonManager` claims Linux subreaper ownership before spawning, validates
successors against publication-time identity and ancestry, and watches them
through immutable pidfds rather than numeric-PID liveness. It holds a private
state-directory lease and persists the watched identity, manager identity,
boot ID, and bounded discovery phase in `manager.json`.
The private inherited-lease environment handoff is consumed once. The receiver
owns that descriptor on both success and validation failure; a rejected handoff
closes it rather than retaining the flock and poisoning a same-process retry.
Run it in a dedicated manager process: its entire descendant tree, including
helper processes, belongs to this lifecycle. Do not embed it in an unrelated
long-lived process whose other children must survive manager exit.

Supply distinct `config_dir` and `manager_state_dir` paths and a `spawn`
callback returning a process with `pid` and `poll()`. A `resolve_update`
callback may return a new absolute executable/argv when its version marker
changes, or `None` otherwise. The manager polls it while the daemon is alive;
successful exec preserves the manager PID and lease. The new image must enter
the same manager API with the same paths; it recovers the validated watched
process or pending discovery state without invoking `spawn` again.

The spawn callback must preserve manager ancestry and use the adopter's
platform-aware launch primitive. Fresh bootstrap refuses a live incumbent
without same-manager recovery state. On a real crash or an in-process error, cleanup freezes the owned
tree to a bounded fixed point and signals only held descendant pidfds; the
caller must propagate `ManagerResult.exit_code` to the service manager.
This is the manager's service outcome, not transparent child-status forwarding:
even a zero-status child exit without a verified successor means the continuously
supervised service has disappeared and yields a nonzero manager status. Intentional
service stop targets the manager/unit itself, not just its current daemon child.
State/exec/containment failures raise explicitly rather than continuing with
weaker guarantees.

This implementation requires Linux subreaper and pidfd support (provided through
Python or libc). Windows and macOS native backends are explicitly unsupported.
For abrupt manager death, the launcher must retain systemd's
`KillMode=control-group`; no in-process cleanup can run after SIGKILL.
Consumer launch wiring, real systemd validation, Windows ownership/handoff,
and platform deployment are tracked by the
[implementation effort](../../efforts/active/versioned-singleton-manager/README.md).

## Consumer contract

Every side-effecting collaborator is **injected**, so a consuming service stays
in control of its own process/health/drain semantics:

```python
from zdd.cutover import CutoverOrchestrator

orch = CutoverOrchestrator(
    config_dir,                        # where active.json lives
    bind="127.0.0.1", version="1.2.3",
    spawn_passive=lambda port: ...,    # start the new slot -> handle(.pid/.terminate/.poll)
    health_check=lambda host, port: ...,   # probe the new slot's readiness -> bool
    make_client=lambda base_url: ...,  # -> client exposing drain/undrain/shutdown[/adopt_relay]
    pick_free_port=lambda: ...,        # -> int
)
result = orch.run(health_timeout=60, drain_timeout=300, force=False)
```

The consumer additionally implements its own drain endpoint + semantics (when is
it safe to retire the old daemon?) and an **edge adapter** that makes its clients
follow the table -- short-lived clients re-read `active.json` directly; a service
behind a fixed external port (e.g. reached through a reverse tunnel) instead has
a hop watch the table and re-point at the live port.

## Vendoring

On `dev`, consumers may reference `libs/zdd` canonically via
`[tool.uv.sources]` with `editable = true` instead of carrying a local
`plugins/<plugin>/libs/zdd` copy. Promotion materializes that canonical
reference back into a real local `libs/zdd/` tree for shipped `main` payloads,
so development stays DRY without weakening the self-contained release payload.

## Development

```bash
uv pip install -e ".[dev]"
pytest
ruff check .
```

`zdd` is pure stdlib and imports nothing from any consuming plugin -- keep it
that way.
