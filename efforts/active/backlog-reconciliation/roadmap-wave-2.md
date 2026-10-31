*The following contribution was assisted using Copilot.*

# Second 200-Issue Stabilization Proposal

Parent: [Backlog Reconciliation](README.md), Phase 2b.
Method: [Vision-Linked Stabilization Roadmap](stabilization-roadmap.md).

**Status:** Research proposal, not an implementation charter. This second,
disjoint wave accounts for 200 more issues: **168 researched proposals and
32 explicit provisional outcomes**. It does not establish 168 missing patches,
complete the remaining roadmap, or satisfy the parent's numerical checkpoint.

## Coverage and evidence

| Area | Records | Researched proposals | Provisional | P1 | P2 | P3 |
|---|---:|---:|---:|---:|---:|---:|
| [Bridge](roadmap-wave-2-bridge.md) | 50 | 45 | 5 | 20 | 28 | 2 |
| [Lifecycle](roadmap-wave-2-lifecycle.md) | 50 | 38 | 12 | 16 | 32 | 2 |
| [Transport](roadmap-wave-2-transport.md) | 50 | 37 | 13 | 13 | 34 | 3 |
| [Worktrees](roadmap-wave-2-worktree.md) | 50 | 48 | 2 | 17 | 30 | 3 |
| Total | 200 | 168 | 32 | 66 | 124 | 10 |

Together with the [first wave](roadmap-wave-1.md), this yields **400 planning
records: 337 researched proposals and 63 provisional outcomes**, with a
consequence-based overlay of 167 P1, 215 P2 and 18 P3. Neither wave established
a P0/current emergency. That observation does not add a no-outage veto or
prevent investigation of an important unverified consequence.

The latest complete whole-queue census used here was observed on
**2026-10-28 at 08:37 UTC**: 933 open issues, including 681 creator-eligible
and 252 excluded metadata-only records. After these 400 assignments,
281 creator-eligible issues still need this roadmap assessment, subject to
fresh full-content eligibility. New arrivals remain unranked until researched.
This publication is not a new whole-queue census and claims no issue closure.
The fixed target remains at most 729 whole open issues, counting inflow and
excluded authors.
These are frozen wave snapshots; later census changes belong in the parent's
dated journal rather than silently changing the research cohort.

Research used exact original/refinement text after complete actor, collaborator,
author/editor and nested-metadata gates, followed by one terminal metadata
check per assignment. There were 826 current original/refinement text records
and 68 actually returned necessary edit texts across the four reports. Two
requested historical versions were unavailable; other historical versions
that were not needed or fetched are not represented as read. Metadata equality
was not used as a substitute for text equality.

Vision, effort and narrow source evidence are pinned to
[`9dc3491fc527`](https://github.com/ThomasMichon/copilot-extensions/tree/9dc3491fc527bbf5561a082341c2b12c44343087).
The appendices give exact vision paths/headings/excerpts and actual pinned
effort status/Plan/Validation/Coordination waypoints, including absent sections
or homes. These establish research fit, not receiving acceptance or a new
implementation owner. All original issues remain whole-contract authorities.

**No new current-code defect audit, product test, native experiment, release,
installed activation or rollout was performed in this wave.** Narrow inspected
or rebound source excerpts corroborate only their stated observations. In
particular, #4196 still lacks the exact supported external SDK schema witness.
This wave must not promote a reported defect into a demonstrated current fix.

## What should be worked first

The choices below are **agent-recommended**, consequence-based first slices.
They supplement, rather than replace, the [first wave's source-fix versus
qualification distinctions](roadmap-wave-1.md). That earlier shortlist retains
its twelve bounded source-supported defects, two qualification-only cases,
one already-fixed predicate and one evidence/design gap at its stated pin.
Do not schedule duplicate implementations of #5791, #5754 or #5356.

### A. Characterize important possible source defects

These are high-value investigations for the existing owners, **not newly
confirmed missing implementations**. Preserve credited foundations and start
with the real consumer, not a title, unchecked box or mocked-past helper.

| Issue | Why this boundary matters | First useful slice and finite exit |
|---|---|---|
| [#5843](https://github.com/ThomasMichon/copilot-extensions/issues/5843) | Reconnect can conceal an already completed consequential operation. | Trace generic caller admission/identity and a lost-response-after-effect case. Reissue only proven pre-submit, idempotent/deduplicated or authoritatively recovered work; otherwise preserve explicit uncertainty and one effect. |
| [#4193](https://github.com/ThomasMichon/copilot-extensions/issues/4193) | A small tail response can still mutate continuity or await an unbounded live writer queue. | Characterize the actual cursor/tail read and flush boundary. Prove read-only AND finite behavior under continuous append while retaining ACK, rebuild and genuine writer failures. |
| [#4117](https://github.com/ThomasMichon/copilot-extensions/issues/4117) | An observational preview must not allocate a checkout, branch, claim, seed, record or process. | Verify real embody-new preview admission before any allocation. Exit with truthful planned/unresolved results and no listed effects; create-then-delete is not preview purity. |
| [#3789](https://github.com/ThomasMichon/copilot-extensions/issues/3789) | Denied, malformed or unreadable activation state must not become absence and permit overwrite. | Characterize activation read/write outcomes and path containment. Preserve prior bytes/selection on failure and distinguish true absence from uncertainty. |
| [#5855](https://github.com/ThomasMichon/copilot-extensions/issues/5855) | Migration must not overwrite an operator's deliberately disabled reaper. | Exercise explicit-zero, absent, nonzero, marker and repeat cases at the existing configuration boundary; preserve unrelated values and actual policy authority. |
| [#4194](https://github.com/ThomasMichon/copilot-extensions/issues/4194) | Optional query filters are not a trust-domain authorization policy. | Define and characterize the real principal/domain compatibility decision for ordinary and explicitly narrowed retrieval. No corpus access or new policy authority is granted here. |
| [#4756](https://github.com/ThomasMichon/copilot-extensions/issues/4756) | Successful create-or-read POST is not proof the previously confirmed fork existed before the effect. | Characterize prior existence/404 and fresh deliberate consent, retaining the delivered private catalog and remote repair. Keep this whole consent contract independent of #5791's credential dispatcher. |
| [#5903](https://github.com/ThomasMichon/copilot-extensions/issues/5903) | Malformed present cell state must not fabricate an endpoint or authorize legacy reach. | Qualify the actual tolerant parser/discovery contract with object, alternate and field-shape controls, preserving typed failure and strict readiness. |
| [#5851](https://github.com/ThomasMichon/copilot-extensions/issues/5851) | Copied build inputs do not prove generated cleanup stays outside the source checkout. | Confirm the actual native callback/caller routing for generated writes AND cleanup. Preserve source, preexisting/concurrent artifacts, packaging and provenance on success and fault paths. |
| [#5410](https://github.com/ThomasMichon/copilot-extensions/issues/5410) | A reported constructed-token defect is specific, not a universal authentication allegation. | First identify the genuinely governed remaining caller and actual transmitted value using secret-silent observations. Retain the retraction of the universal placeholder claim and do not add credentials to boundaries that do not require them. |

Each investigation exits with a demonstrated remaining defect and focused
repair proposal, credited existing delivery plus a qualification plan, or an
explicit unresolved evidence/design gap. None authorizes implementation,
credential access, public probing or operational effects in this campaign.

### B. Continue already-owned integration and qualification

| Existing work | Proposed bounded continuation | Important retained boundary |
|---|---|---|
| #3761 authoritative ground state and #5664 schema-bounded lifecycle state | One existing mutator or exact-ID/current-head consumer, with durable-before-ACK and bounded lookup/decode/copy/render evidence. | No parallel state authority, whole-programme gate or loss of older unsettled duties. Numeric intake is not a source-owner receipt. |
| #4876 artifact admission, #5472 immutable slots and #3905 source-independent installs | Compose one already-owned toolkit/lock/constructor path and one originally scoped consumer. | Optional governed source fallback remains valid; all thirteen adopters, Manager and original native/source-removal duties are not completed by one helper or import. |
| #4417 exact native input and #4564 headed-session restoration | Qualify the exact intended conversation/seed or original recovery partition through its existing native owner. | Staging/Enter/spawn is not consumption. #4564's actual readiness/owner hold remains unsatisfied; writer release or source inspection does not lift it. |
| #4136 termination policy, #5301 supervised units and #5953 keepers | One actual adopter or managed-unit boundary with action-time identity and truthful retirement/descendant outcome. | At most one lease-owning supervisor permits brief losing contenders and legitimate desired-zero states. No new reaper, PID/age takeover or kill permission. |
| #5286 plugin readiness, #5072 dependency closure and #5927 required-resolution errors | One selected actual local/remote/privileged consumer with its authorized source closure and real failure controls. | Current shared preparation is credited. Absent/unconfigured empty differs from failed-required selection; no claim that every launch still uses the old path. |
| #5761 discovery latency and #5555 pivot state streams | Complete the original finite cohort/consumer evidence with truthful partial and unavailable states. | Keep the unchanged eight-second cohort and actual caller totals; one fast or incomplete pass is not acceptance. |
| #5665/#5676/#5677/#5678 accounting and source evidence | One existing-owner lossless input, versioned query or daily product boundary. | Full input coverage, exact versus estimated/unknown values, dedup and real landing remain distinct; no source retirement or second accounting store. |
| #5789 routing, #5861 named effects and #5862 observation | A selected authenticated route, actual named effect-owner acceptance or scoped subscription. | Routing and receipts are not effects; observation is not control authority. Independent local branches do not wait for the whole Gateway programme. |

The role appendices retain further P1 candidates, exact owner constraints and
finite exits. These rows are a bounded starting set, not permission to seize
another participant's reserved source or to serialize every sibling project.

## Effort homes and decisions before execution

Prefer the existing canonical efforts, participants and building blocks linked
in the appendices. A related Draft/Active/Done source document is not a
whole-issue receiving receipt. Where no suitable whole home was established,
keep the original issue and its existing custodians rather than inventing a
mega-effort or transferring responsibility by documentation.

The 32 provisional records remain explicitly provisional. Important decisions
include:

- #4173's authoritative repository/adoption model, preserving all 34 directly
  received rows, current writers and original alternatives; one authority need
  not mean one physical file.
- #5543's BOTH-finalized versus untouched-unused pair convergence, under the
  original safe cascade OR reviewed cleanup-policy choice. No forced sibling
  finalization or rejected proposal is credited.
- #4053's complete disconnect/stop/terminate mapping and whole source/resource
  hold; #3977's actual server-independent interpretation; #5833's raw head
  override versus broader displaced-session intent.
- #3985's optional restricted-core mode and credential/session custody;
  #3989/#5686's actual independent engine owners/restart path, including the
  legacy missing Coordination/Participants evidence.
- #4040's strict wildcard rejection versus later selected exposure policy,
  #4102's scoped mux defaults, #3980's per-vault target authority and #4871's
  profile keep/deprecate choice.
- #3942's unidentified original two launch sites, #3993's actual spawning
  parent/wait custody and #5330's absent cited effort home.
- #3511's historical-exposure disposition and #5410's particular remaining
  consumer. Prevention, a current scrub or a rendered placeholder is not proof
  of the original historical or byte-level result.

Preserve independent evidence duties such as #5352's **all 32 review rounds
plus comparison sample**. Missing linked-provider history was not fetched
through this wave's own-issue authorization. An unavailable edit or outside
evidence gap remains named rather than reconstructed.

## Proposed sequencing

These are organizing buckets, **not a total order or new execution grant**.
The [32 scoped directed proposals and two interface edges](roadmap-wave-2-dependencies.md)
determine when a selected slice actually waits.

1. **Establish the exact consumer and retained owner.** Resolve provisional
   policy/source questions only where needed; characterize important possible
   defects and reuse delivered contracts. Independent ready leaves need not
   wait for every design question.
2. **Protect authority and durable work at that boundary.** Prioritize
   consequential replay, effect-free preview, truthful activation/configuration,
   query policy, explicit consent and source-write isolation investigations.
3. **Qualify startup, recovery and installed composition through current
   owners.** Keep exact seed/conversation, native identity, slot/source/toolkit,
   plugin readiness and original restoration holds independently visible.
4. **Compose actual fleet, evidence and consumer paths.** Prove named
   effect-owner acceptance, subscription recovery, complete source/accounting
   coverage and authoritative UI adoption at their own finite tiers.
5. **Continue independent P2/P3 and vision-extending slices when useful.**
   Bounded diagnostics, title/rendering, workflow clarity and structural work
   can proceed in parallel under their own review gates. #3791 and #5641 stay
   dormant until their original concrete demand conditions; optional #4274
   progress detail is not missing nonblocking execution.

Entry to any implementation stretch requires its existing scope, source/resource
custody, reviewed plan and actual applicable permission. Exit evidence must match
the original requirement: a finite source test is not a native adoption proof,
and a native hold does not spread to every independent renderer or parser leaf.
Release and installation remain separately tracked where required.

## Publication slice versus remaining parent gates

- [x] Publish this exact 200-issue proposal, appendices and scoped dependency
  register through normal review in #9388.
- [x] Reconcile the parent Plan/Validation and journal after the actual merge;
  preserve all provisional decisions and next eligible supply.

This finite publication does not complete the roadmap's 281 remaining eligible
assessments, the 63 cumulative provisional outcomes, any implementation/native
programme or the parent's fixed whole-backlog target. No issue was closed or
owner reassigned by the research.

## See also

- [First-wave findings and source distinctions](roadmap-wave-1.md)
- [Bridge appendix](roadmap-wave-2-bridge.md)
- [Lifecycle appendix](roadmap-wave-2-lifecycle.md)
- [Transport appendix](roadmap-wave-2-transport.md)
- [Worktrees appendix](roadmap-wave-2-worktree.md)
- [Scoped dependency register](roadmap-wave-2-dependencies.md)
