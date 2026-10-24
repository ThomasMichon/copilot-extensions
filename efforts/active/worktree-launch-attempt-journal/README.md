# Worktree Launch Attempt Journal

- **Slug:** `worktree-launch-attempt-journal`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed proposal and implementation PRs against `dev`
- **Created:** 2026-10-23
- **Status:** Draft
- **Vision:** `picker` / `cold-start-resume-prompt` and `durable-launch-attempts`
- **Umbrella issue:** #8375
- **Related:** #6057, #6176, #7073, #7962

## Guiding Intent

Every confirmed Worktree Manager launch action must leave a cheap, durable,
machine-local record before the UI dismisses or creation starts. An operator
can reconstruct what was requested, with which prompt and options, on which
venue, what actually happened, and whether a worktree and Copilot launch
resulted. A crash before worktree allocation must not erase the attempt.

Full prompts are protected private state retained for 90 days, never shared
activity-log fields, public reports or repository content. Read-only machine
sweeps return metadata by default and retrieve prompt text explicitly. An
attempt journal is evidence and recovery material, not a second launch queue
or permission to replay an ambiguous creation or prompt.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Journal driver | Contract, existing-owner integration, validation and PR stewardship | Managed source worktree |
| Validation venues | Isolated Windows and POSIX process-boundary checks | Existing resolved machine transports |
| Remote parity owner | Target-owned SSH prompt admission and delivered-source qualification | Existing #6057 effort |

## Coordination

- **Topology:** reviewed proposal, then cohesive bounded implementation slices.
- **Host (owns PRs):** Journal driver.
- **Delegates:** none assigned.
- **Handoff:** preserve request identity, private-state custody, exact source
  evidence and all unresolved Plan/Validation gates.
- Preserve #6057's original target-owned New/cold Resume contract and completion
  gates. #6176 supplies private submitted-prompt history, not complete attempt
  outcomes. Do not replace #7962's native mux ownership or claim its repair.
- Reuse actual provider/engine receipts. Existing prompt-consumption and
  first-turn receipt owners remain authoritative; a wrapper event is not proof
  that Copilot accepted a prompt.

## Context

Current Manager source saves prompt-only history from the confirmation widget.
It lacks a universal initial attempt record joining action, options, venue and
outcome. Engine activity has rolling lifecycle and preparation-phase events,
but attempts before allocation lack a worktree identity, prompt text is
deliberately absent, and phase step IDs are not a whole-attempt ID.

The October 23 report is not resolved by the older source merges. Inspected
Manager and engine versions are newer than the initial parity release.
Current Manager explicitly refuses seeded remote requests and hides the remote
composer; the engine also rejects remote `--require-native-seed`. Restoring SSH
requires a negotiated target-native fence, not deleting checks. That executable
repair remains part of #6057, with journal correlation integrated here.

## Request

The operator asked:

> Do we log each worktree-create attempt durably on each machine? Ideally you can sweep machines and their logs, and see which attempts I made, what the prompts where, and what the outcomes were. We need a quick path on each worktree-manager surface to record a log event that I attempted a "New worktree", "Resume", etc, what prompt and options I put in, what happened, and whether it successfully produced a Worktree and launched into Copilot.

The operator confirmed `worktree-launch-attempt-journal` and **90 days** of
full-prompt retention. The preceding report requires crash-resistant prompt
preservation and SSH creation delegated with the seed to the selected venue,
which owns execution beyond that boundary.

The following decomposition and detailed evidence rules are agent-recommended.

## Plan

### Phase 1 -- Reviewed attempt and privacy contract

- [ ] Review and merge this proposal before executable changes.
- [ ] Inventory all Manager New/Resume/Open entry surfaces, existing private
  history/restart stores, engine phase/lifecycle events and actual start/receipt
  owners. Define which events each can truthfully attest.
- [ ] Specify versioned attempt/event identities and private prompt retention,
  read defaults, failure behavior, cross-machine correlation and migration.

### Phase 2 -- Durable intent before effects

- [ ] Add a shared quick append path using existing private-state protections
  and transaction conventions. Persist action, exact submitted prompt/options,
  project, selected venue and attempt ID before dismissal or allocation.
- [ ] Cover empty prompts, rejected unsupported choices and cancellation without
  treating draft text or cancellation as accepted launch intent.
- [ ] Record failures explicitly. If required intent persistence fails, preserve
  the dialog/draft and refuse mutation rather than silently dropping the audit.
- [ ] Keep audit history separate from daemon-owned pending intent; retain
  New/Resume provenance and never manufacture a replacement request on retry.

### Phase 3 -- Existing-owner outcome correlation

- [ ] Carry the attempt ID through local and SSH boundaries, binding the
  target receipt and allocated worktree ID before later launch phases.
- [ ] Record per-owner progress, refusal, error and exit events without putting
  prompt contents in shared logs. Distinguish allocated, launcher started,
  Copilot process started and prompt handoff/acceptance where proven.
- [ ] Represent a missing terminal receipt after crash as interrupted or unknown,
  including allocated-but-not-launched cases; never infer success from a plan,
  a zero SSH exit, `copilot_invoked` alone or missing errors.
- [ ] Integrate negotiated SSH prompt admission with #6057. The selected venue
  owns allocation, seed staging and launch after admission; the host journal is
  not a second execution authority and cannot automatically replay uncertainty.

### Phase 4 -- Inspect, sweep and retain

- [ ] Expose a discoverable Manager history/detail path and bounded structured
  local reader, filterable by time, action, project, venue, outcome and attempt.
  Prompt contents require explicit detail retrieval.
- [ ] Add a read-only sweep over explicitly selected configured reachable
  machines through existing transports, preserving per-source errors,
  unavailable machines and source identity rather than reporting empty success.
- [ ] Enforce 90-day prompt retention, including a documented policy for legacy
  seed-history copies, SQLite sidecars, expiry and explicit deletion. Do not
  silently leave a second indefinite full-prompt copy or delete pending intent.

### Phase 5 -- Acceptance and original report reconciliation

- [ ] Land implementation through current-head review and required CI, then
  verify containing release and authorized installed source identities.
- [ ] Reconstruct recent real attempts using available logs without printing
  private prompts into public evidence; name historical gaps honestly.
- [ ] Exercise installed local and SSH New/cold Resume, including crash recovery,
  with exact attempt, worktree, conversation and prompt-handoff correlation.
  Return original prompt-loss and parity completion evidence to #6057.
- [ ] Settle owned validation resources, resolve every Plan/Validation item,
  mark Done, archive through review and release focus before finalization.

## Validation Plan

- [ ] Kill the process after confirmation, after journal commit, before/after
  target admission, after allocation, during setup and around native handoff.
  Retain the attempt and classify outcomes without duplicate worktrees/prompts.
- [ ] Test concurrent submissions, Unicode/multiline prompts, complete option
  fidelity, empty prompts, New/Resume provenance and prompt-free Open.
- [ ] Validate disk-full, permissions, corrupt/foreign schema, partial writes
  and lock contention with explicit failure and unchanged draft/mutation state.
- [ ] Prove protected Windows ACLs and POSIX permissions, linked-path refusal,
  prompt-free default output/errors and 90-day expiry including legacy copies.
- [ ] Use native Windows and POSIX subprocess boundaries and actual SQLite;
  establish a measured append-latency bound before claiming a quick path.
- [ ] Exercise lost SSH replies, unreachable targets, old capabilities and
  mismatched receipts. No automatic replay or host-side duplicate seed store.
- [ ] Verify cross-machine sweeps are read-only, bounded and explicit about
  unavailable/error sources. Prompt retrieval requires an explicit request.
- [ ] Require provider-specific start/receipt evidence where claiming launch or
  prompt acceptance; unsupported evidence remains unknown, not optimistic success.
- [ ] Run affected tests, lint and contracts, then record genuine installed
  local/remote proof separately from source and synthetic backend checks.

## Proposal

Use one machine-local append interface in the existing Manager private-state
ownership. Allocate a stable attempt identity at confirmation and persist intent
before effects. Existing receiving engine/daemon and launch owners append or
return correlated receipts rather than granting the journal new control powers.

Metadata readers omit prompts by default; explicit local/selected-machine
details expose protected prompt recovery. Do not provision a new always-on
service, silently collect a whole fleet, replay uncertain operations, or export
prompt text through general telemetry. Select the concrete schema and latency
budget after reviewing existing stores and consumer boundaries in Phase 1.

## Journal

### 2026-10-23 -- Confirmed operator intent and current gap

- Confirmed the effort slug and 90-day prompt retention; captured the request
  verbatim without private host, repository or actual prompt contents.
- Verified existing prompt-only recovery and engine phase logs do not provide
  the requested complete attempt-to-outcome audit. Found the current remote
  native-seed refusal; no executable safety fence was removed.
- Claimed #8375 and prepared reviewed intent before implementation. Original
  crash-loss cause and installed SSH acceptance are still open, not solved.

### 2026-10-24 -- Documentation CI detour

- The operator requested that Markdown-only changes select no executable
  coverage tests or code-contract scans, while retaining lint. Reviewed fixes
  #8406 and #8439 preserve release changefile enforcement and required outcomes,
  and use the pinned PR merge-base change set so advancing upstream code cannot
  create false overlap.
- This proposal remains documentation-only. Its next normal CI execution is
  the live check of explicit unit/source-scan skips and retained Markdown lint;
  no journal implementation, crash-loss repair or SSH capability is implied.
