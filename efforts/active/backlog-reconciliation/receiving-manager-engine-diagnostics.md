# Manager-to-engine diagnostic receiving proposal

Back to [Backlog Reconciliation](README.md).

## Intent and acceptance boundary

This is a research-only proposal to retain #8312's complete ordinary delegated
update contract directly in the existing #7743 integration issue. It does not
implement either request, authorize another native run, or transfer an owner's
execution or resource custody. Both issues remain OPEN at proposal time.

The complete current originals and discussions were studied: #8312 has its
original and three comments; #7743 has its original and twenty-eight comments.
A subsequent complete metadata qualification found both unions unchanged,
both original authors eligible, no assignments, and no new discussion or edit.
Historical statements of taking a slice are not current reservation proof.
Absence of an issue assignee is not absence of execution custody.

The reason for this route is the actual captured-output boundary: #8312's
ordinary Manager-to-engine handoff is one of #7743's two reached integration
hops. A bypass completing a payload refresh is not acceptance of that path.
The source's two exit-one incidents and later bounded stream diagnosis remain
unresolved; #8395 is reported as a merged documentation-only plan, not a fix.

## Complete source-to-receiver crosswalk

The proposed receiving clauses below are direct unfinished requirements, not
references that require reopening #8312 to discover the work.

| Clause | Retained #8312 requirement or evidence | Direct acceptance in #7743 |
|---|---|---|
| ME1 | Normal Manager-delegated update twice exited one; `--no-manager --wait` completed a payload refresh. | Reproduce and qualify the ordinary delegated path. Preserve the two failures as incident evidence; bypass success cannot count as a normal-path repair or identify either old failure's cause. |
| ME2 | Useful diagnostics must cross the Manager-to-engine boundary. The bounded source finding concerns the actual scoped `run_engine_passthrough` call. | Exercise that real default call with a native captured parent and both stdout and stderr. Preserve actionable failure output, phase/source and request context when actually captured, and the original nonzero result rather than an unexplained exit-one summary. Do not invent unavailable child output. |
| ME3 | Explicit headless submissions, terminal-aware repository bootstrap and repair-child controls are distinct paths. | Do not substitute any of those controls, or the successful bypass, for default-path acceptance. Cover the normal caller's actual stream destinations and participant handoff. |
| ME4 | Serial execution, normal installation guards, participant handoff and no-window behavior must survive. | Keep those protections through success, failure and interruption; diagnostic forwarding must not create concurrent installation or detach an unaccounted child. Preserve the child's actual wait/exit result. |
| ME5 | Driver/installer logging, retained-child settlement and broker-removal work already have independent owners. | Keep each existing owner's responsibilities and evidence. This intake adds an integration acceptance clause, not a replacement logger, broker owner, retained-child owner or execution reservation. |
| ME6 | No successful default-path implementation or native receipt is established by the supplied union. | Leave technical acceptance unchecked until the actual default boundary passes its scoped controls. Source/plan publication, local tests and historical reports remain separate from promoted/installed behavior. |

## Incumbent receiver and coordination

All #7743 text, discussions and existing check states remain intact. In
particular, retain the dependency-free fresh-installer entry before installed
Python/package/Manager probes; configured source, arguments and ownership;
CMD/PowerShell/POSIX parity; genuine failure propagation; and both actual
captured-output hops.

The reported sixteen-target native full-suite and ordinary-entry successes
are separate episodes from the later failed consumer request. Preserve the
575.016-second exit-one result and 140-second publication-lock refusal as
failures, not healthy no-op or suite completion. The native owner's condition
requires predecessor owner **AND descendant** settlement, fresh routing and
cleanup-confirmed no-launch evidence with primary-error identity before the
bounded retry. That condition is not met by process absence. Uncertain launch
keeps conservative custody; an unused retry is not permission to spend it.
The separate guest kernel/restart hold, single-pass cost, persistent logging
and shared-lifetime owners remain unchanged.

No source token supplied in #8312's complete discussion requires that tracking
identity to remain open; this is not a claim that no reservation exists
elsewhere. Before a disposition, refresh both issues' coordination metadata and
apply any actual owner/open-token condition. Do not contact or reassign an owner,
run a test, restart a guest or release a claim as part of this proposal.

## Planned receiving and separate judgment

- [x] Land this complete planned contract through the normal reviewed PR flow:
  #12747 merged after current-head approval and required checks.
- [x] Requalify source and receiver; reconcile any material new text, owner,
  assignment or explicit tracking-token condition before publication.
- [x] Append clauses ME1-ME6 and the incumbent/coordination boundary directly
  to #7743 without replacing its original text or prior discussion.
- [x] Read back the complete authored receiving contribution under the original
  publisher identity; verify all six clauses and retained conditions.
- [x] Separately judge whether #8312 is redundant planned tracking. If the
  accepted receiver retains its whole unresolved contract and no actual token
  condition conflicts, explain the unfinished work and close `NOT_PLANNED`,
  never `COMPLETED`. Otherwise leave #8312 OPEN with the concrete gap.
  The [actual receiving contribution](https://github.com/ThomasMichon/copilot-extensions/issues/7743#issuecomment-6900167239)
  directly retains all six unchecked clauses. The separate
  [source explanation](https://github.com/ThomasMichon/copilot-extensions/issues/8312#issuecomment-6900183663)
  and provider readback confirm #8312 CLOSED `NOT_PLANNED`; #7743 stays OPEN.

## Validation and technical work that stays open

- [x] Verify the accepted public receiver against every crosswalk row, including
  the ordinary-path-versus-bypass distinction and existing owner conditions.
- [x] Preserve every incumbent #7743 check state and responsibility.
- [ ] Under its own authorized owner, exercise the actual ordinary scoped
  handoff with native captured stdout/stderr, real return-code propagation,
  serial/guard/participant/no-window controls, failure and interruption cases.
- [ ] Keep #7743 OPEN until its own complete original and later acceptance
  conditions are resolved; this proposal settles no native retry, installed
  qualification, guest restart, source hold or resource release.

The first two validation items concern receiving and tracker judgment. The
latter two remain technical acceptance with their existing owners; tracking
consolidation does not have to implement that work.
