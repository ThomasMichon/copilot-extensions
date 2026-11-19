# Finite nullable catalog capability

[Back to Session Discovery](README.md) | Source: [#13489](https://github.com/ThomasMichon/copilot-extensions/issues/13489)

## Outcome and custody

Deliver only the explicit capability/name facet of S4036-P1/P2/LV1. The
bounded source owner is `patient-knob`, reserved on #13489. The parent stays
Draft; its discovery axes, S4036-P3/P4 static reload, #60 state/mutation
custody, #1460 compatibility foundation and retained-inbox repair remain
independent. No whole-parent completion, runtime rollout or resource action
follows.

The selected representation is `supports_worktrees: true | false | null`.
True means declared support for Bridge worktree-scoped dispatch; false
means declared lack of support; null means unknown. A missing field in a
legacy response also means unknown. Capability is not readiness, liveness,
reachability, permission or a replacement for the operation's admission.

## Design

Use the existing agent configuration and namespace row declaration owners,
not another registry or a probe:

- Add an optional nullable declaration to the existing ordinary agent
  configuration and namespace row models. Preserve false exactly; accept
  only actual booleans or null. Strings, integers and other malformed
  declarations must not silently become true/false.
- Carry the declaration through existing ordinary configuration parsing,
  namespace-list parsing, row serializers, roster cache and applicable
  detail. Absent declarations remain null. Project, root, spawnability,
  transport, discovery eligibility and a local repository's class cannot
  substitute for the target owner's declaration.
- Account for configured remote ACP separately against Bridge's actual
  supported request contract. Publish false only where that operation
  explicitly lacks the worktree-scoped operation; otherwise retain null.
  Do not contact a peer to classify the row.
- Preserve qualified project names, explicit display-name overrides,
  aliases and agent identity. Existing derived-name behavior is positive
  baseline, not a reason to rewrite producers or claim static reload.
- Keep dispatch/allocation/hosting/head/claim admission unchanged. Do not
  call namespace resolution, create, SSH or live discovery for passive
  metadata. Preserve resolver-absent responses and incomplete/last-good
  cache semantics.

This closes a finite catalog facet of the existing
`topology-aware-agent-resolution` vision, not a standing vision revision.
It follows the single-owner/derive-don't-duplicate and
[service-contract evolution](../../../docs/patterns/service-contract-evolution.md)
patterns: declare only supported semantics and retain unknowns.

## Compatibility and consumer boundary

Provider-first additive JSON expansion: older consumers can ignore the
new key and keep their prior behavior. Current consumers must interpret an
absent/null field as unknown, never false; malformed values cannot establish
support. No CLI option, request field, effect-side policy, protocol support
floor, required consumer or automatic migration is introduced.

Use the existing row/response contract and selected running producer.
Document the additive key and legacy omission. Do not add a universal
handshake, per-row subprocess, package-version test or unrelated wire
generation change. If an actual supported strict reader cannot tolerate
this expansion, revise the finite design before publishing that writer.

There is no established current reader of this new field. The `agents`
CLI/JSON/streaming, target-selection and resume/handoff paths already
consume the containing inventory; keep their behavior unchanged. This
slice does not claim a Manager UI integration or begin filtering targets.

## Finite Plan

- [ ] C1: Extend existing declaration and serialization seams with strict
  nullable support, preserving ordinary, namespace, remote and detail
  distinctions and legacy unknown behavior.
- [ ] C2: Prove the real declaration-to-JSON/HTTP/cache path and existing
  qualified names/aliases/overrides without running any target operation.
- [ ] C3: Document semantics and version-skew behavior, clear focused
  managed tests, whole-touched-file strict typing, relevant source
  contracts, review and exact-head required CI, then merge.
- [ ] C4: Record finite source acceptance and settle only #13489's own
  reservation. Leave parent/global/static-reload/native/installed duties
  and every independent owner unchanged.

## Validation Plan

- [ ] V1: True/false/null/omitted declarations survive actual ordinary and
  namespace parsing, serialization, cache and applicable HTTP detail/list
  responses; invalid values do not become support. Legacy producer or row
  omission stays unknown.
- [ ] V2: Routing-field-only and wrong-host/local-class controls cannot
  imply support. Test remote ACP against the actual unsupported-operation
  boundary. Trap resolver/launch/probe effects in passive catalog tests.
- [ ] V3: Qualify project-derived display names, explicit overrides and
  aliases, including local/remote derivation, without identity changes.
  Incomplete and last-good cached rows preserve truthful capability.
- [ ] V4: Old-reader/new-response, new-reader/legacy-response and provider
  rollback controls retain prior selection/admission and explicit unknown.
  No new field automatically hides a target, denies an existing operation
  or changes the chosen provider.
- [ ] V5: Record unit, actual contained consumer, clean-room and external
  evidence separately. Clean profile/legacy inputs are contained source
  controls; no installed runtime, live provider, remote venue or rollout is
  required or authorized by this passive JSON change. Record any genuinely
  unavailable tier rather than claiming native execution.

## Completion

Accept only C1-C4/V1-V5 after real source qualification and merge. Do not
mark S4036-G or the discovery effort Done: same-consumer static reload and
the other original requirements remain outside this finite slice.

## Journal

### 2026-11-19 - Reviewed plan and finite source qualification

The plan merged in #13490 before production edits. Seventeen initial
consumer cases fail on absent metadata, ignored invalid declarations and
the prior namespace row shape. The final contained packet passes 308 cases,
including 21 focused HTTP/cache/declaration/legacy-rollback controls.
Remote ACP's existing refusal executes before its false metadata assertion.
An added cache test explicitly expires its fixture observation; force-refresh
alone intentionally does not rescan a fresh healthy namespace.

All seven touched Python files pass whole-file strict checking for Linux
and Windows. Necessary annotations preserve actual optional/dynamic roles
without changing admission; the coroutine helper's cast expresses its
declared result through Python's untyped `StopIteration.value`.
The existing actor-free namespace packet gains ten decoder/declaration
controls and retains its prior 14 cases. Exact source bindings are refreshed.
Disposable Windows run44608558606 qualifies all24 cases at immutable
`5839798627dfc4953a516bcf4af790777532eed4`, with zero failures/skips and both
source/selection and contained-tool phases passed. No Python bytes change
in this journal follow-up.

The local full unit runner refuses before execution because an untouched
PowerShell file has CRLF working-copy bytes while its Git blob exactly
matches the retained LF hash. No unrelated source or admission hash is
changed. Required full unit CI, final-head review, source merge and finite
acceptance remain pending. No runtime, provider or installed rollout is claimed.
