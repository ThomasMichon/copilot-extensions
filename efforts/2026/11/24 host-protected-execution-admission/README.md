# Host-Protected Execution Admission

## Goal

Produce a reviewable, concrete source contract for native host-scoped execution
admission that preserves configured interactive headroom and independent review
capacity. Distinguish proposed interfaces from implemented or deployed support.

- **Slug:** `host-protected-execution-admission`
- **Repo:** copilot-extensions
- **Branch(es):** independent proposal PR; subsequent implementation unassigned
- **Created:** 2026-11-24
- **Status:** Done
- **Vision:** [purposeful placement](../../../../visions/agent-fabric/agentic-factory/agent-topology/README.md#purposeful-placement)
- **Umbrella issue:** [#14006](https://github.com/ThomasMichon/copilot-extensions/issues/14006)

## Guiding Intent

Extend the accepted native execution owner, not the topology helper or a new
private allocator. A reserved task, standing relationship, launch lease or
positive preview must not be presented as protected host execution capacity.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Proposal source host | Own this contract proposal and its PR stewardship | Source codename `lopsided-widget`; #14006 |
| Dispatch admission maintainers | Review policy, accounting, transaction and compatibility semantics | #14006 and proposal PR |
| Worktrees and Bridge maintainers | Review real launch/resume integration and native outcome custody | Proposal PR's caller matrix |
| Future implementation owner | Unassigned; requires an accepted bounded source assignment | #14006 after proposal review |

## Coordination

- **Topology:** one source-only proposal; no agent or runtime rollout.
- **Host (owns PRs):** proposal source host.
- **Delegates:** none.
- **Handoff:** accepted contract and explicit remaining implementation gates stay
  under #14006. This proposal does not claim another contributor's code.
- Preserve [#9709](https://github.com/ThomasMichon/copilot-extensions/issues/9709)
  and [#13204](https://github.com/ThomasMichon/copilot-extensions/issues/13204):
  assignment, running-lane startup, retry and retained transitions remain theirs.
  Any implementation touching those seams needs their actual coordination.

## Context

The [source-owner decision](https://github.com/ThomasMichon/copilot-extensions/issues/14006#issuecomment-7143664285)
accepts Dispatch execution admission as the enforcing source-design home.
Worktrees/Bridge retain launch and incarnation custody; the adopter's existing
host/venue policy owner selects capacity and protected classes.

The [contract](contract-v1.md) specifies proposed callables, durable state,
accounting, fences, caller coverage and truthful unsupported cases.
The [validation and delivery plan](validation-and-delivery.md) names the real
consumer and mixed-version evidence implementation must eventually provide.

This closes a source-design ambiguity, not a live resource gate.
[#12137](https://github.com/ThomasMichon/copilot-extensions/issues/12137)
delivered advisory admission, not an enforcing provider.
[#6132](https://github.com/ThomasMichon/copilot-extensions/issues/6132)
addressed pool accounting, not affirmative host reserves.

## Request

Continue the bounded public-safe contract proposal and normal source review
after #14006's source-placement decision. Specify the versioned callable/state
boundary, native fences, participating cohort and unsupported manual/external
coverage. Keep eventual implementation separately assigned and gated.

This is the public-safe source scope. Private operational particulars and their
original authorization evidence remain with the external source host; they are
not prerequisites or public inputs to this portable contract.

## Scope Approvals

Source contract/documentation authoring, bounded offline validation and normal
PR stewardship only. No runtime code, implemented API, source-worker appointment,
host policy selection, native reservation, installation, service change or live
qualification is included. Source review never grants deployment or overrides
another owner's acceptance.

## Plan

### Phase 1 - Concrete contract proposal

- [x] Define source ownership, versioned proposed operations/state and mandatory
  native accounting/fences without advertising implemented support.
- [x] Map finite, standing, interactive and retained execution callers; refuse
  unsupported unmanaged coverage rather than promise host-wide protection.
- [x] Specify compatibility, migration, independent-gate and real-boundary
  acceptance before implementation.
- [x] Resolve proposal review and merge the documentation through normal gates
  in #14442.

### Phase 2 - Explicit successor disposition

- [x] Record the reviewed contract and unresolved implementation decisions under
  #14006, then conclude only this proposal slice. Implementation and installed
  qualification need their own accepted owner and reviewed execution plan; see
  the [successor disposition](https://github.com/ThomasMichon/copilot-extensions/issues/14006#issuecomment-7146462094).

## Validation Plan

- [x] Check proposal links, source-symbol references and documentation whitespace.
- [x] Obtain normal source PR review and required CI without bypassing CI or
  outstanding feedback; the authorized Maintainer merge omitted only the
  additional CODEOWNER vote.
- [x] Verify publication and keep implementation tests explicitly prospective;
  no source/runtime, clean-room or live-consumer pass is claimed by this PR.

## Proposal

See [contract version 1](contract-v1.md). Every new name in that document is a
**proposed** contract; none is a current CLI, endpoint or runtime capability.

The proposal advances existing topology purposeful-placement intent and native
resource composition; it adds no second queue, controller or automatic lifecycle.
It follows [version-skew-safe service contracts](../../../../docs/patterns/service-contract-evolution.md):
provider-first support, selected-running-owner negotiation, effect-side
enforcement, strict requests and explicit rollback refusal.

## Journal

### 2026-11-24 - Finite proposal completed

[#14442](https://github.com/ThomasMichon/copilot-extensions/pull/14442)
merged as `051c21da84004dcf343991c384d6c8a05002f675` after
[Copilot approval](https://github.com/ThomasMichon/copilot-extensions/pull/14442#pullrequestreview-6048413265)
of exact head `d10551983f6a8c860038991094e39e532beef406`, green observed
required checks and resolution of the reserve-preservation review thread.
Interactive and review admissions must each preserve the other class's reserve;
both symmetric exhaustion cases are prospective tests, not runtime evidence.
The normal authorized Maintainer merge omitted the additional CODEOWNER vote,
not CI or unresolved feedback.

Offline checks passed: effort structure, documentation consistency, nine local
Markdown links, seven existing source-symbol references and whitespace.
Runtime unit, real-consumer, clean-room, native concurrency and live external
tests were not applicable to prose-only proposed interfaces; no such pass or
installed capability is claimed.

The [source issue disposition](https://github.com/ThomasMichon/copilot-extensions/issues/14006#issuecomment-7146462094)
keeps #14006 open for separately assigned provider/state implementation, caller
composition, compatibility/old-writer qualification, release and authorized
installed adoption. No implementation worker, numeric host policy, reservation,
installation or activation follows from this finite proposal's completion.
The contract remains proposed, not implemented; this archive is its reviewed
design record, not current runtime documentation.

### 2026-11-24 - Proposal authored

Concrete contract and validation plan prepared after the recorded source-owner
decision. No source implementation, actor, policy, reservation or rollout.
