# Qualified-source publication occurrence receiving

Canonical campaign: [README.md](README.md).
Decision record: [dated study](journal/2026/11.25-publication-occurrence-receiving.md).

**Status: proposed, not accepted receiving or source retirement.**
The existing OPEN #7378 is the proposed receiver. This is a frozen receiving
specification; after accepted publication, that issue owns the current
technical case states. This file is not a second live incident board.

## Route and boundary

Eighteen repository-owned automated reports record the same functional stage:
**Publish and read back the qualified source release before merge**, failing
at **Publish immutable source assets and verify all raw bytes before merge**.
Actual job metadata establishes that stage correspondence. Each report retains
a different signature, reported commit and execution. Their cleanup-only
excerpts do not establish a common cause or a particular repair.

The proposed route is **redundant tracking**, not technical completion,
obsolescence of the current publisher, or a claim that the failures are
duplicates of one bug. Every occurrence remains independently OPEN in the
receiver's direct matrix below. Only its separate leaf tracking token may
retire as `NOT_PLANNED` after real receiving acceptance.

This cohort contains only verified first-party automation status reports:
all eighteen are unedited, have no human discussion or assignee, and retain
their original report content. It includes no unfinished human or third-party
request. The existing receiver predates this consolidation; it is not a new
campaign-filed duplicate. The third-party-request disposition rule is unchanged.

#8033 is deliberately excluded: its specific #8115 repair and subsequent
publication were independently verified and acknowledged `COMPLETED`.
That proof is not extended to these eighteen cases. Also exclude #7352's
separate diagnostic, #7872's source-unit reuse failure, #7377's periodic
portfolio, #7370's module split, and all other independently owned work.

## Preserve recurrence routing before retirement

The current watchdog's `_existing_issue` selects the first OPEN
`ci-failure-signature` issue returned by the literal search
`"Signature: <key>"`. A missing match takes the issue-creation path, which
records a new-issue number for the fix-attempt dispatcher. An existing match
uses the configured issue-level cooldown or a recurrence comment, without
recording a new-issue number. Bare hashes and a documentary receiving link
are therefore not a sufficient routing handoff.

The receiving matrix below preserves the exact `Signature: <key>` strings
in the existing receiver's **body**, alongside its existing selector label.
Its whole incumbent body remains verbatim, all incumbent comments remain
untouched, and the disclosed receiving section is added rather than replacing
another author's contract. Before composing the supported update, obtain the
immediate qualified current body/revision; independently audit its complete
preservation afterward. Read-then-write is not atomic. A concurrent change,
unknown write or preservation discrepancy blocks source retirement and needs
reconciliation, not replay.

Before each source transition, use the same read-only lookup as the producer:

```text
gh issue list --repo ThomasMichon/copilot-extensions \
  --label ci-failure-signature --state open \
  --search '"Signature: <key>"' --json number,updatedAt
```

Invoke it through the repository-scoped account wrapper. First verify that
the accepted OPEN receiver is actually indexed as a match. If the still-open
source is selected first, a staged tracker-only handoff may withdraw **only**
that source's `ci-failure-signature` selector label after the receiver is
already a verified live match. Preserve unrelated labels and ownership.
The source remains OPEN throughout this handoff. Do not retire it until the
same lookup selects #7378 first; search delay, lookup failure or another
selected target is not a pass. A blocked handoff stays open and any confirmed
own label withdrawal is reconciled without overwriting another owner's changes.

Verify the same route again after each source transition. Preserve the
producer's configured receiver-level cooldown; no per-signature comment-cadence
guarantee or new multi-case fix-agent capability is claimed. This campaign
does not invoke the reporter, fetch its raw logs, dispatch jobs, implement
routing code or run product tests. If the existing tracker-data/lookup
contract cannot establish a safe route, keep the affected source OPEN for
the existing producer owner rather than manufacture retirement.

## Proposed append-only receiving contract

The block between the following markers is the complete proposed addition
to #7378's body. Compose the final body as the exact top-of-body disclosure
`*The following contribution was assisted using Copilot.*`, the complete
unchanged incumbent body, and this block, separated by blank lines.
Do not duplicate the disclosure or modify incumbent words or comments.
Required plan review precedes publication; the returned whole receiving
contract and actual recurrence routing must then be accepted before separate
source judgments.

<!-- publication-occurrence-receiving:start -->
## Qualified-source publisher: eighteen independently unresolved occurrences

### Intent and current gap

Retain the complete publication-occurrence cohort here, alongside this
issue's existing release/tag and later admission diagnostics. The common
intended outcome is reliable qualified-source artifact publication before
Main merge. The reports identify the same actual publisher job and failing
step, but their excerpts contain post-job cleanup rather than a causal
diagnostic. **A common stage is not proof of a common bug.**

All rows below remain **OPEN technical cases**. Their original reports,
signatures, reported commits and execution references are retained individually.
No current failure reproduction, source repair or native/installed acceptance
is asserted. The separate fulfilled #8033 outcome supplies no resolution
credit for these rows.

### Direct occurrence matrix

For every row, the observed job is **Publish and read back the qualified
source release before merge** and the actual failing step is **Publish
immutable source assets and verify all raw bytes before merge**. The direct
unresolved condition is publication failure at the recorded execution with
the underlying cause not established by its cleanup-only excerpt.

| Clause / original | Signature | Reported commit | Actual failed execution | Technical state |
|---|---|---|---|---|
| P7587 / #7587 | `Signature: 582b747abd7c` | `6cf24adbae071dc3f06146de274d4e8a82298375` | [39594242116 / 119205209106](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39594242116/job/119205209106) | OPEN |
| P7803 / #7803 | `Signature: aa6f4b3b8f49` | `fea868b178df6fe700f7a87ebe771c80e2a75077` | [39735049914 / 119681906803](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39735049914/job/119681906803) | OPEN |
| P8027 / #8027 | `Signature: 2e16dae40bf4` | `dfacb5359159e14d1a199814bc8d233803d49bb6` | [39881828271 / 120183784626](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39881828271/job/120183784626) | OPEN |
| P8030 / #8030 | `Signature: 94dc47749f1f` | `bb37ce82b82e1f0108e37ede99da5e5d72f7e1a3` | [39882774817 / 120186607216](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39882774817/job/120186607216) | OPEN |
| P8039 / #8039 | `Signature: 7e68997ed378` | `958d3e2046bb395d54c7fe8d63280a5bd68e848b` | [39884763835 / 120192338735](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39884763835/job/120192338735) | OPEN |
| P8043 / #8043 | `Signature: 2bc068c64cb3` | `41913ba4c176c3f2e17ad7c375f9406ac6feba30` | [39885644486 / 120195108922](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39885644486/job/120195108922) | OPEN |
| P8047 / #8047 | `Signature: 1b5b24f00fb2` | `28410290f07629a3e910f70094c023913008dabb` | [39886355739 / 120197778576](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39886355739/job/120197778576) | OPEN |
| P8050 / #8050 | `Signature: 6f6081260690` | `c4dd7b0377a1c8c80600fde9bb661ed7da79fdd1` | [39887351817 / 120200535803](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39887351817/job/120200535803) | OPEN |
| P8053 / #8053 | `Signature: 5e6780fe6a44` | `eeff7c19cd8185ae5107fff1d6869bab8ed02493` | [39888777763 / 120205218309](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39888777763/job/120205218309) | OPEN |
| P8056 / #8056 | `Signature: 0a35b05e6614` | `67aa451179484ad459e93b3d0850318a45528ff9` | [39889713799 / 120208262798](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39889713799/job/120208262798) | OPEN |
| P8061 / #8061 | `Signature: 7192ee0b8ae6` | `68ef28f3c3968c057d2f524567b0293c6b99daac` | [39890170088 / 120211480941](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39890170088/job/120211480941) | OPEN |
| P8064 / #8064 | `Signature: 19fe7342890a` | `6e327f495f283406529782c2df064316f2e47965` | [39891265573 / 120214168372](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39891265573/job/120214168372) | OPEN |
| P8068 / #8068 | `Signature: acada56a6700` | `0062ba8f4d5cf2464f71603dc36bed7939288c9b` | [39892025996 / 120216786311](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39892025996/job/120216786311) | OPEN |
| P8073 / #8073 | `Signature: f6fe2bd38444` | `715709202b0f815f873236663a945865993da753` | [39893331883 / 120219704363](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39893331883/job/120219704363) | OPEN |
| P8074 / #8074 | `Signature: 256287fedfbb` | `e2bacb89a64c6128f468b645d22d4cc92a1b3c45` | [39894050459 / 120222503042](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39894050459/job/120222503042) | OPEN |
| P8075 / #8075 | `Signature: 990e7fbfe738` | `0a68c8e3a127be8af4393aa79dfed25f22fa783e` | [39894677903 / 120225060713](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39894677903/job/120225060713) | OPEN |
| P8077 / #8077 | `Signature: 6e8e17971222` | `f7d73325ec844b7e98206f45bfb43d9718166d9a` | [39895575955 / 120227627600](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39895575955/job/120227627600) | OPEN |
| P8080 / #8080 | `Signature: dde92fdf7df2` | `2f626fa830080a4cafaadb2743d333a89fad5681` | [39896405800 / 120230074396](https://github.com/ThomasMichon/copilot-extensions/actions/runs/39896405800/job/120230074396) | OPEN |

### Retained plan and acceptance

- [ ] Preserve every matrix row as a distinct unresolved occurrence. A source
  tracking retirement does not resolve its technical row or prove that its
  reported commit is the currently selected Dev or controller input.
- [ ] During appropriately authorized publisher triage, give each row a
  specific remaining actionable gap, delivering evidence, or an evidenced
  obsolete/superseded outcome. Unknown cases remain directly tracked here.
  Do not require rerunning an old commit merely to retain its evidence.
- [ ] Do not apply a fix, successful later run, source declaration or warning
  classification across rows without case-specific support. The inspected
  structured annotations do not supply the missing causal diagnostic.
- [ ] Preserve every unresolved row's literal `Signature: <key>` body marker
  and this OPEN receiver's `ci-failure-signature` selector while the producer
  needs that recurrence route. Source tracking retirement requires the
  producer-equivalent read-only lookup to select this receiver, not a new
  issue or an unverified target. Preserve its configured issue-level cooldown;
  no new fix-attempt dispatch or per-key comment-cadence guarantee follows.
- [ ] Preserve current candidate/source binding, qualification, signature,
  consumer-authority and exact raw-byte publication checks. No report
  consolidation permits an integrity waiver, tag/asset repair, controller
  activation, release rerun or native/runtime operation.
- [ ] Keep this issue's incumbent release/tag and admission diagnostics and
  all five existing human comments intact. Those requests are independent of
  this publisher matrix. Keep #8033's actual completion separate.
- [ ] Before resolving an occurrence, retain its specific outcome or accepted
  named follow-up directly. Do not silently drop an unknown case because
  another row was resolved. The receiver stays open while its incumbent
  requirements or accepted occurrence obligations remain unresolved here.

### Coordination and traceability

Existing release-pipeline/component custodians and all source, native,
installed, operational and resource obligations remain unchanged. Current
issue metadata is unassigned and unlocked; that is not proof of absent
external custody. This is planned diagnostic intake, not an implementation
appointment, resource handoff, new repair service or authority to execute.
An actual claimant, hold or source-open condition discovered before a
transition must receive its required coordination; it is not erased by this
matrix.

Every source original remains available as historical evidence without
rewriting its body or discussion. The proposed leaf-token reason is
`NOT_PLANNED` for superseded tracking, never `COMPLETED` for an unproven
repair. No human or third-party unfinished request is in this cohort, and no
such request is dismissed against a campaign-created duplicate.

The eighteen literal signatures and exact reported inputs above preserve
the useful incidents without copying complete transcripts. Their shared
functional stage, actual job/step correspondence and direct unresolved
conditions justify one visible open publication cohort, not a common-cause
claim. This receiving content supplies no new execution permission.
<!-- publication-occurrence-receiving:end -->

## Campaign delivery gates

These are tracker-delivery gates, not a requirement for this campaign to
implement or resolve the receiver's technical cases:

- [ ] Land the reviewed receiving specification through the normal PR flow.
- [ ] Requalify the current receiver, preserve its whole incumbent content,
  publish the exact disclosed body addition and independently read back the
  complete accepted receiving content with all eighteen rows still OPEN
  and all exact producer lookup markers present.
- [ ] Before each source retirement, validate its live recurrence route
  through the producer-equivalent metadata-only lookup. Verify the receiver
  is an indexed OPEN/selector-eligible match before any selective label
  withdrawal; keep the source OPEN until the lookup selects #7378 first.
  If this cannot be established within existing authority, retain the source
  open. No code change, product test, workflow replay or dispatch is licensed.
- [ ] Recheck each source's eligibility, content revisions, current custody
  conditions and direct mapping before its separate acknowledgment, proper
  tracking reason and independent provider readback, followed by another
  routing check. A changed, held or unroutable source stays open; unknown
  writes are reconciled, not replayed.
- [ ] Record actual effects and a fresh complete whole-queue census, keeping
  technical completions, obsolete-gate retirements and redundant tracking
  separate. Preserve every incumbent parent Plan/Validation state.

Before those gates pass, all eighteen source issues remain open and no
prospective reduction is counted. No source/copy hold or historical
acquisition/refusal condition is cleared by this receiving proposal.
