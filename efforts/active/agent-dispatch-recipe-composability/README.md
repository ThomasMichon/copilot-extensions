# agent-dispatch recipe composability (extend any declaration + script-path hooks)

- **Slug:** `agent-dispatch-recipe-composability`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`)
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-10-02
- **Status:** Draft
- **Vision:** `visions/plugins/agent-dispatch/README.md` §*extend-any-declaration*
  (added by this effort's own Provenance entry) — generalizes *loop-recipes*
  and *The recipe* from "extend one of four named, plugin-shipped archetypes
  with scalar overrides" to "extend any already-resolved declaration, with
  override values that may themselves be script-path hooks the base engine
  invokes."
- **Umbrella issue:** [#4959](https://github.com/ThomasMichon/copilot-extensions/issues/4959)
- **Related:** [`agent-dispatch-recipe-library`](../agent-dispatch-recipe-library/README.md)
  (#4691) — this effort builds on, and does not replace, that effort's
  `extends:` resolution mechanism (`registrar_recipes.py`: `resolve_extends`
  / `resolve_recipe_ref` / `deep_merge` / `substitute_placeholders`). Land
  this effort's Phase 1 only after confirming it doesn't collide with any
  concurrent recipe-library work in the same module.

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
- **Builds on `agent-dispatch-recipe-library`** (#4691, status: in progress):
  that effort shipped `extends:`'s resolution mechanism and four named
  global recipes. This effort generalizes the *resolution* (any base, not
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
- [ ] Generalize `resolve_extends` so that when a resolved template itself
      carries an `extends:` key, that is resolved recursively (depth > 1)
      before merging — today's single-hop-only resolution is the literal
      gap between "extend a named recipe" and "extend any already-resolved
      declaration."
- [ ] **Each hop resolves its own nested `extends:` ref against its own
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
- [ ] Add a chain-depth/cycle guard distinct from
      `substitute_placeholders`'s existing cyclic-*value* guard (tracks
      resolved *ref identities* — the fully-resolved absolute path, or
      `global:<name>` — across the chain, not object ids within one
      template) — a ref chain that revisits the same resolved identity
      raises a clear `RegistrarError` naming the full chain, never a
      `RecursionError`.
- [ ] Confirm (already true today, verify with a test) that a repo-local or
      cross-repo `extends:` ref may point at **any** valid declaration
      document, not only a document authored as a "recipe template" —
      extending a real, already-used direct declaration as a base is not a
      new acceptance rule, just a consequence of chaining working
      correctly.
- [ ] Tests: a 2-hop chain (declaration → repo-local recipe → `global:`
      recipe); a 3-hop chain; **a chain whose hops span two different
      repository roots**, proving each hop's own nested ref resolves
      against *its own* directory rather than the original caller's; a
      cyclic chain (A → B → A) raises clearly with the offending ref chain
      named; placeholder substitution and override deep-merge both still
      apply correctly at every hop, in order (closest override wins,
      matching today's single-hop contract).

### Phase 2 — `script` backlog provider (first script-path-hook realization)
- [ ] Add a `script` forge provider for `repository_issue_loop`
      declarations, alongside `github`/`azure-devops`/`gitea`: implements
      `ForgeProvider`'s four methods by invoking a declared script via
      subprocess for each operation.
- [ ] Define and document the script's command contract: one invocation per
      operation (`list_open_issues` / `reserve` / `claim` / `release`,
      named via a `--op` flag or equivalent), a structured JSON request on
      stdin, a structured JSON response on stdout, non-zero exit treated as
      a real error with the script's stderr surfaced in the raised
      exception (never silently swallowed).
- [ ] **Bound execution location and duration**, following the existing
      `ScriptEvaluator` precedent (`producers/evaluator.py:292-315`:
      `subprocess.run(..., timeout=..., capture_output=True, text=True,
      shell=False, **no_window_kwargs())`, a distinct caught exception for
      `TimeoutExpired` vs. `OSError`): resolve the script path, and the
      subprocess's `cwd`, relative to the **declaration file that supplies
      the `script` override** — never the daemon's own incidental working
      directory, since these calls run synchronously inside each scheduled
      occurrence. Add a configurable `timeout_seconds` (same default-30s
      shape as `ScriptEvaluator`) with a clear timeout error distinct from
      a non-zero exit or malformed output.
- [ ] `repository_issue_loop`'s existing scheduling/lease/quiet-period/
      dedup machinery is reused completely unchanged — the script supplies
      only the four backlog operations, never the loop shape, matching the
      vision's *extend-any-declaration*: "the base keeps ownership of the
      loop... the script owns only the domain-specific decision."
- [ ] Tests: a fixture script implementing the four-op contract drives a
      full `repository_issue_loop` cycle identically to the existing
      `github`/`azure-devops` adapters' own test coverage (same shared test
      matrix where it applies); a malformed script response (invalid JSON,
      wrong shape) raises a clear `RegistrarError`; a non-zero exit
      surfaces the script's stderr verbatim in the error; a hung script
      raises the distinct timeout error rather than stalling the loop; the
      resolved CWD is proven relative to the declaration file by running
      the test suite itself from an unrelated, neutral daemon CWD.
- [ ] Validate against the motivating consumer: a real script polling that
      consumer's own REST API for pending work items, reserving/claiming/
      releasing against it, proves the shape generalizes beyond a forge —
      coordinate with that consumer's own effort for this validation round
      rather than guessing at its API shape here.

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

- [ ] Full `agent-dispatch` plugin suite green after each phase; every
      existing direct `kind:` declaration and every existing `extends:`
      declaration (repo-local, cross-repo, `global:`) resolves identically
      to its pre-effort behavior — zero regression in
      `agent-dispatch-recipe-library`'s own shipped surface.
- [ ] A 2-hop and a 3-hop `extends:` chain resolve to the identical
      `ProfileDeclaration` a hand-written equivalent direct declaration
      would produce (byte-for-byte dict equality before `load_declaration`
      runs — the same proof style Phase 3 of `agent-dispatch-recipe-library`
      used for its own single-hop case).
- [ ] A `script`-provider `repository_issue_loop` declaration round-trips a
      full list → reserve → claim → release cycle against a fixture
      script, producing task/dedup behavior identical in shape to the
      `github`/`azure-devops` adapters' own tested cycle.
- [ ] The motivating consumer's own emitter need is provably expressible as
      a `script`-provider `repository_issue_loop` extension with zero
      bespoke command-emitter code — confirmed with that consumer's own
      effort, not assumed here.

## Proposal

_Pending — Phase 1's concrete chain-resolution + cycle-guard design will be
detailed here once implementation starts, if it grows beyond what the Plan
items above already specify._

## Journal

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
