# SSH Reverse Candidate Forward

- **Slug:** `ssh-reverse-candidate-forward`
- **Repo:** copilot-extensions
- **Branch(es):** public source `greased-kiln`
- **Created:** 2026-10-22
- **Status:** Draft
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
(`efforts/active/dynamic-reverse-reservations/README.md`). It does not duplicate that campaign's authenticated
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
- [ ] Merge the governing vision/pattern amendment #7726 under its actual
  required checks. Approval without merge is not completion.
- [ ] Land this narrow transport proposal through normal review/CI.
- [ ] Publish the exact optional API and result semantics in #7720 for #7719;
  distinguish admitted candidate, retained transport, readiness and unknown.

### Phase 2 - Existing forward-owner implementation

- [ ] First capture the fixed-candidate occupied-port failure and actual
  readiness gap with focused existing-owner tests.
- [ ] Add optional finite positive loopback candidates without changing the
  fixed-port default. Require candidate-specific readiness for dynamic mode.
- [ ] On a positively classified remote bind collision, retire only that
  attempt's owned process, then try the next already-admitted candidate within
  one bounded operation. Authentication, host-pin, permission, transport and
  unknown failures must not masquerade as a collision.
- [ ] Retain the successful process/listener, return its exact endpoint and
  owner correlation, and clear exposed readiness when that process is retired.
  No probe-close-bind reservation or incumbent-listener eviction.
- [ ] Keep cancellation, monitor/reconnect gate, host-target resolver and
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

The exact API is not implemented yet. Prefer optional constructor arguments and
an immutable candidate/readiness result on `SupervisedRelayForward`, with the
fixed relay-port path unchanged. Bound the explicit candidate set and share the
existing process creation, headless proxy and cleanup primitives.

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
