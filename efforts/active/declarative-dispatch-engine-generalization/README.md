# Declarative Backlog & Review Engine Generalization

- **Slug:** `declarative-dispatch-engine-generalization`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-09-05
- **Status:** Draft
- **Vision:**
  [`visions/plugins/agent-dispatch/repository-issue-loop/`](../../../visions/plugins/agent-dispatch/repository-issue-loop/README.md)
  (declarative-turnkey-adoption, provider-neutral-backlog-capability,
  declarative-worker-identity) and
  [`visions/plugins/agent-dispatch/README.md`](../../../visions/plugins/agent-dispatch/README.md)
  (concise-event-then-charter-pull, preloaded-dispatch-supplement) and
  [`visions/plugins/agent-dispatch/reviewer/`](../../../visions/plugins/agent-dispatch/reviewer/README.md)
  (the open structural-guard question from its Phase 6 in
  `review-automation-reliability`)

## Guiding Intent

Make the declarative recipe engine (reviewer loops and repository-issue-loops
alike) as easy for a colleague on an unfamiliar team to adopt as it is for its
original author, provider-neutral rather than GitHub-only, driven by named
reusable worker identities instead of inlined prompt prose, and cheap to
embody per event instead of paying full instructional cost on every task.

## Context

Live use of the `odsp-web-harness-backlog` repository-issue-loop (validating
the #2056 terminal-reservation fix, see `review-automation-reliability`) and a
same-day conversation about extending the engine surfaced four related,
forward-looking gaps against the newly-extracted repository-issue-loop vision
and the parent agent-dispatch vision:

1. The `ForgeProvider` seam in `repository_issue_loops.py` is already
   provider-agnostic, but `validate_config` hard-gates
   `forge.provider != "github"` -- there is no second adapter, so pointing the
   engine at an Azure DevOps backlog is not yet possible.
2. Adopting a new loop today means authoring a private declaration whose
   `worker_guidance` is a long, hand-written prose blob (see
   `odsp-web-harness-issue-loop.json`, dotfiles) -- there is no library of
   reusable, named worker identities a new adopter can just select.
3. `embody.autopilot_worker_prompt` inlines a large, generic "how to behave as
   an agent-dispatch worker" instructional essay into **every** embodied
   worker's seed, regardless of the actual event that triggered embodiment
   (new work vs. a submitter update vs. a steer answer) or whether the worker
   already knows this material from a prior turn.
4. The reviewer vision's Phase-6 open question (below-altitude prose was
   already explicit and was still violated once) points at the same root
   cause: policy expressed only as prose in a per-repository declaration is
   weaker than policy expressed structurally in a reusable, named identity.

## Plan

### Phase 1 - Azure DevOps backlog provider

- [x] Implement a `ForgeProvider` adapter for Azure DevOps work items
  (list/reserve/claim/release) alongside the existing GitHub implementation.
  Landed `AzureDevOpsProvider` (via the `az` CLI's `devops`/`boards`
  subcommands): WIQL discovery + `az boards work-item show` for fields,
  `System.Tags` as the label equivalent, and reservation markers carried as
  work-item comments through the generic `az devops invoke` REST bridge --
  reusing the existing `_marker`/`_parse_marker`/`_latest_reservations`
  helpers unmodified across both providers.
- [x] Generalize `validate_config`'s hard-coded `"only 'github' is supported"`
  gate to dispatch on the adapter registry instead of a literal string.
  `_SUPPORTED_FORGE_PROVIDERS = {"github", "azure-devops"}`; a new
  `_forge_provider_for(config)` factory selects the adapter class, replacing
  `run_tick`'s hard-coded `GitHubProvider(...)` default.
- [ ] Prove one live Azure DevOps-backed declaration end-to-end (discovery,
  batching, reservation, settlement) alongside the existing GitHub declaration
  it must not regress. Still open -- no live Azure DevOps organization/project
  has exercised this adapter yet; today it is validated only by mocked-`az`
  unit tests (17 new tests: identity mismatch, tag-as-label round-trip,
  reservation-marker parity with GitHub, provider selection).

### Phase 2 - Declarative worker identity

- [x] Define the shape of a reusable, named worker identity (a sub-agent
  definition, in the mold of `proxy-code-review:proxy-reviewer`) that a
  declaration selects instead of inlining `worker_guidance` prose.
  Landed as `agent_dispatch.worker_identities.load_worker_identity`: an
  identity is a `<name>.identity.md` file in the same frontmatter (`name`,
  `description`) plus markdown-body (`rules`) shape as an in-session
  `*.agent.md` sub-agent, resolved repo-local-first then from the plugin's
  packaged `plugins/agent-dispatch/src/agent_dispatch/identities/`. A declaration's new
  `worker_identity` field is mutually exclusive with inline
  `worker_guidance`; `validate_config` resolves it at validation time.
- [x] Extract at least one existing declaration's inline prose (the
  `odsp-web-harness-backlog` loop is the live candidate) into such an
  identity, proving the declaration shrinks to policy/eligibility only.
  Extracted verbatim into the packaged built-in identity, now shipped from
  `plugins/agent-dispatch/src/agent_dispatch/identities/odsp-web-harness-backlog.identity.md`
  (see 2026-09-07 journal entry below for why it moved there).
  **Not yet applied to the live `dotfiles` declaration** -- that requires the
  running agent-dispatch daemon to have this PR's code deployed first (an
  older daemon would reject `worker_identity` as an unknown key and break the
  live loop); see Gotchas.
- [ ] Assess whether a named identity's structural boundaries (permitted
  tools/mutations) can enforce the reviewer vision's never-supersede rule
  more robustly than prose alone -- closing the Phase-6 open question in
  `review-automation-reliability`.

### Phase 3 - Concise event-then-charter-pull prompts

- [x] Classify the event shapes a recipe already knows about embodiment time.
  Two shapes already had dedicated concise handling before this effort
  (`bridge.resume_steered_owner` for a steer answer, `supervisor._default_nudge`
  for a stalled-but-live nudge); the remaining gap was the **new-work /
  redrive spawn** path (`embody.autopilot_worker_prompt`), which always
  inlined the full behavioral essay regardless of whether the embodying
  worker would need it again.
- [x] Add the "full charter" command/route a seed points at. Landed
  `agent_dispatch.worker_charter` (one authoritative `autopilot` charter
  text: contract-net evaluation, the goal/progress loop, decline/duplicate/
  complete conventions) plus a new `agent-dispatch charter show <name>` CLI
  command, and a new `concise: bool = False` parameter on
  `embody.autopilot_worker_prompt` -- opt-in, every existing call site
  (`supervisor.py`'s spawn + redrive, `embody.spawn_embodied_worker`)
  unaffected by default. When `concise=True` the seed keeps only the
  task-specific mechanics (show/claim/start/decline/complete commands) and
  points the worker at `agent-dispatch charter show autopilot` to pull the
  policy prose only if it does not already have it this session.
- [x] Measure the token-cost delta. The concise seed is well under half the
  length of the always-inlined one (asserted in
  `test_autopilot_prompt_concise_pulls_charter_instead_of_inlining_it`);
  behavioral fidelity is unchanged since the charter is the same prose,
  fetched on demand instead of always pasted in -- claim/evaluate/complete
  mechanics and decline conventions are identical either way.
  Wired to its first live call site: `supervisor.make_redrive_sender` now
  builds its re-drive seed with `concise=True` -- a re-drive always targets an
  already-embodied worker that already had a chance to read the charter, so
  re-inlining the full essay a second time is pure waste. Every other call
  site (initial spawn, both CLI and headless) is unaffected. New test
  `test_make_redrive_sender_builds_concise_seed` asserts the redrive sender
  builds its seed with `concise=True`.

### Phase 4 - Preloaded dispatch supplement on the worker identity

- [x] Give the shared "how to behave as a dispatch worker" supplement one
  independently-revisable home (`agent_dispatch.worker_charter`) that a seed
  references by a stable name (`autopilot`) instead of re-deriving or
  re-inlining its prose per task. This is the same landing as Phase 3 above
  (they are two views of the same seed redesign, per this effort's own
  Proposal) -- the module is not yet attached *to a named worker identity*
  from Phase 2 specifically (an identity's `rules` are still separate prose);
  that attachment (e.g. an identity frontmatter field naming which charter it
  expects) is still open.
- [ ] Confirm a worker embodied under a named identity never spends a tool
  call or prompt tokens re-deriving this supplement from scratch -- open
  until a live call site actually uses `concise=True` end-to-end.

### Phase 5 - Turnkey colleague adoption

- [ ] Write the adoption path for a colleague unfamiliar with the runtime:
  declaration schema reference, the library of available worker identities,
  and a worked example end-to-end (a new repository, its declaration, its
  selected identity).
- [ ] Identify and remove any remaining step in that path that requires
  reading engine source rather than the declaration schema and an identity's
  own documentation.

## Validation Plan

- [ ] A new Azure DevOps-backed declaration reaches the same discovery →
  batch → settle outcomes as the existing GitHub-backed one, provider
  differences fully behind the adapter.
- [ ] A declaration authored against a named worker identity contains no
  inlined behavioral policy prose, only eligibility/cadence/identity
  selection.
- [ ] Per-embodiment seed size and token cost drop materially for a
  known-event embodiment versus today's always-inlined prompt, with no
  behavioral regression in claim/evaluate/complete/decline flows.
- [ ] A colleague can stand up a new loop from the declaration schema and an
  existing worker identity alone, without reading engine source.

## Proposal

Sequence Phase 1 (ADO) and Phase 2 (worker identity) independently since
neither blocks the other; land Phase 3/4 (prompt shape) together since the
charter-pull command and the preloaded supplement are two halves of the same
seed redesign; do Phase 5 last so the adoption doc reflects the shape the
other phases actually land in.

## Journal

### 2026-09-05 - Kickoff

- Captured four forward-looking gaps discussed live while reconciling the
  #2056 backlog-loop fix against the newly-extracted repository-issue-loop
  vision: ADO provider support, declarative (named, sub-agent) worker
  identity, concise event-first prompts with on-demand charter pull, and a
  preloaded shared dispatch-behavior supplement on the identity. No
  implementation started; this effort tracks the plan only.

### 2026-09-06 - Reconciled with aperture-labs; started Phase 2

- Reconciled the "aperture-labs" reviewer-loop concern from the prior
  handoff: the copilot-extensions PR history for the reviewer module
  (`#1445`..`#2134`) is entirely merged under the one operator account, and
  already matches the vision docs this effort builds on. No separate
  unmerged branch or competing identity concept was found; the existing
  `*.agent.md` sub-agent shape (e.g.
  `copilot-extensions-reviewer.agent.md`) is the precedent Phase 2's worker
  identity borrows from.
- Landed Phase 2's identity shape and one extraction: new
  `agent_dispatch.worker_identities` module (`WorkerIdentity`,
  `load_worker_identity`), a new `worker_identity` declaration field on
  `repository-issue-loop` (validated, mutually exclusive with
  `worker_guidance`), and the `odsp-web-harness-backlog` identity extracted
  verbatim from the live dotfiles declaration into
  `plugins/agent-dispatch/identities/odsp-web-harness-backlog.identity.md`.
  10 new tests (`test_worker_identities.py` + 4 cases in
  `test_repository_issue_loops.py`); full existing suite (55 tests) passes
  unchanged.
- Did **not** switch the live `dotfiles` declaration to
  `worker_identity: odsp-web-harness-backlog` yet -- the running
  agent-dispatch daemon must have this PR's code deployed first, or it will
  reject the new field as an unknown key and break the live backlog loop.
  That switch is the very next slice once this PR lands and deploys.
- Phase 2's third bullet (structural enforcement of never-supersede) is
  still open -- the identity file today only carries prose rules, no
  enforced tool/mutation boundary; deferred to a follow-up slice.

### 2026-09-06 (cont.) - Landed Phase 1 (Azure DevOps provider)

- Also fixed a pre-existing, unrelated CI-blocking baseline bug found while
  landing Phase 2: `#2167` bumped `agent-index`'s own version without
  updating its shipped agent-dispatch registrar declaration fixture, failing
  the `agent-dispatch` suite's version-consistency assertion for any PR.
  Landed separately as `#2170` (merged) so it didn't get bundled with
  unrelated Phase 2 content.
- Implemented Phase 1's `AzureDevOpsProvider` (list/reserve/claim/release
  over Azure DevOps work items via the `az` CLI), generalized
  `validate_config`'s forge-provider gate to
  `_SUPPORTED_FORGE_PROVIDERS = {"github", "azure-devops"}`, and added a
  `_forge_provider_for(config)` factory replacing `run_tick`'s hard-coded
  `GitHubProvider(...)`. `repo` keeps the same `owner/name`-shaped format for
  both providers (`organization/project` for Azure DevOps satisfies the same
  regex), so no new top-level declaration field was needed. Reused
  `_marker`/`_parse_marker`/`_latest_reservations` unmodified -- Azure
  DevOps work-item comments carry the identical JSON marker convention as
  GitHub issue comments. 17 new tests (identity mismatch, tag round-trip,
  provider-factory selection, malformed-config message update); full
  existing suite passes unchanged.
- Phase 1's third bullet (a live Azure DevOps org/project proving the full
  discovery -> batch -> reserve -> settle path) is still open -- this
  session had no Azure DevOps organization available to validate against;
  today's coverage is mocked-`az` unit tests only. That live proof is the
  next slice for whoever picks this back up, alongside a first real
  `azure-devops`-backed declaration to adopt it.

### 2026-09-07 - Landed Phase 3/4 (charter-pull seed mechanism)

- Landed `agent_dispatch.worker_charter`: one authoritative `autopilot`
  charter (contract-net evaluation, the goal/progress loop, decline/
  duplicate/complete conventions) plus a new `agent-dispatch charter show
  <name>` CLI command that prints it on demand.
- Added `concise: bool = False` to `embody.autopilot_worker_prompt` --
  every existing call site (`supervisor.py` spawn + redrive,
  `embody.spawn_embodied_worker`, `fleet_autopilot_worker_prompt`)
  unaffected by default; `concise=True` builds a much shorter seed (task
  mechanics only) that points the worker at `agent-dispatch charter show
  autopilot` instead of inlining the essay. New tests
  (`test_worker_charter.py`, plus two in `test_embody.py`) assert the
  concise seed is under half the length of the default, still carries the
  task-specific commands, and omits the discursive policy prose; the full
  existing suite (341 tests outside the pre-existing pydantic-missing MCP
  gap and one unrelated pre-existing `test_fleet.py` SSH failure -- both
  reproduced identically on unmodified `main`) passes unchanged.
- **Not yet wired to any live call site's default** -- no call site passes
  `concise=True` yet, so today's live behavior is completely unchanged; the
  next slice is choosing which embodiment events should pass it (the new
  Successor Work roster below) and, per Phase 4's second bullet, attaching a
  charter name to a Phase 2 worker identity rather than hard-coding
  `"autopilot"` in the caller.
- Phase 5 (turnkey colleague adoption docs) still not started.

### 2026-09-07 (cont.) - Wired `concise=True` into the redrive call site

- Landed the first live call site for `concise=True`:
  `supervisor.make_redrive_sender` now builds its re-drive seed with
  `concise=True`. A re-drive always targets a **live, already-embodied**
  worker that never claimed its task -- it already had a chance to read the
  charter the first time it embodied, so re-inlining the whole behavioral
  essay a second time was pure waste. Every other call site (initial CLI
  spawn, headless spawn, fleet spawn) is unaffected -- still `concise=False`
  by default. New test `test_make_redrive_sender_builds_concise_seed`
  (`test_supervisor.py`) asserts the redrive sender passes `concise=True`
  through to `embody.autopilot_worker_prompt`; full targeted suite
  (`test_worker_charter.py`, `test_embody.py`, `test_supervisor.py` -- 231
  tests) passes unchanged otherwise. Bumped agent-dispatch to `0.1.2-dev35`
  across all three version surfaces (`pyproject.toml`, `plugin.json`,
  `.github/plugin/marketplace.json`).
- Attaching a charter name to a Phase 2 worker identity (Phase 4's second
  bullet) is still open -- today's identity `rules` only ever feed
  `worker_guidance` in a repository-issue-loop's *task* prompt
  (`_task_prompt`), which is a different prompt from the *embodiment* seed
  (`autopilot_worker_prompt`) that the charter lives on. Wiring a charter
  name onto an identity would need a new path connecting a dispatched task
  back to the identity that created it (so the supervisor's spawn/redrive
  knows which charter to reference) -- deferred as a larger slice, not
  bundled with this one.
- Confirmed again this leg (unchanged from prior handoffs): the live
  `dotfiles` declaration is still NOT switched to
  `worker_identity: odsp-web-harness-backlog`. Unlike prior legs, this time
  the check found the running daemon HAS auto-updated: `agent-dispatch
  --version` -> `0.1.2-dev34` (was `0.1.2-dev29`), and its own venv
  (`%USERPROFILE%\.agent-dispatch\versions\0.1.2-dev34\Scripts\python.exe`)
  successfully imports `agent_dispatch.worker_identities`. The daemon-version
  gate for the dotfiles switch is now clear -- this is the next slice, not a
  re-check. A paired `odsp-web-harness` + `dotfiles` knowledge worktree
  (`tmichon-cloud1-win-20260907-033821-8451` /
  `tmichon-cloud1-win-20260907-033821-8451-k`) already exists, empty and
  unused, ready for whoever picks up the switch (edit
  `dotfiles/.agent-dispatch/registrar/odsp-web-harness-issue-loop.json` from
  its `-k` worktree, never the dotfiles anchor).

### 2026-09-07 (cont.) - Found and fixed a packaging bug blocking the dotfiles switch

- Edited the `-k` dotfiles worktree's live declaration to
  `worker_identity: odsp-web-harness-backlog` and validated it against the
  running daemon's own installed code (the local per-user
  `.agent-dispatch\versions\0.1.2-dev34\...\repository_issue_loops.validate_config`
  runtime slot) before actually deploying the change. It failed:
  `RegistrarError: worker_identity 'odsp-web-harness-backlog': no identity
  file found`. Root cause: `worker_identities._BUILTIN_DIR` was computed as
  `Path(__file__).resolve().parents[2] / "identities"`, which only resolves
  correctly in the **editable/dev src layout**
  (`plugins/agent-dispatch/src/agent_dispatch/worker_identities.py` ->
  `plugins/agent-dispatch/identities/`). The `identities/` directory lived
  as a **sibling of `src/`**, outside the `agent_dispatch` package, so
  standard `setuptools` package discovery never included it in the built
  wheel at all -- an installed venv (like the live daemon's) has no
  `identities/` directory anywhere under its site-packages, regardless of
  version. This bug was latent since Phase 2 landed (2026-09-06); the
  daemon-version gate masked it because no leg had gotten a clean daemon
  version *and* actually attempted the switch until now.
- Fixed by moving the identity file **inside** the installable package
  (`plugins/agent-dispatch/src/agent_dispatch/identities/`), changing
  `_BUILTIN_DIR` to `Path(__file__).resolve().parent / "identities"`, and
  adding `[tool.setuptools.package-data] agent_dispatch =
  ["identities/*.identity.md"]` to `pyproject.toml` so the file actually
  ships in the wheel. Verified by building a wheel
  (`pip wheel . --no-deps`) and inspecting its contents: the `.identity.md`
  file is now present at `agent_dispatch/identities/...` inside the zip
  (it was absent before this fix, confirmed by inspecting an unfixed
  build). Targeted suite (`test_worker_identities.py`,
  `test_repository_issue_loops.py`, `test_worker_charter.py`,
  `test_embody.py`, `test_build_info.py`, `test_supervisor.py`,
  `test_agent_index_managed.py` -- 311 tests) passes unchanged. Bumped
  agent-dispatch to `0.1.2-dev37` across all three version surfaces
  (`pyproject.toml`, `plugin.json`, `.github/plugin/marketplace.json`).
- **The dotfiles switch is still blocked** -- now on a *new* daemon-version
  gate: this fix must deploy to the live daemon (a version at or above the
  one carrying this PR) before the `-k` worktree's edited declaration file
  can be copied over to `dotfiles/.agent-dispatch/registrar/...` live,
  because even a `worker_identity`-aware daemon without this packaging fix
  will still fail to resolve the identity file. Re-verify the daemon version
  next leg the same way as this leg did (`agent-dispatch --version` plus
  confirming `agent_dispatch.worker_identities.load_worker_identity(
  "odsp-web-harness-backlog")` actually resolves, not just imports) before
  retrying the switch. Left the edited-but-not-yet-live declaration change
  in the `-k` worktree (`dotfiles.worktrees\...-8451-k`) uncommitted for
  whoever verifies the new gate is clear; do not copy it into the live
  `dotfiles` anchor until then.

