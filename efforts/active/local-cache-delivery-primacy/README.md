---
visions:
  - visions/harness-guidance
---

# Local-Cache Delivery Primacy

- **Slug:** `local-cache-delivery-primacy`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-phase worktrees
- **Created:** 2026-10-02
- **Status:** Active
- **Vision:** `visions/harness-guidance` -- vision-extending. Reframes
  `resilient-safety-boundary` (and the worktree-scoped dynamic guidance
  pattern it governs): the lifecycle-hook-rendered
  `.local.instructions.md` sibling becomes the **primary** delivery path
  for projected instruction content; the checked-in,
  sync-worker-maintained copy becomes strictly the **fallback** for a boot
  where no pre-session hook could render anything fresher.
- **Umbrella issue:** [ThomasMichon/copilot-extensions#4925](https://github.com/ThomasMichon/copilot-extensions/issues/4925)
- **Builds on:** `efforts/2026/10/02 ambient-guidance-navigability`
  (Phase 7 landed the mechanism this effort reframes and extends -- read
  that effort's Journal for the mechanism's own design history before
  working here).

## Guiding Intent

`ambient-guidance-navigability` Phase 7 built the worktree-scoped local
cache (`docs/patterns/worktree-scoped-dynamic-guidance.md`) as a
*supplement* to the checked-in projection: "the checked-in copy remains
the unconditional floor," with the local cache patching the specific gap
of an ordinary contributor lacking push rights to fix sync-lag themselves.

The operator's stated intent is a **reprioritization**, not just a gap
patch: the lifecycle-hook-rendered local cache should now be understood
and documented as the **primary** way per-plugin instruction content
reaches a session. The checked-in, scheduled-sync-worker-maintained copy
is downgraded in framing to being purely the fallback that exists to
handle two cases -- which, in turn, means the precedence *mechanism*
itself needs one real fix (not just reframing) to actually deserve that
trust: a fallback that a stale leftover local file can silently outrank
is not a safe fallback (see Phase 1's stale-sibling item below). With
that fix landed, the mechanism serves:

1. A launch path where no lifecycle hook runs at all before the session
   starts (a fully headless/hookless/sandboxed invocation).
2. A launch path where a hook *could* run but hasn't yet had the chance to
   render fresher content before the very first read (a pre-sync gap this
   effort's Phase 2 does not need to close, since the local-render boundary
   already closes it wherever it's wired).

Operationally, this means two things:

- **The vision and pattern docs need rewording** to state this priority
  order explicitly, rather than leaving the local cache's role implied as
  secondary rescue plumbing beside a canonical checked-in source of truth.
- **Every pre-session boundary that *can* render the local cache before a
  session starts should do so** -- today, only `agent-worktrees`
  (create/resume/`sessionStart`) is wired. `agent-bridge` spawns Copilot
  sessions through several distinct paths -- `target.type == "local"`
  (direct same-filesystem spawn via a Session-Host), containers, GitHub
  Codespaces, an SSH/remote mesh, and `target.type == "command"` (a
  generic spawn-command shape that *also* covers Codespaces, containers,
  elevated relays, and other providers with no `target.cwd` at all,
  per `session_start.py`/`agent_registry_resolver.py`) -- and currently
  wires none of them. This effort's first slice closes **only
  `target.type == "local"`** (the one path proven to have the same
  direct-filesystem access to an explicitly daemon-local `target.cwd`
  that `agent-worktrees`' own wiring relies on -- **not** "command",
  which is not a reliable proxy for locality); every other target type,
  including `command`, is an explicitly out-of-scope follow-on slice
  (confirmed with the operator), since each needs a *remote-exec* variant
  of the render call rather than a direct filesystem call, or simply has
  no local `cwd` to render against at all.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent (this repo) | Vision/pattern doc reframing (Phase 1), `agent-bridge` local-spawn-path wiring (Phase 2) | independent per-phase worktrees, this repo's own PR flow |

## Coordination

- **Topology:** independent per-phase worktrees, each its own PR.
- **Host (owns PRs):** the driving agent, for both phases.
- **Delegates:** none.
- **Handoff:** each phase closes with its own PR merged before the next
  starts; a fresh session may pick up at either phase boundary from this
  doc's Journal.

## Context

See `efforts/2026/10/02 ambient-guidance-navigability`'s Journal for the
full design history of the mechanism this effort reframes:
`docs/patterns/session-scoped-dynamic-guidance.md` (per-session computed
facts) and `docs/patterns/worktree-scoped-dynamic-guidance.md`
(worktree-scoped projected instruction content -- the pattern this effort
directly amends).

`agent-bridge`'s session-spawn paths live in
`plugins/agent-bridge/src/agent_bridge/session_start.py`
(`SessionStartCore.start_session`, called from
`routes/sessions.py:start_session` and `routes/worktrees.py:resume_worktree`).
Only `target.type == "local"` resolves a `target.cwd` directly on the same
filesystem the agent-bridge daemon itself runs on -- directly analogous to
`agent-worktrees`' own `worktree_creation._create_worktree_core` /
`resolve_launch_cli._resolve_resume_context` call sites. Every other target
type -- container, Codespace, SSH-mesh, and the generic `"command"` shape
(which itself also covers Codespaces, containers, elevated relays, and
other providers, often with **no** `target.cwd` at all per
`session_start.py`/`agent_registry_resolver.py`) -- resolves a *remote* cwd
(or none) the daemon has no direct filesystem access to; rendering there
needs a remote-exec call, or simply does not apply (a follow-on slice, not
this effort's Phase 2).

## Request

Operator (verbatim, this session): "The intent of my effort here is that
the *primary* way of delivering per-plugin instructions is now via the
live-copy to `.local` mechanism. The checked in instructions should now
only be a 'fallback', so handle cases where an agent boots in a state
where it won't run hooks, or be able to sync *before* the session starts,
like what agent-worktrees (and agent-bridge) can provide."

Scoping follow-up, confirmed by the operator: the vision reframing is
exactly as proposed (no additional nuance to the scheduled sync-worker's
own cadence); `agent-bridge` wiring is scoped to the local spawn path
first (`target.type == "local"` specifically -- narrowed from an initial,
incorrect "local/command" framing, since `"command"` is not a reliable
proxy for locality; see the Journal), with every other target type as
explicitly named follow-on slices; no other pre-session boundary besides
`agent-bridge` was flagged.

## Plan

### Phase 1 -- Vision and pattern-doc reframing
- [x] Revise `visions/harness-guidance/README.md`'s `resilient-safety-boundary`
      behavior to state the lifecycle-hook-rendered local cache as the
      primary delivery path for worktree-scoped projected instruction
      content, with the checked-in copy as strictly the fallback for a
      hookless boot or a not-yet-rendered pre-session gap. **Landed.**
- [x] Revise `docs/patterns/worktree-scoped-dynamic-guidance.md`'s framing
      (Problem/Standard approach/Rationale sections) to match: the
      checked-in copy is introduced as the fallback tier, the local cache
      as the primary tier, not the reverse. Note the stale-sibling fix
      below *does* change the actual precedence mechanism -- this phase
      is reframing *plus* that one correctness fix, not reframing alone.
      **Landed**: a precedence note up top, §2/§3's own preamble/catch-all
      text, §5's "floor" wording, and the Exemplars pointer all updated.
- [x] Cross-check `docs/patterns/session-scoped-dynamic-guidance.md` for
      any framing that would now read inconsistently with this
      reprioritization (it covers a different, per-session-computed-facts
      case, so likely needs no change -- confirm rather than assume).
- [x] **Design and land a stale-sibling invalidation check**: today a
      local sibling is preferred purely by existence
      (`instruction_projections.py`'s `render_projection` preamble), and
      is only ever reconciled when `render_local_cache()` itself runs
      (`_render_local_cache_locked`'s stale-removal pass, which only
      fires for a *disabled* source, not a merely-outdated one). A sibling
      left by an earlier *successful* render can therefore silently
      outrank a *newer* checked-in projection on a later boot where no
      pre-session render succeeds (hookless boot, a bounded-timeout miss,
      or any other render failure) -- precisely the boot this effort's
      reframing says must fall back safely to the checked-in copy.
      **Design:** a render *timestamp* cannot establish freshness -- an
      older/regressed installed payload rendered *after* the sync worker
      commits a newer checked-in projection would still carry the later
      timestamp and win, recreating the exact bug this item fixes; a
      changing timestamp field would also break `render_projection()`'s
      deterministic output and the local cache's own byte-idempotence
      check (`current == rendered.content`). Use the **stable provenance
      the markers already carry** instead -- `pluginVersion` and
      `templateSha256`, no new field needed -- comparing the checked-in
      projection's marker against the local cache's own marker for the
      same source, with the numerically newer `pluginVersion` winning.
      **Equal-version fail-safe:** a version string is not an immutable
      source identity (`projection_reflect.py`'s own documented caveat) --
      a dirty/local-checkout payload can differ without a version bump,
      so an equal `pluginVersion` needs its own tie-break, decided by
      `templateSha256` specifically -- **never whole-file bytes**, which
      always differ by construction (only the checked-in file carries the
      preamble at all) and would make the checked-in file win on *every*
      equal-version tie, inverting the local-primary policy for the
      by-far most common steady-state case (caught by PR #4947's own
      review, which correctly flagged an earlier draft's vaguer "content
      differs" wording as exactly this trap). A matching hash means
      nothing meaningful changed -> local wins; a genuine mismatch ->
      fall back to the named **checked-in** file (not merely "this file",
      which is ambiguous to a reader comparing two files from outside
      either one's own context -- naming it explicitly closed a real
      misattribution failure mode found during validation, see below).
      **Landed**: rewrote `render_projection()`'s per-file preamble and
      the shipped `local-cache-catchall.instructions.md` template
      accordingly -- this is reading-agent-level precedence text (the
      mechanism has no Python-level enforcement point for a boot where no
      render ever ran), not a numeric comparison inside the render code
      itself. Kept deliberately terse to stay within the aggregate-budget
      tests' existing margins (two already-trimmed templates needed a
      further few bytes shaved). New unit tests assert both rendered
      texts literally contain the `pluginVersion`/`templateSha256`/
      "newer"/"tie" comparison language, and `tools/run-plugin-tests.py
      customizing-copilot`: 310 passed, 8 skipped -- full regression
      coverage, no existing preamble/marker test broken. The wording's
      equal-version/differing-hash fail-safe case has a known,
      separately-tracked reliability gap in clean-room validation --
      see the Validation Plan item below and
      `ThomasMichon/copilot-extensions#4960`.

### Phase 2 -- `agent-bridge` local spawn-path wiring
- [ ] Add an `agent_bridge`-side equivalent of
      `agent_worktrees.local_cache_refresh` (resolve customizing-copilot's
      declared `render-local-cache` CLI the same bounded-timeout,
      never-raising, global-activation-scoped way Phase 7's
      `agent_worktrees.local_cache_refresh` does -- no cross-plugin
      reach-around; see `docs/patterns/a-la-carte-independence.md`).
- [ ] Eligibility is **`target.type == "local"` only** -- never `"command"`
      (that shape also covers Codespaces, containers, elevated relays, and
      other providers, often with no `target.cwd` at all; see
      `session_start.py`/`agent_registry_resolver.py`). Confirm the
      resolved `target.cwd` is a real, local, trusted directory before
      calling the renderer.
- [ ] Call it from `session_start.py`'s `target.type == "local"` branch,
      after `target.cwd` is resolved and before the Copilot CLI process is
      actually spawned, bounded by a **short, explicit latency budget**
      (a few seconds -- sized against this plugin's own existing
      `SESSIONSTART_MAX_TIMEOUT_S`-style precedent, not the create/resume
      path's more generous 30s default, since this sits directly in the
      spawn's own critical path) -- completing the render before spawn
      necessarily gates spawn for up to that bound; it is never
      unbounded, and a timeout or any other render failure is absorbed
      (spawn proceeds against whatever the checked-in floor already has)
      rather than failing the spawn itself.
- [ ] Guard test: a synthetic repo with a stale checked-in projection and
      a divergent installed payload gets its `.local.instructions.md`
      sibling refreshed by a local-spawn (`target.type == "local"`)
      `start_session` call, proven against the real `render_local_cache()`
      call (not a stub), completing within the defined latency budget.
- [ ] Negative-proof test: customizing-copilot not installed, the repo not
      yet trusted, or a render failure must never fail the spawn itself,
      and the render call is proven bounded by the defined latency budget
      (not merely asserted zero-delay, which is unachievable for a
      synchronous pre-spawn render).
- [ ] Negative-proof test: a `target.type == "command"` (or any non-local)
      spawn never invokes the local renderer at all, including the
      specific case of a `"command"` target with no `target.cwd` present.

## Validation Plan

- [ ] `tools/run-plugin-tests.py customizing-copilot agent-bridge` (and
      `agent-worktrees` if its own tests are touched) pass;
      `check-changefile-presence` / `check-version-consistency` /
      `check-docs-consistency` clean on every PR.
- [ ] A clean-room-style proof (matching the methodology
      `ambient-guidance-navigability`'s own Phase 7 used) that a
      local-spawned (`target.type == "local"`) `agent-bridge` session
      actually sees the fresher `.local.instructions.md` content when the
      checked-in copy is stale -- not just that the unit-level render call
      fires -- and that the render's latency stays within Phase 2's
      defined budget.
- [x] **Stale-sibling-boot negative-proof, version-ordering case**: a
      `.local.instructions.md` sibling rendered from an older installed
      payload, left in place while the *checked-in* projection is
      subsequently updated to a genuinely newer version (the scheduled
      sync worker advanced it independently of this worktree's own local
      render), must be superseded by the checked-in copy on a boot where
      no render runs at all -- proven against the real marker-comparison
      logic Phase 1 lands, not asserted by inspection. **Landed**:
      frozen-snapshot clean-room scenario (real `render_projection()`
      output, independent skill/tool-forbidden `explore` sub-agents,
      matching `ambient-guidance-navigability` Phase 7's own methodology)
      -- **Scenario C**, 5/5 runs reached the correct final answer (the
      checked-in file's value) across two wording revisions; one run
      misattributed *which* file carried which version while still
      reaching the correct conclusion -- a labeling slip, not a
      precedence failure, noted rather than rounded up.
- [x] **Equal-version, matching-hash negative-proof** (the common steady
      state, nothing has changed -- local must still win, not fall back
      to checked-in merely because the two files' whole-file bytes
      differ): proven against the real marker-comparison logic, not
      asserted by inspection. **Landed**: **Scenario E**, 3/3 runs
      reached the correct answer *and* correctly identified the local
      file as authoritative, with correct reasoning (recognizing that
      whole-file length/the preamble's presence is *not* a precedence
      signal).
- [ ] **Equal-version, differing-hash fail-safe negative-proof** (the
      case where a dirty/local-checkout payload shares a declared version
      with the checked-in copy but has genuinely different content --
      fail safe to checked-in): **not reliably proven; left open rather
      than claimed complete** (caught by PR #4947's own review, which
      correctly declined to accept a passing claim this evidence doesn't
      support). **Scenario D**: across 5 runs relying on memory/`view` to
      compare two long, near-identical `templateSha256` values (both
      before and after renaming the ambiguous "this file" fallback target
      to explicit "this checked-in file"), only **2/5** reached the
      correct final answer -- every failure was a **transcription error**
      (the two hash values transposed between files), not a logic error
      in the stated rule; a run explicitly directed to use `grep` (whose
      own output is mechanically filename-prefixed, removing the
      transcription step) got it exactly right. The underlying
      precedence *rule* was never shown wrong, but an agent comparing two
      files from outside, relying on unaided memory, is a genuine,
      separate reliability risk this effort's own wording changes could
      not close. **Deferred to
      `ThomasMichon/copilot-extensions#4960`**: tracks the residual gap
      and the candidate directions (nudging the instruction text toward
      exact-match tooling, moving the tie-break to a deterministic code
      path where `render_local_cache()` already runs, or accepting the
      narrow residual risk as documented) -- not resolved inline here
      since none of those directions were a quick, obviously-correct fix
      within this PR's own byte/line budget, and forcing one without
      follow-up validation would repeat the exact "claimed solved, wasn't"
      mistake this item's own correction exists to avoid.

## Proposal

_Pending._

## Journal

### 2026-10-02 (cont.) -- Phase 1 landed: vision/pattern reframing + the stale-sibling fix
Picked up immediately after the plan PR (#4926) merged, per `planning-
efforts`' own "sync forward, then execute" sequencing.

- **Vision reframing**: `visions/harness-guidance/README.md`'s
  `resilient-safety-boundary` behavior now states the lifecycle-hook-
  rendered local cache as the primary delivery path for worktree-scoped
  projected instruction content, with the checked-in copy strictly the
  fallback.
- **Pattern-doc reframing**: `docs/patterns/worktree-scoped-dynamic-
  guidance.md` gained a precedence note up top, reworded §2 (the per-file
  preamble) and §3 (the catch-all) to describe marker-provenance
  comparison rather than existence-based preference, clarified §5's
  "floor" wording, and updated the Exemplars pointer. Cross-checked
  `session-scoped-dynamic-guidance.md` -- genuinely out of scope (a
  different, per-session-computed-facts mechanism), confirmed rather than
  assumed; no change needed.
- **The stale-sibling fix** (the real code change this phase carries):
  rewrote `render_projection()`'s per-file preamble and the shipped
  `local-cache-catchall.instructions.md` to direct the reading agent to
  compare the checked-in and local files' own embedded marker
  `pluginVersion` fields -- prefer whichever is newer -- replacing the
  old existence-only "if it exists, prefer it" instruction.
- **PR #4947 review caught a severe tie-break bug in the first draft**:
  the initial wording resolved an equal-`pluginVersion` tie by "content
  differs," which is ambiguous between the *whole rendered file* (which
  always differs -- only the checked-in copy carries the preamble at
  all) and the *underlying template*. Read the first way, the checked-in
  file would win on *every* equal-version tie -- the by-far most common
  steady state -- inverting the entire local-primary policy this effort
  exists to establish. Fixed by comparing `templateSha256` specifically
  (already in both markers, no new field) on a tie: matching hash means
  local wins (nothing meaningful changed); a genuine mismatch falls back
  to the explicitly-named **checked-in** file (not vague "this file",
  which proved ambiguous to an outside reader -- see the validation
  findings below).
- Kept deliberately terse throughout: the final preamble is only ~29
  bytes longer than the pre-Phase-7 original (not the ~214-byte naive
  first draft), but the two already near-budget shipped templates
  (`agent-worktrees`' `worktree-context-guide` and `context-handoff`'s
  `awareness`) still needed a few more bytes trimmed each revision to
  stay under the aggregate-budget tests, and `instruction_projections.py`
  itself needed several comment-only trims to stay at its grandfathered
  2457-line module-size ceiling -- following the same precedent Phase 7
  itself set.
- **Clean-room validation, full and honest results** (real
  `render_projection()` output, independent skill/tool-forbidden
  `explore` sub-agents, matching Phase 7's own methodology): Scenario C
  (stale sibling superseded by a genuinely newer checked-in file) and
  Scenario E (equal version, matching hash -- local correctly stays
  authoritative) both came back clean across every run. Scenario D
  (equal version, differing hash -- the fail-safe-to-checked-in case)
  surfaced a real, reproducible failure mode: sub-agents comparing two
  near-identical marker JSON blobs from outside (not from within either
  file's own loaded context) repeatedly transposed the two
  `templateSha256` values **from memory**, reaching the wrong final
  answer in most runs -- a transcription error, not a logic error (a run
  explicitly told to use `grep`, whose output is mechanically
  filename-prefixed, got it exactly right). Reported this honestly in the
  Validation Plan rather than smoothing it into a false clean sweep; the
  precedence rule itself was never shown wrong, but relying on unaided
  memory to compare two long, similar hashes is a genuine, separate
  reliability risk worth knowing about.
- Added `test_render_projection_preamble_compares_marker_provenance_not_
  existence` and extended the shipped-catch-all test with the same
  content assertions, so the comparison language itself is guarded, not
  just the clean-room proof. `tools/run-plugin-tests.py
  customizing-copilot`: 310 passed, 8 skipped. `context-handoff`'s own
  suite: 34 passed (the 3 `test_emit_guidance.py` failures are a
  pre-existing, unrelated Windows `bash.exe`-availability gap, confirmed
  via `git stash` to predate this change).
- Phase 1's code and docs are landed; see the next entry for round 2's
  correction to this journal's own premature "fully proven" claim above.

### 2026-10-02 (cont.) -- PR #4947 review round 2: three more findings, honestly worked
Three further issues came back from the automated reviewer after the round-1
tie-break fix above:

- **The catch-all's own unit test couldn't actually prove what it
  claimed.** `test_customizing_copilot_ships_the_repo_wide_local_cache_
  catchall` asserted against the full *rendered* projection output, which
  always includes the auto-injected per-file preamble -- itself written in
  the same comparison vocabulary ("pluginVersion", "templateSha256",
  "tie"). The test could pass purely off the preamble's text even if the
  catch-all template's own body were wrong or missing that language
  entirely. Fixed to assert against `spec.template_content` (the catch-
  all's raw, un-rendered body) directly, and strengthened to check both
  tie outcomes explicitly (matching-hash -> local stays authoritative;
  differing-hash -> checked-in file wins).
- **The pattern doc's own literal examples drifted from the shipped
  text.** `docs/patterns/worktree-scoped-dynamic-guidance.md`'s §2 and §3
  code blocks still showed the pre-fix "content differs" wording from
  before the `templateSha256` tie-break landed -- the doc demonstrating
  the mechanism no longer matched the mechanism. Fixed both blocks to
  read identically to the shipped preamble/catch-all text.
- **This journal's own prior entry overclaimed.** The reviewer correctly
  declined to let "Scenario D: ~2/5" stand next to a Validation Plan item
  marked complete -- a validation gate that isn't actually met at an
  acceptable reliability cannot be checked off, even when the surrounding
  work is otherwise solid. Rather than force a false "fixed" or quietly
  drop the finding, split the single Validation Plan checkbox into three:
  Scenario C (version-ordering, 5/5) and the equal-version/matching-hash
  case (Scenario E, 3/3) stay `[x]`, each scoped to only what it actually
  proves; the equal-version/differing-hash case (Scenario D, 2/5, every
  miss a hash-transcription error rather than a logic error) stays
  genuinely `[ ]` and now points at tracked GitHub issue
  `ThomasMichon/copilot-extensions#4960`, which records the full failure
  mode, the `grep`-success counter-evidence, and three candidate
  remediation directions (left undecided -- none was an obviously-correct
  fix within this PR's own byte/line budget, and picking one without
  follow-up validation would repeat the exact mistake this correction
  exists to avoid). The effort stays Active, not archived, so this
  doesn't block anything right now; it is a named, trackable gap rather
  than a silently dropped one.
- Re-ran `tools/run-plugin-tests.py customizing-copilot` after the test
  fix (310 passed, 8 skipped) and the module-size/budget checks after the
  doc-example edits (prose-only, no code/byte-budget impact).

### 2026-10-02 -- Kickoff
- Carved from a direct operator follow-up to the just-archived
  `ambient-guidance-navigability` effort: the operator clarified the
  Phase 7 mechanism's intended role is a reprioritization (local-render
  primary, checked-in fallback-only), not merely a supplementary patch.
  Confirmed scope with the operator (vision reframing as described;
  `agent-bridge` wiring scoped to local/command spawn path first,
  container/codespace/SSH as named follow-on slices; no other pre-session
  boundary flagged). Effort created, premise captured.
- **PR #4926 review caught two real issues, fixed same-session:**
  - This worktree's own `agent-worktrees` lifecycle wiring rendered 7 real
    `.local.instructions.md` siblings for this repo's own consumed
    sources (the exact dirty-tree gap the operator flagged live, in
    conversation, independently of this PR) -- and since this repo had no
    `.github/instructions/.gitignore` yet, `git add -A` picked them up and
    committed them. Untracked them (`git rm --cached`) and added the
    ignore rule here too (also landing separately via #4930, found and
    fixed as its own atomic pre-existing-issue commit the moment the gap
    was identified; a private downstream consumer repo with the same
    gap got the equivalent fix too, outside this repo's own history).
  - The Plan's own "local/command" framing was wrong: `target.type ==
    "command"` is a generic spawn-command shape that also covers
    Codespaces, containers, elevated relays, and other providers, often
    with no `target.cwd` at all -- not a reliable proxy for "same
    filesystem as the daemon." Narrowed Phase 2's eligibility to
    `target.type == "local"` only, added an explicit negative-proof item
    for non-local target types, and replaced the "never gates/delays the
    spawn" claim (internally contradictory for a synchronous pre-spawn
    render) with a defined short latency budget modeled on this plugin's
    own `SESSIONSTART_MAX_TIMEOUT_S` precedent.
- **PR #4926 review round 2 caught a genuine architectural gap:** the
  existing precedence mechanism prefers a local sibling purely by
  existence, with no freshness check -- so a sibling left by an earlier
  *successful* render can outrank a *newer* checked-in projection on a
  later boot where no pre-session render succeeds. This was always true
  under Phase 7's original framing too, but this effort's reframing
  (declaring local-render primary) makes it load-bearing rather than a
  cosmetic edge case: a "fallback" a stale leftover can silently shadow
  is not actually safe. Added an explicit Phase 1 Plan item (a marker-
  timestamp comparison the preamble/catch-all directive text must apply,
  not "prefer local merely because it exists") and a matching Validation
  Plan item -- this is now real code work for Phase 1, not pure prose.
