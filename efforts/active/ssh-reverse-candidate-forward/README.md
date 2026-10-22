# SSH Reverse Candidate Forward

- **Slug:** `ssh-reverse-candidate-forward`
- **Repo:** copilot-extensions
- **Branch(es):** public source `greased-kiln`
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** existing plugin-services and machine-fleet owned connectivity; governing amendment #7726 must merge first
- **Umbrella issue:** #7720
- **Sub-issues:** complementary fleet coordination #7719; existing integration #6681

## Guiding Intent

Extend the existing SSH forward owner with optional finite-candidate reverse
binding, retaining the actual successful SSH process and requiring
candidate-specific serving evidence before exposing a changed endpoint.
Preserve fixed-port consumers and the current supervisor by default.

This effort owns only the transport leaf of the canonical dynamic reverse
reservation campaign proposed in #7726
(`efforts/2026/10/22 dynamic-reverse-reservations/README.md`). It does not duplicate that campaign's authenticated
admission, generation publication or uncertainty ledger.

## Participants

| Participant | Role | Reached via |
|-------------|------|-------------|
| `greased-kiln` | SSH finite-candidate bind/readiness/process owner; this plan and implementation PRs | #7720 |
| `soldered-sprocket` | Complementary supplied-owner admission, generation publication and uncertainty layer; governing amendment | #7719 / #7726 |
| Existing Gateway, enrollment, profile and lifecycle owners | Retained policy, authentication, installation and effect custody | #6681 / #6443 / #6424 / #6240 |

## Coordination

- **Topology:** independent worktrees and disjoint source leaves.
- **Host (owns PRs):** `greased-kiln` for this leaf.
- **Delegates:** none. No new service, agent, credential or supervisor.
- **Handoff:** explicit accepted source boundary and evidence; no transfer
  inferred from time, missing session metadata or another owner's merge.
- Reserve only `libs/ssh-manager/src/ssh_manager/relay_channel.py`, narrowly
  necessary existing forward argument support, focused tests and library docs.
  Shared Fleet Manager policy/runtime/enrollment/profile/lifecycle and CLI files
  remain excluded.
- #7719 consumes this leaf's actual accepted contract and implements no second
  selector, retry loop, probe-close allocator or SSH process owner.

## Context

`SupervisedRelayForward` currently retries the same fixed reverse port four
times. That is appropriate for frozen existing consumers, but cannot select
another admitted endpoint while the old listener remains occupied.
`build_forward_ssh_args` already supports a dedicated reverse-only process
with `ExitOnForwardFailure=yes`; retain its strict failure behavior.

The current settle check observes process liveness and failure stderr, not
end-to-end reverse serving. A dynamic candidate therefore needs stronger,
candidate-specific readiness supplied by the owning integration.

The standing transport pattern and plugin-services vision previously required
OS-assigned ports unconditionally. #7726 proposes their narrow constrained
reverse-SSH exception. This plan grants no exception before that amendment
actually merges, and no forwarding policy after it merges.

## Request

Public-safe operator authorization:

> Authorize this coordinator to take an unclaimed isolated source/test slice

The decomposition below is **agent-recommended**. It authorizes neither
enrollment nor deployment and does not relax existing source claims.

## Plan

### Phase 1 - Reviewed contract and prerequisites

- [x] Deduplicate public issue/PR work and reserve #7720 with a public-safe
  visible claim; reconcile the disjoint #7719 source boundary.
- [x] Merge the governing vision/pattern amendment #7726 under its actual
  required checks. Approval without merge is not completion.
- [x] Land this narrow transport proposal through normal review/CI.
- [x] Publish the exact optional API and result semantics in #7720 for #7719;
  distinguish admitted candidate, retained transport, readiness and unknown.

### Phase 2 - Existing forward-owner implementation

- [x] First capture the fixed-candidate occupied-port failure and actual
  readiness gap with focused existing-owner tests.
- [x] Add optional finite positive loopback candidates without changing the
  fixed-port default. Require candidate-specific readiness for dynamic mode.
- [x] On a positively classified remote bind collision, retire only that
  attempt's owned process, then try the next already-admitted candidate within
  one bounded operation. Authentication, host-pin, permission, transport and
  unknown failures must not masquerade as a collision.
- [x] Retain the successful process/listener, return its exact endpoint and
  owner correlation, and clear exposed readiness when that process is retired.
  No probe-close-bind reservation or incumbent-listener eviction.
- [x] Keep cancellation, monitor/reconnect gate, host-target resolver and
  bounded cleanup coherent. Dynamic uncertainty must stop fresh allocation
  until its existing owner reconciles the original correlation.

### Phase 3 - Qualification and completion

- [ ] Exercise legacy behavior and the dynamic negative/concurrent/reconnect
  cases through the existing library consumer boundary.
- [ ] Run a real OpenSSH occupied-port test only against an existing explicitly
  supplied isolated fixture and its existing credentials; otherwise record
  this lane unexercised with the exact missing input.
- [ ] Add changefiles for actual consuming plugins per repository shared-library
  policy; complete documentation-impact review, source review and required CI.
- [ ] Merge the source and return the exact candidate/API/test evidence to #7719.
  Record release and separately authorized integration/adoption as retained
  obligations; do not close the parent integration from this source result.

## Validation Plan

All items are **agent-recommended** and require exact evidence.

- [ ] Fixed-port construction and all existing consumers remain unchanged when
  dynamic mode is absent.
- [ ] Invalid/empty/oversized/duplicate/nonpositive/nonloopback candidate sets
  refuse before spawning a process; a missing dynamic readiness adapter refuses.
- [ ] An occupied candidate advances only on a confirmed bind-conflict result;
  exhaustion is explicit and never widens grants.
- [ ] The actual successful process remains held throughout candidate readiness
  and return; its exact candidate is passed to the probe. A listening foreign
  endpoint or live process alone cannot establish readiness.
- [ ] Unknown/failed readiness, publication uncertainty and cancellation do not
  silently allocate another endpoint or publish stale success.
- [ ] Concurrent establish/stop/reconnect cannot replace a live owned resource
  or leave readiness pointing to a retired generation.
- [ ] Existing owned process/proxy cleanup and reconnect gating remain effective;
  no foreign process, system sshd, profile, credential or shared service changes.
- [ ] Real sockets/processes cover the actual boundary they exercise; they are
  not described as OpenSSH authentication/permission qualification.
- [ ] Optional OpenSSH fixture proves occupied/admitted alternate, finite
  exhaustion and denied tuple without issuing keys or changing host policy.
  Both `permitlisten` and peer `permitopen` remain existing authority inputs.

## Proposal

The source candidate adds `ssh_manager.reverse_candidates.FiniteReverseForward`,
an opt-in subclass of `SupervisedRelayForward`, with immutable endpoint,
attempt/evidence and status values. This keeps the established constructor and
every fixed-port consumer unchanged, reusing the existing process creation,
monitor, reconnect gate, headless proxy and cleanup primitives. The only base
hook defaults to the prior retry behavior; dynamic unknown/refused outcomes can
hold that same monitor instead of automatically allocating again.

The new module is the small necessary implementation leaf for the accepted
`relay_channel.py` boundary, not another supervisor or registry. Its exact
constructor/result and external observer obligations are documented in
[`libs/ssh-manager/README.md`](../../../libs/ssh-manager/README.md#optional-finite-reverse-forward-selection).
Source candidate is not review, release or production integration.

The policy owner supplies admitted exact positive loopback tuples; this parser
does not authenticate a caller or grant forwarding. The serving adapter must
bind its proof to the selected endpoint and owned attempt/correlation.
Only after that evidence may a caller request its existing generation-checked
publication. #7719 retains that publication and original-correlation ledger.
No library callback is advertised as fleet authority.

Keep `ExitOnForwardFailure=yes` on the reverse-only dynamic channel.
Preserve a confirmed distinction between bind collision and unknown failures;
do not rotate candidates merely because SSH returned255. Return bounded,
redacted cause codes rather than raw credential-shaped diagnostics.

## Journal

### 2026-10-22 - Bounded transport reservation

- Public claim `greased-kiln-7720`, comment6330788431, was the sole active
  #7720 claim on immediate re-read.
- #7719 owner explicitly excludes selector/reconnect/process code and consumes
  #7720's forthcoming accepted interface, comment6331115862.
- #7726's current governing amendment is not merged; actual required metadata
  cancellation remains tracked under #7741. No finite-candidate engine is
  implemented before that prerequisite clears.
- Existing selected tests expose no real sshd/credential fixture. This is a
  validation gap, not permission to create credentials or alter a running sshd.

### 2026-10-22 - Intent merged; bounded source and native resource witness

- #7726 actually merged as `5935a5d0bd81bb371235e7edf75be677cb5e0ae1`;
  this proposal #7757 merged as `aa27954a9b0818db2eea7e1a01535758a2254087`.
  Current-head approval and real required checks passed. The initial missing
  Documentation impact metadata was corrected and its thread resolved;
  independent effort-index rows were preserved through rebase conflicts.
- Re-read the merged plan and continued in the same source worktree. Added the
  opt-in subclass and base retry hook, not changes to existing fixed defaults or
  the reserved Fleet Manager/authentication/profile/lifecycle files.
- The native regression reproduces four failed fixed-port attempts while a real
  fixture socket holds the old tuple. The dynamic candidate retains a different
  real child-owned socket, validates that child's serving marker, survives an
  owned interruption through the existing reconnect path and reaps only its
  children while preserving the original listener.
- This witness launches an isolated Python socket fixture through canonical
  SSH process/proxy containment. It is **not an SSH server**, OpenSSH permission
  test, installed wheel consumer or production recovery. Existing real OpenSSH
  fixture/credentials remain unavailable; that qualification is still open.
- Final contained focused validation passed56 cases with one Linux-only
  owner-sweep skip on native Windows, including bounded diagnostic reasons and
  cancellation/stop before a delayed spawn returns. F/E9 lint and the existing
  install-contract gate passed. No runtime was installed.

### 2026-10-22 - Reconnect/lifecycle review corrections

- Initial source review of #7779 identified cleanup/replacement concurrency,
  per-candidate reconnect gating, stale readiness and default-target drift.
  These are material source defects, not findings waived by a green check.
- Serialize retirement, monitor restart and direct establish through one
  task-owned lifecycle boundary. Concurrent callers report busy without
  overwriting active state. Explicit stop cancels the active owner operation
  and preserves interrupted uncertainty. No descendant sweep can race a
  replacement owned by another transition.
- Invalidate the stored ready attempt before replacement, retain the initial
  local target across remote endpoint changes, refuse failed target resolution,
  and recheck stop/unknown venue gates before each monitor-driven candidate.
- Focused contained validation now passes63 cases with the same one
  Linux-only pidfd skip, including delayed retirement versus direct establish,
  old readiness during a delayed new observer, gate changes after a collision,
  stable local target and redacted cleanup failure. Current source review and
  actual merge are still pending; no release or adoption is claimed.
- Complete the same lifecycle correction across `start()` and shutdown:
  a concurrent start cannot reset stop intent while retirement is active.
  `stop_attempt(attempt_id)` performs matching and owned retirement atomically,
  making delayed consumer release inert against a reconnected successor while
  preserving an unknown result. This is a local transport fence, not fleet
  publication or enrollment authority.
- The final extended focused suite passes66 cases with the same one
  Linux-only skip, including stale attempt release and concurrent start/stop.
- A subsequent source review found the same ownership class in detached
  cleanup: interrupted refusal retirement and `stop_nowait()` could outlive
  lifecycle ownership, and failed late-spawn termination lost the process
  reference. Track one captured-process retirement task, block admission while
  any cleanup is pending, latch interrupted uncertainty and keep uncleaned
  process custody for later explicit stop. Cleanup never unconditionally clears
  a replacement handle.
- Add dedicated interrupted-refusal, delayed-nowait and failed-late-spawn
  cleanup regressions. The native fixture now enables `SO_REUSEADDR` only on
  POSIX for TIME_WAIT reconnects; Windows retains its occupied-listener
  semantics. Native Linux execution is still not claimed from Windows tests.
- A later review identified concurrent shutdown callers cancelling each other.
  They now share one shutdown-completion future, including attempt-specific stop;
  a repeated async or nowait shutdown joins/cooperates rather than cancelling the
  first teardown caller or manufacturing uncertainty after successful cleanup.
  Both shutdown forms followed by restart are covered.
- Current focused contained validation passes72 cases with the same one
  Linux-only skip. The existing local Linux container engine is unavailable;
  it was not started or repaired to manufacture platform proof. Source/test and
  canonical containment bytes were verified unchanged through the index-only
  rebase, preserving independent effort rows. Fresh-head review remains required.
- Review also exposed caller-finally shutdown re-entry and termination helpers
  returning without observed exit. Wait for lifecycle ownership release instead
  of whole-caller completion; retain process custody and unknown when its
  return code is still unset. Dedicated regressions cover both boundaries.
- The concrete attempt-fenced `finally` variant in a later review overview is
  fixed even though its displayed open-issue count was zero: join an existing
  shutdown before acquiring lifecycle ownership, then recheck the attempt.
  The extended native witness also runs two independent owners against one
  actual child-bound tuple; only one succeeds, and loser cleanup preserves
  the winner. Focused contained validation passes76 cases with the same one
  Linux-only skip. This still does not qualify OpenSSH or deployed recovery.
- Investigated the later nonspecific diagnostic/correctness concern rather
  than treating its zero displayed count as a clean verdict. The inherited
  reconnect-gate path could log raw provider exceptions, coerce invalid data
  and wait beyond the finite operation budget. The opt-in owner now bounds and
  redacts that callback, treats invalid data as unknown and checks stop intent
  again after it returns. Positive finite monitor intervals and callable owner
  hooks refuse invalid configuration before starting.
- Focused contained validation now passes82 cases with the same one
  Linux-only skip, including a synthetic private provider error absent from
  captured logs, bounded gate timeout, malformed result handling and no spawn
  after stop intent changes. Fixed-port legacy behavior remains unchanged.
- Apply the same narrow provider-exception redaction to the existing fixed-port
  gate without changing its endpoint, retry or return behavior. Its dedicated
  log-capture regression brings focused contained validation to83 passed with
  the same one Linux-only skip.
- Preserve a bounded untrimmed settle tail and join later diagnostic chunks so
  fragmented failure markers, including whitespace boundaries, remain usable.
  Normalize every observer exception before local typed-error handling so a
  provider-raised `ReverseForwardError` cannot bypass the original correlation's
  unknown latch or expose a provider reason. Focused contained validation is88
  passed with the same one Linux-only skip; existing trimmed diagnostic text and
  equality behavior remain unchanged.
