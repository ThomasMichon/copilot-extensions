# Client Request Interpretation

- **Slug:** `client-request-interpretation`
- **Repo:** copilot-extensions
- **Branch(es):** serial proposal and implementation PRs against `dev`
- **Created:** 2026-10-29
- **Status:** Draft
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
- [ ] Land this proposal through the normal review and merge flow.
- [ ] Preserve the original local-only, operator-owned one-shot boundary.

### Phase 2 - Align snapshot launch settings
- [ ] Default to the operator's current model/effort/context preferences;
  preserve explicit overrides and fail clearly rather than inventing a model.
- [ ] Use a 300-second default finite timeout and update the guidance.

### Phase 3 - Interpret locally by default
- [ ] Write a local companion JSON report after successful capture, without
  changing the raw bytes or printing private content to stdout.
- [ ] Identify instruction full-content/body matches with source paths,
  transformations and overlapping/ambiguous-match accounting.
- [ ] List observed skill and subagent metadata, distinguishing complete
  rendered metadata, names-only advertisements and source frontmatter matches.
- [ ] List declared and advertised tools, configured-name MCP correlations,
  builtin classifications and unknown ownership separately.
- [ ] Describe observed discovery/search surfaces and their schema capabilities,
  without claiming that searches ran or returned their full discoverable set.
- [ ] Report memory counts/bytes and builtin-prompt byte accounting with
  observed, source-matched, unsupported and unknown states as appropriate.
- [ ] Support offline interpretation of an existing local request.

**Agent-recommended implementation details:** reuse existing read-only
inventory/settings helpers, keep payload-only operation, use exclusive local
report writes, bound filesystem discovery, and never execute hooks or contact
servers merely to inventory them. Count decoded UTF-8 bytes separately from
wire JSON bytes. Exact builtin bytes or memory totals remain unknown when no
supported attribution boundary exists; expose measured system totals and
unclassified remainder separately instead of fabricating completeness.

### Phase 4 - Qualify, publish and reconcile
- [ ] Validate with focused synthetic parser/launcher fixtures and applicable
  repository checks; preserve raw-body and privacy regressions.
- [ ] Apply the offline analyzer to an operator-approved existing snapshot
  locally, emitting only report metadata to the coordinating conversation.
- [ ] Land the reviewed implementation and release changefile through the
  normal merge/promotion flow, without an installed-consumer rollout.
- [ ] Resolve the complete Plan and Validation Plan, then archive.

## Validation Plan

These are agent-recommended checks for the requested behavior.

- [ ] Configured model/effort/context and explicit override precedence are
  exercised without a real provider call; missing/invalid settings fail clearly.
- [ ] The default timeout is 300 seconds and an explicit timeout still wins.
- [ ] Exact Unicode/raw JSON is preserved; companion output never overwrites
  an existing file or prints captured text, descriptions or memory bodies.
- [ ] Fixtures cover instruction body versus selector reference, identical
  files, newline transformations and non-overlapping byte-span totals.
- [ ] Fixtures distinguish rendered skill/agent metadata from names alone,
  source frontmatter, loaded bodies and runtime execution.
- [ ] Function and custom/grammar tools, builtin/MCP/unknown ownership,
  deferred advertisements and discovery schemas remain distinguishable.
- [ ] Absence of recognized memory boundaries is not reported as proof of
  zero; system-message or leftover bytes are not exact builtin-prompt bytes.
- [ ] Offline analysis works without inference, hook execution, a running
  sibling service or publication of private snapshots/inventories.
- [ ] Required CI/review and release checks pass; limitations are reflected
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
