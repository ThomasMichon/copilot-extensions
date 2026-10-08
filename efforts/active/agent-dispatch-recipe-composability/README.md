# agent-dispatch recipe composability (extend any declaration + script-path hooks)

- **Slug:** `agent-dispatch-recipe-composability`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`)
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-10-02
- **Status:** Active <!-- Phase 1 done; Phase 2 landed (PR #4993 merged); Phase 3 (docs) next -->
- **Vision:** `visions/plugins/agent-dispatch/README.md` §*extend-any-declaration*
  (added by this effort's own Provenance entry) — generalizes *loop-recipes*
  and *The recipe* from "extend one of four named, plugin-shipped archetypes
  with scalar overrides" to "extend any already-resolved declaration, with
  override values that may themselves be script-path hooks the base engine
  invokes."
- **Umbrella issue:** [#4959](https://github.com/ThomasMichon/copilot-extensions/issues/4959)
- **Related:** [`agent-dispatch-recipe-library`](../../2026/10/08%20agent-dispatch-recipe-library/README.md)
  (#4691) — this effort builds on, and does not replace, that effort's
  `extends:` resolution mechanism (`registrar_recipes.py`: `resolve_extends`
  / `resolve_recipe_ref` / `deep_merge` / `substitute_placeholders`). Land
  this effort's Phase 1 only after confirming it doesn't collide with any
  concurrent recipe-library work in the same module. That effort also
  deferred its own Phase 3 single-`emitter`-producer-primitive taxonomy
  refactor (schedule/webhook/websocket as emitter *triggers* rather than
  separate `kind` values) to
  [`ThomasMichon/copilot-extensions#5732`](https://github.com/ThomasMichon/copilot-extensions/issues/5732)
  when it archived — a related but distinct registrar-taxonomy refactor,
  not part of this effort's own scope (this effort generalizes `extends:`
  *resolution* and override values; #5732 generalizes *producer kind*).
  Worth checking for sequencing overlap before either lands.

## Guiding Intent

`extends:` lets a registrar declaration parameterize one of agent-dispatch's
own named, plugin-shipped recipes (`reviewer`, `conflict-resolution`,
`goal-driven`, `repository-issue-loop`). That is real reuse, but it stops at
two boundaries: only the plugin's own enumerated recipes are valid bases
(no chaining, no extending an arbitrary repo-authored declaration), and
override values are scalars only (no way to hand the base engine a script
to run at one of its own extension points). A domain whose work doesn't fit
any of the four archetypes today has exactly one option — a wholly bespoke
`command:`-backed emitter with no shared loop contract and no reuse story,
reimplementing scheduling/lease/suspend-resume logic the plugin already
owns for every other recipe. This effort closes that gap: any
already-resolved declaration becomes a valid extension base, and an
override value may name a script the base's own engine invokes at a
declared extension point — while the existing no-`extends:` path (write
your own full emitter and evaluator) stays available for domains no base
fits.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| copilot-extensions (this repo) | `extends:` chaining generalization, script-backed provider, docs | worktree PRs against `dev` |
| A consuming repo with its own internal work-item source (e.g. a private facility's own health-monitoring effort) | Motivating consumer and validation target for the script-provider slice; adopts once it lands | the consumer's own private effort, linked back here, not tracked in this repo |

## Coordination

- **Topology:** independent per-phase PRs against `dev`, each phase
  independently reviewable and mergeable (Phase 1 is a prerequisite for
  Phase 2's validation step, which exercises a multi-hop `extends:` chain
  ending in a `script`-provider declaration; Phases are otherwise
  sequential, not parallel-landable, given Phase 2 builds directly on
  Phase 1's resolver changes in the same module).
- **Host (owns this repo's PRs):** copilot-extensions worktree sessions.
- **Delegates:** none currently.
- **Handoff:** this effort's Journal records when each phase merges and
  promotes; the motivating consumer's own linked private effort starts its
  validation round (Phase 2's last item) once Phase 2 merges, and reports
  back into this effort's Journal rather than this repo tracking that
  consumer's own work directly.

## Context

- **Motivating consumer:** a private facility repo's own health-monitoring
  effort needs to poll its own internal REST API for pending work items
  (not a forge/issue tracker) and convert results into dispatch tasks,
  reusing `repository-issue-loop`'s scheduling/lease/suspend-resume
  machinery rather than reimplementing it in a bespoke emitter. That
  consumer's own tracking effort references this one; it is not itself
  tracked here (see `references/efforts.md` §Cross-repo placement — this
  is the "Build directly in the target repo" model, confirmed via
  `scripts/emit-policy.sh --check-adoption`).
- **Builds on `agent-dispatch-recipe-library`** (#4691, status: Done, archived
  2026-10-08): that effort shipped `extends:`'s resolution mechanism and four
  named global recipes. This effort generalizes the *resolution* (any base, not
  only a named recipe; chaining) and the *override model* (a script-path
  hook, not only scalar substitution) without changing that effort's own
  shipped behavior — every existing direct `kind:` declaration and every
  existing `extends:` declaration must resolve identically after this
  effort's changes land.
- **`repository_issue_loops.py` already has the right shape for Phase 2:**
  its `ForgeProvider` is a four-method `Protocol`
  (`list_open_issues`/`reserve`/`claim`/`release`) with three existing
  adapters (`github`, `azure-devops`, a `gitea` stub). A `script` provider
  implementing the same Protocol via subprocess (structured JSON in/out) is
  a natural fourth adapter, not a new engine — this is the concrete first
  realization of a "script-path hook" the vision describes abstractly.

## Request

Captured across two rounds (2026-10-02), verbatim:

> It's probably a bit much to expect a perfectly-declarative general
> HTTP-polling emitter. The new system should allow a "packagable
> emitter/evaluator pair" to be stored in the repo, though, and referenced
> by YAML.

> Ah, the vision/architecture needs improvement. It should be possible to
> extend *any* upstream declaration, ideally, and one should be able to
> inject "script paths" as template values, to be run by the upstream
> emitter. If you don't extend anything, you provide the emitter/valiator
> yourself. Would love to see tha work somehow.

Read literally: (1) extension must not be fenced to a small named set — any
already-resolved declaration is a valid base; (2) an override value may be a
script path, run by the **base's own** (the "upstream") emitter — the script
supplies a decision, not the loop; (3) the no-extension path (author your
own full emitter and evaluator) remains available and is not being removed
or discouraged, only no longer the *only* option for a domain that doesn't
fit an existing archetype.

## Plan

### Phase 1 — Recursive `extends:` resolution (any base, not only a named recipe)
- [x] Generalize `resolve_extends` so that when a resolved template itself
      carries an `extends:` key, that is resolved recursively (depth > 1)
      before merging — today's single-hop-only resolution is the literal
      gap between "extend a named recipe" and "extend any already-resolved
      declaration."
- [x] **Each hop resolves its own nested `extends:` ref against its own
      canonical base directory, not the original caller's `base_dir`.**
      `resolve_recipe_ref`/`_load_recipe_document` take a single caller-
      supplied `base_dir` today (`registrar_recipes.py:207-275`) because
      there is only ever one hop; a recursive resolver must instead carry
      forward **the directory the just-resolved template file itself lives
      in** (or, for a `global:` ref, the plugin's own root) as the `base_dir`
      for *that template's own* nested ref — never the original
      declaration's directory. Otherwise a cross-repo base's own
      repo-relative `./...` ref would incorrectly resolve against the
      *consuming* repo instead of the base's own repository.
- [x] Add a chain-depth/cycle guard distinct from
      `substitute_placeholders`'s existing cyclic-*value* guard (tracks
      resolved *ref identities* — the fully-resolved absolute path, or
      `global:<name>` — across the chain, not object ids within one
      template) — a ref chain that revisits the same resolved identity
      raises a clear `RegistrarError` naming the full chain, never a
      `RecursionError`.
- [x] Confirm (already true today, verify with a test) that a repo-local or
      cross-repo `extends:` ref may point at **any** valid declaration
      document, not only a document authored as a "recipe template" —
      extending a real, already-used direct declaration as a base is not a
      new acceptance rule, just a consequence of chaining working
      correctly.
- [x] Tests: a 2-hop chain (declaration → repo-local recipe → `global:`
      recipe); a 3-hop chain; **a chain whose hops span two different
      repository roots**, proving each hop's own nested ref resolves
      against *its own* directory rather than the original caller's; a
      cyclic chain (A → B → A) raises clearly with the offending ref chain
      named; placeholder substitution and override deep-merge both still
      apply correctly at every hop, in order (closest override wins,
      matching today's single-hop contract).
- [ ] **Known gap, not yet closed** (surfaced in PR #4968 round 5):
      per-hop directory provenance is proven for exactly one
      path-dependent field on one kind -- `kind: emitter`'s own
      `spec.cwd`. Two other kind-specific expansion paths resolve their
      own path-dependent fields against the *outer* leaf file/`repo_root`
      entirely outside `resolve_extends` (discarded once it returns a
      plain dict): `reviewer_loops.expand_reviewer_loop`'s own
      `emitter.cwd` handling, and
      `repository_issue_loops.expand_repository_issue_loop`'s
      `worker_identity` resolution via the caller's `repo_root`.
      Extending a cross-repo `reviewer-loop`/`repository-issue-loop` base
      through a chain may therefore still misattribute one of those
      fields to the wrong repo. Closing this fully likely needs
      `resolve_extends`'s per-hop directory tracking threaded out to
      `read_declaration_file_set` (a public-signature change spanning
      `registrar_recipes.py` and `registrar_discovery.py`), not just
      `registrar_recipes.py` alone -- scoped as its own follow-up slice
      rather than folded into this phase, since no real consumer needs
      cross-repo chaining of those two kinds today (the motivating
      consumer only needs `repository-issue-loop`'s forthcoming `script`
      provider, Phase 2 below, chained same-repo).

### Phase 2 — `script` backlog provider (first script-path-hook realization)
- [x] Add a `script` forge provider for `repository_issue_loop`
      declarations, alongside `github`/`azure-devops`/`gitea`: implements
      `ForgeProvider`'s four methods by invoking a declared script via
      subprocess for each operation. **Landed:** new `script_provider.py`
      module (`ScriptProvider`, mirroring `gitea_provider_stub.py`'s own
      split-for-module-size-cap precedent), wired into
      `repository_issue_loops.py`'s `_SUPPORTED_FORGE_PROVIDERS`,
      `_FORGE_KEYS`, `validate_config`, and `_forge_provider_for`.
- [x] Define and document the script's command contract: one invocation per
      operation (`list_open_issues` / `reserve` / `claim` / `release`,
      named via a `--op` flag or equivalent), a structured JSON request on
      stdin, a structured JSON response on stdout, non-zero exit treated as
      a real error with the script's stderr surfaced in the raised
      exception (never silently swallowed). **Landed:** documented in
      `ScriptProvider`'s own docstring; a malformed/non-dict stdout, or a
      non-list/malformed `issues` entry, is equally a real
      `ScriptProviderError`, never a silent empty success.
- [x] **Bound execution location and duration**, following the existing
      `ScriptEvaluator` precedent (`producers/evaluator.py:292-315`:
      `subprocess.run(..., timeout=..., capture_output=True, text=True,
      shell=False, **no_window_kwargs())`, a distinct caught exception for
      `TimeoutExpired` vs. `OSError`): resolve the script path, and the
      subprocess's `cwd`, relative to the **declaration file that supplies
      the `script` override** — never the daemon's own incidental working
      directory, since these calls run synchronously inside each scheduled
      occurrence. Add a configurable `timeout_seconds` (same default-30s
      shape as `ScriptEvaluator`) with a clear timeout error distinct from
      a non-zero exit or malformed output. **Landed:** new
      `validate_script_forge_config` resolves `forge.command`'s script path
      and `forge.cwd` (both if relative) against `validate_config`'s own
      `cwd` (repo_root) parameter — the same threading `worker_identity`
      and the emitter's own `spec.cwd` already use, re-resolved fresh each
      tick via `run_tick`'s own `cwd=spec.get("cwd")`, never the daemon's
      incidental cwd.
- [x] `repository_issue_loop`'s existing scheduling/lease/quiet-period/
      dedup machinery is reused completely unchanged — the script supplies
      only the four backlog operations, never the loop shape, matching the
      vision's *extend-any-declaration*: "the base keeps ownership of the
      loop... the script owns only the domain-specific decision." **Landed:**
      `_forge_provider_for` only changes which `ForgeProvider` is
      constructed; `plan`/`run_tick`'s own scheduling/lease/dedup
      *algorithm* is unchanged (a later round did thread every provider
      call through `_backlog_identifier(config)` so `forge.backlog`
      reaches the script instead of the task-routing `repo` -- a call-site
      change, not a change to the loop's own shape/control flow).
- [x] Tests: a malformed script response (invalid JSON, non-object, a
      malformed issue entry) raises a clear `ScriptProviderError`; a
      non-zero exit surfaces the script's stderr verbatim; a timeout
      raises a distinct error; a start failure (`OSError`/
      `FileNotFoundError`) raises a distinct error; `validate_config`
      resolves a relative `forge.command`/`forge.cwd` against the
      declaring repo root and leaves an absolute one untouched; `reserve`/
      `claim`/`release` post the documented JSON payload shape (including
      `producer_login`). **Landed:** per-operation unit coverage via a
      mocked subprocess runner (matching `ScriptEvaluator`'s own test
      style) for the contract shape itself, *plus* a real, on-disk
      fixture script exercised through real `Popen` spawns
      (`test_script_provider_round_trips_list_reserve_claim_release_through_a_real_script`)
      proving the full `list` → `reserve` → `claim` → `release` state
      transition persists to disk across four separate real subprocess
      invocations -- not just the per-op JSON shape in isolation.
- [x] Deferred to the motivating consumer's own effort: validate against
      the motivating consumer: a real script polling that consumer's own
      REST API for pending work items, reserving/claiming/releasing
      against it, proves the shape generalizes beyond a forge —
      coordinate with that consumer's own effort for this validation
      round rather than guessing at its API shape here.
- [x] Deferred to `#5174`: **known, tracked limitation** — `forge.command`/
      `forge.cwd` are resolved against the **leaf declaration's own repo
      root** (the outermost file's `cwd`, threaded via `run_tick`'s
      `cwd=spec.get("cwd")`), never against the directory of whichever
      hop in an `extends:` chain actually *supplied* the override —
      unlike `kind: emitter`'s own `spec.cwd`, which Phase 1's
      `_resolve_extends_tracking_cwd_origin` already tracks per-hop for
      exactly this reason (see that function's own "Scope, stated
      plainly" docstring section, which already named
      `repository_issue_loops.expand_repository_issue_loop`'s
      path-dependent fields — `worker_identity` and now `forge.command`/
      `forge.cwd` — as out of its current reach). A bounded mitigation
      ships instead of the silent misresolution: a declaration that
      inherits a *relative* `forge.command`/`forge.cwd` is refused
      outright at registration whenever the *nearest hop that actually
      defines that field* lives outside this repo
      (`registrar_discovery.read_declaration_file_set`/
      `_field_defining_hop_is_cross_repo` walk the chain per field to
      find it, threading the result through
      `validate_script_forge_config`, which raises a clear
      `RegistrarError`) — an absolute inherited path, a field whose
      defining hop is same-repo, or a field declared directly rather than
      inherited, is unaffected. Properly fixing the underlying resolution
      (rather than refusing it) still means generalizing
      `_resolve_extends_tracking_cwd_origin` beyond its
      current `kind: emitter`-only scope to also track
      `repository-issue-loop`'s own path-dependent fields, a change to
      already-merged, heavily-reviewed Phase 1 code (`registrar_recipes.py`)
      — deliberately not attempted inside Phase 2's own PR given its scope
      and risk; tracked in `#5174` as explicit follow-up work instead of a
      silent gap.

### Phase 3 — Docs
- [ ] `plugins/agent-dispatch/README.md`: document the generalized
      `extends:` chaining (any base, multi-hop) and the `script` provider,
      with a worked migration example (a hand-written custom-backlog
      `command:` emitter → its `script`-provider `repository_issue_loop`
      equivalent).
- [ ] Update `visions/plugins/agent-dispatch/README.md`'s
      *extend-any-declaration* Provenance entry to mark implementation
      landed, citing the merged PRs.

### Phase 4 — Further script-hook points _(agent-recommended, lower priority)_
- [ ] _(agent-recommended)_ If a concrete future consumer needs a
      script-path hook in a different engine (e.g. `reviewer-loop`'s
      verdict-application step), extend the same subprocess-JSON pattern
      there. Not built speculatively now — track against a real motivating
      need only, the same discipline Phase 2 followed for
      `repository_issue_loop`.

## Validation Plan

- [x] Full `agent-dispatch` plugin suite green after each phase; every
      existing direct `kind:` declaration and every existing `extends:`
      declaration (repo-local, cross-repo, `global:`) resolves identically
      to its pre-effort behavior — zero regression in
      `agent-dispatch-recipe-library`'s own shipped surface. (Re-confirmed
      after Phase 2: 836+480+494+622+600+773, all 6 sub-suites.)
- [x] A 2-hop and a 3-hop `extends:` chain resolve to the identical
      `ProfileDeclaration` a hand-written equivalent direct declaration
      would produce (byte-for-byte dict equality before `load_declaration`
      runs — the same proof style Phase 3 of `agent-dispatch-recipe-library`
      used for its own single-hop case).
- [x] A `script`-provider `repository_issue_loop` declaration's `reserve` →
      `claim` → `release` calls each post the documented JSON payload
      shape (repo/issue/reservation/task_id/reason, plus `producer_login`)
      -- proven both via a mocked subprocess runner for per-op unit
      coverage (the same style the `github`/`azure-devops` adapters' own
      tests use) *and* via a real, on-disk fixture script spawned through
      real `Popen` calls, round-tripping `list_open_issues` → `reserve` →
      `claim` → `release` and persisting each reservation marker to disk
      across the four separate invocations.
- [ ] The motivating consumer's own emitter need is provably expressible as
      a `script`-provider `repository_issue_loop` extension with zero
      bespoke command-emitter code — confirmed with that consumer's own
      effort, not assumed here.

## Proposal

_Pending — Phase 1's concrete chain-resolution + cycle-guard design will be
detailed here once implementation starts, if it grows beyond what the Plan
items above already specify._

## Journal

### 2026-10-04 — PR #4993 merged: Phase 2 (`script` forge provider) complete
- 25 automated review rounds processed across the push-fix loop
  (case-sensitive script backlog keys; `forge.backlog` decoupling
  script-reported backlog identity from the task's own routing `repo`;
  cross-repo `extends:` inherited-script-path rejection, tracked to the
  nearest field-defining hop; indeterminate-probe/PID-reuse/Windows-
  separator/timeout-budget fixes in `procutil.py`/`script_provider.py`;
  an eager `single_instance` import in `conftest.py` fixing an unrelated,
  pre-existing subsuite-grouping-dependent CI flake). Full
  `agent-dispatch` suite green (~4000 tests, all 7 sub-suites) after
  every fix. Merged via the Maintainer self-merge bypass once round 25
  returned zero new findings and every remaining item was independently
  re-verified as already resolved in-code.
- Remaining explicitly-deferred Phase 2 items (unchanged by this round):
  validating against the motivating consumer's own emitter (left for
  that consumer's own effort) and the `extends:`-chain per-hop
  origin-tracking gap for `forge.command`/`forge.cwd` (bounded by the
  cross-repo rejection above, not yet generalized into
  `registrar_recipes.py`).
- Next: Phase 3 (docs) — the worked `extends:` + `script`-provider
  migration example in `plugins/agent-dispatch/README.md`.

### 2026-10-02 — Phase 2 landed: `script` forge provider
- New `script_provider.py` module: `ScriptProvider` (implements
  `repository_issue_loops.ForgeProvider`'s four methods via subprocess,
  one invocation per op with `--op <name>`, JSON request on stdin, JSON
  response on stdout) and `validate_script_forge_config` (the
  `forge.command`/`forge.cwd`/`forge.timeout_seconds` fields, gated to
  `forge.provider: script` the same way `validate_discovery_scope` gates
  `discovery_scope` to `azure-devops`). Split into its own file purely to
  stay under the module-size cap, mirroring `gitea_provider_stub.py`'s
  own precedent.
- Wired into `repository_issue_loops.py`: `"script"` added to
  `_SUPPORTED_FORGE_PROVIDERS`; `_FORGE_KEYS` extended;
  `validate_config` relaxes the `repo` field's `owner/name` shape
  requirement for `script` (the script interprets `repo` itself -- the
  motivating consumer's own case has no forge-shaped identifier at all) and resolves
  a relative `forge.command`/`forge.cwd` against its own `cwd` (repo_root)
  parameter -- the exact same threading `worker_identity` and the
  emitter's own `spec.cwd` already use, re-resolved fresh each tick via
  `run_tick`'s `cwd=spec.get("cwd")`; `_forge_provider_for` gained the
  `script` branch.
- `repository_issue_loop`'s own scheduling/lease/quiet-period/dedup
  *algorithm* (`plan`/`run_tick`) is unchanged -- only which `ForgeProvider`
  gets constructed changed, proving the vision's *extend-any-declaration*
  claim ("the base keeps ownership of the loop... the script owns only
  the domain-specific decision") concretely for the first time. (A later
  round did thread every `provider.*` call site through
  `_backlog_identifier(config)` so `forge.backlog` reaches the script
  instead of the task-routing `repo` -- see below.)
- 21 new tests (`test_repository_issue_loops.py`): `validate_config`
  requiring/rejecting the script-only fields by provider, resolving a
  relative command/cwd against repo_root and leaving an absolute one
  untouched, default/overridden timeout, `_forge_provider_for` selecting
  `ScriptProvider` with the resolved fields; `ScriptProvider` itself --
  `list_open_issues` parsing a well-formed response, `reserve`/`claim`/
  `release` posting the documented payload (including `producer_login`),
  a non-zero exit/timeout/start-failure/malformed-JSON/non-object-
  response/malformed-issue-entry each surfacing as a distinct
  `ScriptProviderError`. Fixed two existing tests' hardcoded
  `_SUPPORTED_FORGE_PROVIDERS` sorted-list error-message assertions
  (`'script'` now appears in the sorted list). Full suite green
  (836+480+494+622+600+773, all 6 sub-suites).
- Left Phase 2's own "validate against the motivating consumer" item
  unchecked by design -- that needs the consumer's own script and its
  own effort's cooperation, not assumed here.
- Next: Phase 3 (docs -- the `extends:` chaining + `script` provider
  worked migration example in `plugins/agent-dispatch/README.md`).

### 2026-10-02 — PR #4993 review round fixed (6 findings)
- 2 Medium: (1) the CLI's own `status`/`discover` commands built their
  forge provider directly from the *raw* `spec.repository_issue_loop`
  declaration -- never routed through `run_tick`'s own
  `validate_config(config, cwd=cwd)` -- so a relative `forge.command`/
  `forge.cwd` resolved against this process's own incidental cwd instead
  of the declaring repo root; worse, `discover` handed that
  prematurely-built provider into `run_tick` as `provider=`, which
  short-circuits `run_tick`'s own normalization (`provider or
  _forge_provider_for(config)`) entirely. Fixed by having
  `_forge_provider_for` itself re-normalize only `forge`'s
  `script`-specific `command`/`cwd` fields via
  `validate_script_forge_config` (idempotent on an already-resolved
  absolute path) -- re-running the *full* `validate_config` was tried
  first and rejected: its own output carries derived keys (e.g.
  `worker_filters`) that are not valid re-input, so it is not safe to
  call twice. Both `loop_commands.py` call sites now thread
  `cwd=source["spec"].get("cwd")`. Added CLI-level regression tests
  (`test_repository_issue_loop_cli.py`) using a real executable marker
  script (subprocess-level, not a monkeypatched runner -- `ScriptProvider`'s
  default `popen=subprocess.Popen` is bound at class-definition time, so a
  later `subprocess.Popen` patch can't reach it) to prove the resolved
  path end to end for both `discover` and `status`. (2) `timeout_seconds`
  accepted `inf`/`nan` (only `<= 0` was checked) in both
  `validate_script_forge_config` and `ScriptProvider.__init__` itself
  (defense-in-depth for direct construction); fixed with
  `math.isfinite()`, matching the repo's existing timeout-validator
  pattern (`companion.py`, `coordinator_loops.py`, etc.).
- While fixing the above, found and fixed a related latent bug the review
  didn't name directly: an *unset* `forge.cwd` resolved to `None` (inherit
  the daemon's own incidental cwd) rather than defaulting to the
  declaring repo root, undermining the same repo-root-anchoring intent
  `command`'s own resolution already enforces. Now defaults to the repo
  root when unset.
- 4 Low: removed a private downstream-consumer identifier ("HAB") that
  leaked into this Journal, a `script_provider.py` docstring, and a test
  docstring -- replaced with identifier-neutral phrasing ("the motivating
  consumer('s own case)"). Documented the `script` provider end to end in
  `docs/repository-issue-loop.md` (new "The `script` provider" section:
  config shape, repo-root-relative resolution rules, and the full
  subprocess JSON request/response contract per op) and updated
  `docs/repository-issue-loop-adoption.md`'s schema reference table
  (`forge.provider` now lists `script`; added `command`/`cwd`/
  `timeout_seconds` rows; `repo`'s own notes mention the `script`
  exception).
- Full suite green after the fixes (all 6 sub-suites).

### 2026-10-02 — Plan reviewed (PR #4962, 3 rounds) + Phase 1 landed
- PR #4962 (the plan itself) went through 3 Copilot review rounds: round 1
  flagged per-hop base-directory semantics, a private-identifier leak in
  the new Provenance entry, a missing Documentation impact statement, and
  a missing active-effort index row; round 2 flagged unbounded script
  execution (CWD/timeout) in the Phase 2 plan and a missing required
  Coordination section. All fixed and verified by direct content
  inspection; round 3 repeated the same 4 items verbatim against the
  already-fixed commit (a known stale-thread-tracking artifact, not new
  substance) and was treated as a clean pass per the commented-review-
  verdict policy. Merged via maintainer bypass.
- **Phase 1 landed** (`registrar_recipes.py`, 2 implementation review
  rounds): `resolve_extends` now recurses when a resolved template itself
  carries an `extends:` key, threading a per-hop base directory so each
  hop's own nested ref (or relative `spec.cwd`) resolves against *its own*
  directory rather than the original caller's -- the exact bug a single
  shared `base_dir` across every hop would have reproduced. A `_chain` of
  resolved ref identities (a canonicalized absolute path, or
  `global:<name>`) detects a cyclic chain and raises `RegistrarError`
  naming the full chain; a `_MAX_CHAIN_DEPTH` guard catches an acyclic but
  unreasonably long chain the same way. Both `RuntimeError` and `OSError`
  from a failed `.resolve()` (a symlink loop surfaces as either, depending
  on Python version) convert to `RegistrarError`/`RegistrarIndeterminateError`
  respectively, never an uncaught bare exception. A `global:` recipe's own
  payload root (`_plugin_payload_root`) is resolved lazily -- only when a
  global template actually needs it -- via `COPILOT_PLUGIN_ROOT` (never
  `AGENT_DISPATCH_INSTALL_DIR`, which names the runtime-state root in the
  general installed-service case, not the validated payload attribution)
  or a `plugin.json`-marker walk, with the installed-wheel-packaging gap
  documented honestly rather than papered over (no shipped recipe needs
  this today). An `emitter` base's own relative `spec.cwd` is absolutized
  against the directory it actually *originated* from -- tracked across
  any number of intermediate hops that inherit but don't resolve the
  placeholder themselves (`_resolve_extends_tracking_cwd_origin`'s
  `pending_cwd_origin`), applied only **after** placeholder substitution
  (never before -- that would corrupt an outer-supplied absolute value,
  or misjudge a still-unresolved deferred placeholder as a literal
  relative path), and reset the instant a hop's own override replaces the
  field outright (deep-merge precedence, left to the existing downstream
  leaf-file rebase exactly as an un-inherited `cwd` always was) -- so
  `read_declaration_file_set`'s single leaf-file-only path rebase doesn't
  silently run a chained base emitter from the wrong repository even
  across multiple non-resolving hops.
  **16 new tests** across 4 review rounds: chaining (2-hop, 3-hop with
  overrides at each hop, chained placeholders, a cross-repo-rooted chain
  with a same-named decoy proving per-hop directory resolution, a
  `global:` recipe's nested ref against the attributed payload root, a
  cyclic chain naming the full chain, an acyclic chain past the max
  depth, an absolute symlinked ref canonicalizing to its real target,
  extending an arbitrary direct declaration); `spec.cwd` provenance
  (absolutizing against its own directory, an outer-filled placeholder
  recognized as absolute, a still-deferred placeholder left untouched, an
  origin preserved across an unresolving intermediate hop, an own-override
  resetting inherited-origin tracking); and resolution-failure
  classification (`RuntimeError` -> `RegistrarError`, `OSError` ->
  `RegistrarIndeterminateError`, both mocked rather than relying on
  flaky/slow real symlink-loop detection).
  Full `agent-dispatch` suite green (836+480+494+622+580+773, all 6
  sub-suites) both before and after.
- Next: Phase 2 (`script` `ForgeProvider` adapter for
  `repository_issue_loop`).

### 2026-10-02 — Effort created; vision updated; issue filed
- Captured the operator's two-round request verbatim (see Request).
  Checked `agent-dispatch-recipe-library`'s actual shipped scope first
  (its `extends:` model parameterizes named built-in recipes only) to
  confirm this is a genuine, distinct gap, not a duplicate of in-progress
  work — that effort merged its Phase 3 sub-PR 2 (`global:` recipes) only
  ~2 hours before this effort was created, so collision risk in
  `registrar_recipes.py` is real; sequenced this effort's Phase 1 to be a
  small, additive, no-behavior-change generalization for exactly that
  reason.
- Added vision §*extend-any-declaration* to
  `visions/plugins/agent-dispatch/README.md` (Features) plus a Provenance
  entry, reconciling as vision-closing within the realized layer (the
  existing *loop-recipes*/*The recipe* text already gestured at
  "extension is expected" with no mechanism — this names one).
- Filed umbrella issue #4959.
- Confirmed `repository_issue_loops.py`'s existing `ForgeProvider` Protocol
  (four methods, three existing adapters) is the right concrete shape for
  Phase 2's script-backed provider — not a new engine, a fourth adapter.
- Not yet started: no code written this round; Phase 1 is next.
