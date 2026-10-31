# Client Request Interpretation

- **Slug:** `client-request-interpretation`
- **Repo:** copilot-extensions
- **Branch(es):** serial proposal and implementation PRs against `dev`
- **Created:** 2026-10-29
- **Status:** Done
- **Vision:** `visions/harness-guidance`, `attributable-context-budget`
- **Umbrella issue:** #9609

## Guiding Intent

An operator-owned request snapshot should preserve the operator's selected
model context and explain what the client actually sent. Local interpretation
should identify observed instructions, metadata and tool surfaces, with exact
byte accounting where possible and explicit uncertainty where the request
does not carry provenance.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Own launch defaults, integration, review and completion | Managed source worktree |
| Evidence workers | Bounded inventory, wire-format and preference research | Coordinator delegation |
| Parser worker | Local interpretation and synthetic parser fixtures | Coordinator delegation |

## Coordination

- **Topology:** reviewed proposal, then implementation and completion deltas.
- **Host (owns PRs):** Coordinator.
- **Delegates:** read-only evidence tracks; the Parser worker may later edit
  only explicitly assigned parser/test files, without git operations.
- **Handoff:** compact evidence and changed-path reports; the Coordinator
  owns integration, required checks and completion.
- Raw snapshots and actual downstream inventories stay private and local.
  Public examples and fixtures are synthetic. No runtime rollout or changes
  to the operator's settings, hooks or provider service are authorized here.

## Context

The original capture capability landed in #9589 and released through the
normal marketplace pipeline. It preserves exact raw request bytes but uses
a small fixed model and a 120-second default, and reports only capture metadata.

The existing customization scanner already owns enabled-source discovery and
frontmatter inventories. Reuse its bounded read paths rather than inventing
another plugin loader. Preference translation should remain compatible with
existing model/effort/context launch rules and work without a sibling runtime.

A request exposes rendered metadata and tool declarations, not necessarily
original frontmatter, source ownership, memory serialization or builtin-prompt
boundaries. Inventory matches are evidence of content presence, not proof that
a particular file was loaded. Unmatched system bytes must not be relabeled
as an exact builtin prompt.

## Request

The operator confirmed the slug `client-request-interpretation`.

> Great. We should adjust the guidance to use the Operator harness' current
> model settings for the snapshot, and we'll need to update the default timeout
> to wha you used.
>
> It would be good to add some default parsing and interpretation of the
> response as part of the snapshot tool. Notably, I want a list of:
> 1. All instruction files whose contents appeared
> 2. Every skill whose name and frontmatter appeared
> 3. Every MCP server and tool which appeared
> 4. A list of all built-in tools which appeared
> 5. A list of all sub-agents whose frontmatter appeared
> 6. Information about tool-search, task-search, skill-search and other such tools
> 7. Number of memories and total bytes of memories
> 8. Size in bytes of Copilot's own built-in prompt

## Plan

### Phase 1 - Review the interpretation contract
- [x] Land this proposal through the normal review and merge flow.
- [x] Preserve the original local-only, operator-owned one-shot boundary.

### Phase 2 - Align snapshot launch settings
- [x] Default to the operator's current model/effort/context preferences;
  preserve explicit overrides and fail clearly rather than inventing a model.
- [x] Use a 300-second default finite timeout and update the guidance.

### Phase 3 - Interpret locally by default
- [x] Write a local companion JSON report after successful capture, without
  changing the raw bytes or printing private content to stdout.
- [x] Identify instruction full-content/body matches with source paths,
  transformations and overlapping/ambiguous-match accounting.
- [x] List observed skill and subagent metadata, distinguishing complete
  rendered metadata, names-only advertisements and source frontmatter matches.
- [x] List declared and advertised tools, configured-name MCP correlations,
  builtin classifications and unknown ownership separately.
- [x] Describe observed discovery/search surfaces and their schema capabilities,
  without claiming that searches ran or returned their full discoverable set.
- [x] Report memory counts/bytes and builtin-prompt byte accounting with
  observed, source-matched, unsupported and unknown states as appropriate.
- [x] Support offline interpretation of an existing local request.

**Agent-recommended implementation details:** reuse existing read-only
inventory/settings helpers, keep payload-only operation, use exclusive local
report writes, bound filesystem discovery, and never execute hooks or contact
servers merely to inventory them. Count decoded UTF-8 bytes separately from
wire JSON bytes. Exact builtin bytes or memory totals remain unknown when no
supported attribution boundary exists; expose measured system totals and
unclassified remainder separately instead of fabricating completeness.

### Phase 4 - Qualify, publish and reconcile
- [x] Validate with focused synthetic parser/launcher fixtures and applicable
  repository checks; preserve raw-body and privacy regressions.
- [x] Apply the offline analyzer to an operator-approved existing snapshot
  locally, emitting only report metadata to the coordinating conversation.
- [x] Land the reviewed implementation and release changefile through the
  normal merge/promotion flow, without an installed-consumer rollout.
- [x] Resolve the complete Plan and Validation Plan, then archive.

## Validation Plan

These are agent-recommended checks for the requested behavior.

- [x] Configured model/effort/context and explicit override precedence are
  exercised without a real provider call; missing/invalid settings fail clearly.
- [x] The default timeout is 300 seconds and an explicit timeout still wins.
- [x] Exact Unicode/raw JSON is preserved; companion output never overwrites
  an existing file or prints captured text, descriptions or memory bodies.
- [x] Fixtures cover instruction body versus selector reference, identical
  files, newline transformations and non-overlapping byte-span totals.
- [x] Fixtures distinguish rendered skill/agent metadata from names alone,
  source frontmatter, loaded bodies and runtime execution.
- [x] Function and custom/grammar tools, builtin/MCP/unknown ownership,
  deferred advertisements and discovery schemas remain distinguishable.
- [x] Absence of recognized memory boundaries is not reported as proof of
  zero; system-message or leftover bytes are not exact builtin-prompt bytes.
- [x] Offline analysis works without inference, hook execution, a running
  sibling service or publication of private snapshots/inventories.
- [x] Required CI/review and release checks pass; limitations are reflected
  in the living skill/usage documentation and the local report.

## Proposal

Extend the existing snapshot operation, not a shared inference proxy or an
always-on logger. The raw artifact remains authoritative; a versioned local
analysis report explains observed structure and inventory correlations.
Unavailable attribution is a visible result, not a zero or guessed origin.

## Journal

### 2026-10-29 - Proposal
- Claimed #9609 and completed three bounded evidence tracks before design.
- Confirmed existing scanner/preference prior art and the distinction between
  rendered metadata, wire declarations and authenticated source provenance.
- Implementation awaits the proposal's review and merge.

### 2026-10-30 - Implementation and qualification
- Proposal #9613 merged after approval and required CI.
- Implemented current-preference translation, a 300-second default, exclusive
  companion reports and the offline entrypoint. Shared scanner readers retain
  their existing defaults and add explicit config-root/comment/command controls.
- Synthetic capture, parser, offline-entrypoint and native CLI controls
  exercised the new behavior; existing settings-reader compatibility cases
  passed. Corrected duplicate newline-normalization evidence and continued
  parsing registered agent records across unindented descriptions.
- Independent review identified multiplicative metadata correlations. Indexed
  candidates and bounded records, comparisons and expanded evidence repair
  that issue; targeted independent verification reported no remaining issue.
- An approved private snapshot was interpreted locally without another
  inference call. Only counts/coverage were inspected in the coordinating
  conversation, and the input artifact remained unchanged. No private snapshot
  or inventory is part of the public branch.
- Exact memory totals and builtin-origin bytes remain explicitly unknown when
  the request lacks a supported provenance boundary. Measured system bytes and
  source-matched/unclassified accounting are not relabeled as builtin content.
- Implementation review/merge, normal release and final archival remain.
- Implementation review found incomplete MCP source discovery and resource
  accounting, plus caller-relative custom-instruction lookup. Added native
  project precedence, wrapped/bare maps, plugin inline/declared MCP sources,
  shared read quotas and bounded correlation evidence. Relative instruction
  paths now use the selected harness while existing scanner callers retain
  their default base. Focused regressions cover the supported forms and limits.
- Follow-up review found discovery reads outside those shared quotas and
  quoted metadata names that missed rendered aliases. Discovery now uses the
  same bounded cache as instruction/MCP reads, including settings and manifests;
  cached manifests remain usable at the quota boundary. Quoted skill and agent
  names normalize before alias generation. Existing scanner discovery contracts
  and focused inventory regressions pass.
- Selector-reference and memory-marker evidence now have explicit candidate
  and work limits, lower-bound counts on truncation, and sparse UTF-8 offset
  checkpoints rather than repeated full-prefix encoding. Multibyte and
  exhaustion regressions pass alongside the combined snapshot cases.
- A later review exercised missing frontmatter names, nested grammar
  definitions, and unterminated marker openings. Preserved agent filename
  fallback and skill warnings, added the native nested grammar shape, and
  replaced suffix-rescanning regexes with forward-only delimiter scans.
  Failed candidates are bounded and disclosed rather than silently omitted.

### 2026-10-30 - Reviewed implementation merged
- Implementation #9632 merged after a current-head Comment review with no
  open Medium/High findings and required green CI. All reported findings were
  addressed, including malformed-input and resource-boundary regressions.
- The final correction packet passed 75 focused snapshot cases; existing
  scanner discovery contracts also passed. The final offline interpretation
  retained exact input bytes and emitted no analysis warnings.
- Merge CI passed. Normal promotion remains the final release check; no
  installed-consumer rollout is included or authorized by this effort.

### 2026-10-30 - Release and completion
- Normal promotion run 41037628770 succeeded and release candidate #9652
  merged to Main. The released `customizing-copilot` version is
  `0.2.48-dev1`; capture/analysis payloads and shared inventory helpers match
  implementation merge #9632 byte-for-byte.
- All Plan and Validation Plan items are resolved. Archived the completed
  effort and retained the capture skill/usage reference as living guidance.
- Installed-consumer rollout remains outside this effort's scope. No
  installed plugin, operator setting or shared inference service was changed.
