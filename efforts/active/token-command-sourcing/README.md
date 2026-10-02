# Configurable token-command sourcing for every agent-* service

- **Slug:** `token-command-sourcing`
- **Repo:** copilot-extensions (generalizes a pattern already proven in
  `plugins/agent-dispatch`; consumers: `plugins/agent-vault`, `plugins/agent-index`)
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-10-02
- **Status:** Draft
- **Vision:** None — below-altitude operational/config capability. No
  existing vision governs cross-plugin credential-sourcing configuration;
  `visions/installer` covers machine bootstrap, not per-plugin runtime token
  resolution, and neither `agent-vault` nor `agent-index` has its own vision
  doc under `visions/plugins/`. A future related change may warrant
  authoring a leaf vision then; this effort does not invent one to satisfy
  the template.
- **Umbrella issue:** _to file once this effort's plan clears review_
- **Sub-issues:** _TBD, one per Plan phase_
- **Hybrid split:** this is the canonical, generalized effort. A private
  downstream adoption effort tracks facility-specific deployment wiring
  (Vault-sourced commands, docs, deployment env files) once this lands; it is
  not linked here per this repo's public-artifact conventions, and does not
  duplicate this plan.

## Guiding Intent

An adopter should never have to put a bearer token's raw value in a persisted
environment file to run an agent-* service. `agent-dispatch` already proves
the right shape for this: `AGENT_DISPATCH_CONTROL_TOKEN_COMMAND` names a shell
command whose stdout is the token, fetched **on demand** (never persisted) via
`resolve_control_token()` / `_run_token_command()` in
`plugins/agent-dispatch/src/agent_dispatch/config.py`. Every other agent-*
service that reads an operator-managed bearer/API token directly from a plain
env var should get the same `_COMMAND` escape hatch, sourced from one shared,
tested helper instead of each plugin re-implementing (or never implementing)
the pattern.

This is a **generalization effort, not a per-plugin patch**: extract the
existing agent-dispatch logic into a small new shared lib, migrate
agent-dispatch itself onto it (so there is exactly one implementation, not two
parallel ones), and add `_COMMAND` support to the plugins currently missing
it — including proper packaging (dependency + installer wiring) so the shared
lib is actually deployable, not just importable in a dev checkout.

**Scope correction (review round 3):** `agent-mcp`'s `AGENT_MCP_CONTROL_TOKEN`
was initially miscategorized as an operator-managed credential. It is not: a
fresh token is generated per cutover generation
(`secrets.token_hex(16)` in `cutover.py`) and always injected directly into
the new daemon's environment — the daemon itself then persists it to a
PID-keyed sidecar file for internal coordination (`serve.py`). This is an
internally-generated, ephemeral coordination secret exactly like
`agent-index`'s cell tokens, not a Vault-sourceable credential, and a
`_COMMAND` slot would never actually be consulted (cutover always wins).
**`agent-mcp` is dropped from this effort's scope entirely** rather than
redesigning its cutover/sidecar lifecycle, which is unrelated, out of scope,
and risky.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Lambda-Core (host) | Designs the shared lib, migrates agent-dispatch, adds support to agent-vault/agent-index, drives PRs | local `copilot-extensions` worktree |

## Coordination

- **Topology:** independent per-phase PRs — each phase lands and deploys
  cleanly on its own (Phase 1 is a pure addition; Phase 2 is a same-behavior
  refactor; Phases 3-4 are additive per-plugin features), so no shared feature
  branch is needed.
- **Host (owns PRs):** Lambda-Core (solo effort; no delegates yet).
- **Delegates:** none currently.
- **Handoff:** n/a (single participant).

## Context

Surveyed during a private downstream adopter's session (a durable fix for a
self-reintroducing packaging bug in `agent-bridge`; this effort was carved out
as a follow-up request, not part of that fix).

**Operator-managed credential tokens found reading a plain env var with no
`_COMMAND` alternative:**
- `agent-vault`: `AGENT_VAULT_CORE_TOKEN`
  (`plugins/agent-vault/src/agent_vault/core_ext.py:47,106`)
- `agent-dispatch`: `AGENT_DISPATCH_TOKEN`, the plain client bearer — distinct
  from the three pairs below that already have `_COMMAND`. Read directly
  from `os.environ` at **seven call sites total**: `client_token()` itself
  (`config.py:535`), `config.py:231` (inside `load_config()`'s `Config`
  construction), `board_cli.py:342,368,549,739` (four separate board
  requests), and `producers/webhook.py:167` (the webhook producer, including
  coordinator startup).
- `agent-index`: `AGENT_INDEX_ADO_TOKEN`, the Azure DevOps source's PAT
  (`agent_index/sources/azure_devops.py:62-66`).
- `agent-index`: `AGENT_INDEX_GITHUB_TOKEN`, the GitHub source's token
  (`agent_index/sources/github.py:283-287`), ahead of the ambient `GH_TOKEN`/
  `GITHUB_TOKEN` fallbacks in the same resolution chain.

**Already has the `_COMMAND` pattern (the reference implementation to
extract):**
- `agent-dispatch`: `AGENT_DISPATCH_CONTROL_TOKEN[_COMMAND]`,
  `AGENT_DISPATCH_SHARED_TOKEN[_COMMAND]`,
  `AGENT_DISPATCH_SHARED_CONTROL_TOKEN[_COMMAND]` — see
  `resolve_control_token()` / `_run_token_command()` in
  `plugins/agent-dispatch/src/agent_dispatch/config.py`.
- `agent-dispatch` also has a **second, differently-shaped** resolver,
  `producer_capability()` (`config.py:611-623`): it is **command-first**
  (tries `AGENT_DISPATCH_PRODUCER_CAPABILITY_COMMAND` first, falls back to
  the raw `AGENT_DISPATCH_PRODUCER_CAPABILITY` env value only if the command
  is absent or fails), the **opposite precedence** of
  `resolve_control_token()`'s direct-first logic. Both share the same
  low-level `_run_token_command()` primitive. **The shared lib must expose
  both precedence orders as separate, explicitly-named functions** (e.g.
  `resolve_direct_first()` / `resolve_command_first()`, or equivalent) built
  on one shared `run_token_command()` primitive — never assume one universal
  precedence, and never silently change `producer_capability()`'s existing
  behavior.

**Explicitly out of scope** — internally-generated coordination secrets, not
operator-sourced credentials, so a `_COMMAND` slot doesn't apply:
`agent-mcp`'s `AGENT_MCP_CONTROL_TOKEN` (see the scope correction above —
cutover always injects a fresh generated token directly, bypassing any
`_COMMAND` path), `agent-index`'s
`CELL_TRANSACTION_TOKEN`/`CELL_LOCK_TOKEN`/`CELL_START_TOKEN`, `agent-ssh`'s
`AGENT_SSH_KEEPER_TOKEN`, `agent-worktrees`' handoff/assignment tokens.

**Packaging precedent (the pattern every consumer's installer change
follows):** `agent-mcp/scripts/init.sh`'s "Preinstall workspace path deps
(non-uv fallback)" loop (~line 440) enumerates each vendored
`[tool.uv.sources]` workspace path dep as a `'<libs-dir-name>:<pypi-pkg-name>'`
string and explicitly `pip install`s it when `uv` is unavailable (bare `pip`
doesn't honor `[tool.uv.sources]` at all) — `init.ps1` (~line 664) does the
same for Windows. Every plugin this effort actually touches has its own
equivalent preinstall list (confirm the exact location per plugin) and needs
the new shared lib added to it, in addition to a normal `pyproject.toml`
dependency + `[tool.uv.sources]` entry. A shared package that exists only as
source with no installer/dependency wiring is not actually deployable.

## Request

Operator (verbatim, private repo name redacted per this repo's public-artifact
conventions): "For the control token, we need to make sure all agent-*
services which need tokens support a user- or repo-configurable 'command'
slot, for a shell/pwsh/python command that will pipe it a token. For
[our private deployment], we'll prefer sourcing from Vault." Follow-up,
clarifying the underlying goal: "just wanted to avoid putting tokens in ENV.
Prefer on-demand sourcing from contained locations." Scope decisions
confirmed: initial candidate list (agent-dispatch/agent-mcp/agent-vault)
accepted as proposed; shared-lib extraction preferred over per-plugin
duplication; track as a formal effort. `agent-mcp` was later dropped and
`agent-index` added after review (see Context's scope correction and the
Journal) — both are implementation-detail corrections to the same original
ask, not a change in intent.

## Plan

### Phase 1 — Extract the shared helper
- [ ] Create a new small vendored lib (`libs/token-resolve/` — mirrors the
      existing `libs/<name>/` vendoring convention used by `agent-procutil`,
      `zdd`, etc.) exposing:
      - `run_token_command(command: str) -> str | None` — the low-level
        primitive (parses with `shlex.split`, runs without a shell, returns
        stripped stdout), lifted from agent-dispatch's `_run_token_command()`.
      - `resolve_direct_first(direct_var, command_var) -> str | None` —
        direct env wins, else fetch via command. Mirrors
        `resolve_control_token()`'s existing precedence.
      - `resolve_command_first(direct_var, command_var) -> str | None` —
        command is tried first, falls back to the raw direct env value only
        if the command is unset or fails/returns empty. Mirrors
        `producer_capability()`'s existing precedence.
- [ ] **Windows-safe command parsing:** the existing agent-dispatch
      `_run_token_command()` parses with plain `shlex.split()` (POSIX mode),
      which treats backslash as an escape character and mangles an ordinary
      Windows path command like `C:\Tools\vault.exe read secret` into
      `C:Toolsvault.exe read secret`. The shared primitive must parse with
      `shlex.split(command, posix=(os.name != "nt"))` (or an equivalent
      platform-aware argv parser) so Windows-style paths/quoting survive —
      this is a genuine latent bug in the code being extracted, not something
      to carry forward unfixed. Add explicit Windows-path parsing test cases
      (not just POSIX ones) to Phase 1's unit tests.
- [ ] Unit tests for the new lib covering both resolvers (direct value,
      command fetch, neither set, command failure, command producing empty
      output) plus the low-level primitive directly (POSIX and Windows-style
      command strings).

### Phase 2 — Migrate agent-dispatch onto the shared lib
- [ ] Replace `agent-dispatch`'s own `resolve_control_token()` with a thin
      wrapper over the shared lib's `resolve_direct_first()`, for all three
      of its existing `_COMMAND` pairs (`AGENT_DISPATCH_CONTROL_TOKEN`,
      `AGENT_DISPATCH_SHARED_TOKEN`, `AGENT_DISPATCH_SHARED_CONTROL_TOKEN`).
- [ ] Replace `producer_capability()`'s inline logic with a thin wrapper over
      the shared lib's `resolve_command_first()` — **preserve its exact
      existing precedence**; this is the highest-risk migration step and
      must ship with its own explicit before/after test proving identical
      behavior for: command present+succeeds, command present+fails, command
      absent+direct value present, both absent.
- [ ] Add `agent-dispatch` as a consumer of the new lib: `pyproject.toml`
      dependency + `[tool.uv.sources]` entry, plus the matching preinstall
      addition in its own `scripts/init.sh`/`init.ps1` if it has an
      equivalent non-uv fallback list (confirm during this phase).
- [ ] No behavior change for existing agent-dispatch deployments — this is a
      pure refactor; existing tests must continue to pass unmodified in
      intent (updates only for the new call shape).

### Phase 3 — Add `_COMMAND` support to agent-vault
- [ ] `AGENT_VAULT_CORE_TOKEN_COMMAND` via the shared lib's
      `resolve_direct_first()`, consumed wherever `AGENT_VAULT_CORE_TOKEN` is
      read today (`plugins/agent-vault/src/agent_vault/core_ext.py:47,106`).
- [ ] Packaging: `pyproject.toml` dependency + `[tool.uv.sources]` entry, plus
      add the new lib to agent-vault's own preinstall workspace-path-dep list
      (confirm its exact location/shape during this phase — follow the
      `agent-mcp/scripts/init.sh`/`init.ps1` pattern cited in Context as the
      model, adapted to agent-vault's own installer layout).
- [ ] Tests mirroring agent-dispatch's existing coverage shape.

### Phase 4 — Expand scope to agent-dispatch's remaining token and agent-index
- [ ] `agent-dispatch`: `AGENT_DISPATCH_TOKEN` is read directly from
      `os.environ` at **seven call sites total** (see Context): `client_token()`
      itself, `config.py:231` (inside `load_config()`), `board_cli.py` x4, and
      `producers/webhook.py:167`. These sites split into two groups with
      different risk profiles:
      - **Actual consumption sites** (`client_token()`, `board_cli.py` x4,
        `webhook.py:167`): consolidate each direct
        `os.environ.get("AGENT_DISPATCH_TOKEN")` read to call `client_token()`
        instead (a mechanical, behavior-preserving refactor — test each call
        site still gets the same value it did before), **then** add
        `AGENT_DISPATCH_TOKEN_COMMAND` support to `client_token()` via
        `resolve_direct_first()` so every one of these consumers benefits
        automatically.
      - **`config.py:231`, inside `load_config()`: leave this one as a raw
        env read, do NOT route it through the command-resolving
        `client_token()`.** `load_config()` is called for purposes that don't
        need the token at all (e.g. `client_url()` only needs host/port for
        addressing); making it execute a token-fetch command as a side
        effect of unrelated config resolution would violate the same
        discipline `resolve_control_token()`'s own docstring already states
        for the existing `_COMMAND` pairs (deliberately *not* called from
        `load_config()`). `Config.token` stays the raw direct value; only
        actual use sites resolve the command-backed form.
      - `no_cli_prompts.py:92` generates a **standalone helper script** that
        independently resolves `AGENT_DISPATCH_TOKEN` in a separate process —
        it will NOT inherit `client_token()`'s changes automatically. Update
        the generated helper's own token-resolution logic to call the shared
        lib directly (or shell out to the same command), with its own test.
      - `agent-codespaces`/`agent-containers`' peer env-var allowlists
        forward today's named dispatch vars to spawned codespace/container
        peers but do not yet know about `AGENT_DISPATCH_TOKEN_COMMAND` — add
        the new var name to both allowlists so a command-sourced token
        actually propagates to a spawned peer instead of silently arriving
        tokenless.
- [ ] `agent-index`: add `AGENT_INDEX_ADO_TOKEN_COMMAND` via
      `resolve_direct_first()`, consumed by the Azure DevOps source
      (`azure_devops.py:62-66`). Confirm whether `agent-index`'s other
      tokens (`CELL_TRANSACTION_TOKEN`/`CELL_LOCK_TOKEN`/`CELL_START_TOKEN`)
      are genuinely internally-generated (as currently assumed, hence out of
      scope above) before closing this phase — re-verify, don't just repeat
      the earlier assumption.
- [ ] `agent-index`: add `AGENT_INDEX_GITHUB_TOKEN_COMMAND` via
      `resolve_direct_first()`, consulted in `_env_token()`'s resolution
      chain (`sources/github.py:283-287`). **Precedence, matching
      `resolve_direct_first()`'s actual (not inverted) semantics:** the
      direct `AGENT_INDEX_GITHUB_TOKEN` value wins when set; the `_COMMAND`
      fetch runs only when `AGENT_INDEX_GITHUB_TOKEN` is unset. Both still
      precede the ambient `GH_TOKEN`/`GITHUB_TOKEN` CLI fallbacks exactly as
      today — i.e. the resolution order becomes `AGENT_INDEX_GITHUB_TOKEN` →
      `AGENT_INDEX_GITHUB_TOKEN_COMMAND` → `GH_TOKEN` → `GITHUB_TOKEN`. Do
      **not** add a
      `_COMMAND` variant for the ambient `GH_TOKEN`/`GITHUB_TOKEN` names
      themselves — those are shared, external-tool-owned conventions outside
      this plugin's own credential surface.
- [ ] Packaging for agent-index, following the same per-plugin pattern as
      Phase 3.

### Phase 5 — Docs
- [ ] Each touched plugin's own docs/README gains every new `_COMMAND`
      variable in its documented env-var table, following the existing
      `AGENT_DISPATCH_*_TOKEN_COMMAND` documentation shape as the model.
- [ ] Note the new shared lib in `CONTRIBUTING.md`'s vendored-libs table if
      that table is exhaustive (confirm during Phase 1).

## Validation Plan

- [ ] New shared-lib unit tests (Phase 1) pass via the bounded test runner,
      covering both `resolve_direct_first()` and `resolve_command_first()`
      **and both POSIX and Windows-style command strings** (the shlex
      Windows-path fix).
- [ ] agent-dispatch's full existing test suite passes unmodified in intent
      after the Phase 2 migration — proves the refactor preserves current
      behavior exactly, **including `producer_capability()`'s command-first
      precedence** (the highest-risk migration step).
- [ ] New agent-vault, agent-dispatch (`AGENT_DISPATCH_TOKEN` — covering the
      five consumption-site consolidations onto `client_token()`, the
      standalone `no_cli_prompts.py` helper, and the
      agent-codespaces/agent-containers peer allowlist propagation, but
      explicitly *not* routing `config.py:231`'s `load_config()` read
      through command resolution), and agent-index (`AGENT_INDEX_ADO_TOKEN`
      and `AGENT_INDEX_GITHUB_TOKEN`) tests (Phase 4) pass, each proving:
      direct env wins (or loses, per the correct precedence for that call
      site) when both are set; command fetch works when only `_COMMAND` is
      set; absence of both resolves to `None`/not-configured, matching each
      plugin's existing behavior for "no token"; and `load_config()` never
      executes a token-fetch command as a side effect of unrelated config
      resolution.
- [ ] Packaging validation: for each touched plugin, a clean non-uv install
      (bare `pip`, simulating `HAVE_UV=0`) succeeds and the plugin can import
      the shared lib — proves the installer preinstall-list additions are
      correct, not just the `pyproject.toml`/`[tool.uv.sources]` entries.
- [ ] Manual smoke: for agent-vault, set only the `_COMMAND` var to a trivial
      `echo <value>` and confirm the service actually authenticates using the
      fetched value.

## Proposal

_Pending — Phase 1 design (exact lib name/shape, which existing vendored lib
conventions to mirror) to be elaborated once this plan clears review._

## Journal

### 2026-10-02 — Kickoff
- Effort created from an operator request surfaced immediately after landing
  a durable `agent-bridge` packaging fix (PR #4908) in the same private
  downstream session. Candidate token inventory, shared-lib approach, and
  effort tracking confirmed with the operator before any implementation
  started.

### 2026-10-02 — Review round 1 (PR #4910)
- Copilot review flagged: (1) a High-severity scope gap —
  `producer_capability()`'s command-first precedence wasn't accounted for and
  risked a silent behavior change; (2) a Medium packaging gap — the shared
  lib needs real dependency/installer wiring per consumer, not just source;
  (3) a Low section-set gap — missing `Vision`/`## Coordination`; (4) a Low
  scope-completeness gap — `AGENT_DISPATCH_TOKEN`/`AGENT_INDEX_ADO_TOKEN`
  weren't covered by the stated "every operator-managed token" claim. All
  four addressed in this revision: added `resolve_command_first()` as a
  distinct function and a dedicated Phase 2 sub-task for the
  `producer_capability()` migration; added explicit packaging/installer
  sub-tasks to Phases 2-5; added `Vision`/`## Coordination`; added Phase 5
  covering both previously-missed tokens.

### 2026-10-02 — Review round 2 (PR #4910)
- Copilot review flagged: (1) a Medium cross-platform gap — the proposed
  shared primitive would inherit agent-dispatch's existing `shlex.split()`
  bug (POSIX mode mangles Windows backslash paths); (2) a Medium scope gap —
  Phase 5's `AGENT_DISPATCH_TOKEN` migration only named `client_token()`,
  missing five other direct `os.environ.get("AGENT_DISPATCH_TOKEN")` reads
  (`config.py:231`, `board_cli.py` x4, `producers/webhook.py:167`); (3) a Low
  public-artifact violation — this file named a private downstream repo,
  issue number, and hostname. All three addressed: Phase 1 now specifies
  `shlex.split(command, posix=(os.name != "nt"))` with explicit
  Windows-path test coverage; Phase 5 now consolidates all six
  `AGENT_DISPATCH_TOKEN` read sites onto `client_token()` first, then adds
  `_COMMAND` support there; every private repo/issue/hostname reference
  removed or redacted (hybrid-split note, Context survey line, and the
  Request's verbatim operator quote).

### 2026-10-02 — Review round 3 (PR #4910)
- Copilot review flagged: (1) a High-severity scope error — `agent-mcp`'s
  `AGENT_MCP_CONTROL_TOKEN` is actually an internally-generated,
  per-cutover-generation coordination secret (always injected directly by
  `cutover.py`, persisted to a PID sidecar by `serve.py`), not an
  operator-managed credential; a `_COMMAND` slot there would never be
  consulted; (2) a Medium previously-missed gap — `agent-index`'s
  `AGENT_INDEX_GITHUB_TOKEN` (`sources/github.py:283-287`) still wasn't
  covered by the "every operator-managed token" claim; (3) a Low
  public-artifact miss — the **PR description** (not just the file) still
  named the private downstream repo/effort. Addressed: dropped `agent-mcp`
  from scope entirely (Phase 3 removed, moved to "explicitly out of scope"
  alongside the other internally-generated tokens) rather than redesigning
  its unrelated cutover/sidecar lifecycle; added `AGENT_INDEX_GITHUB_TOKEN`
  coverage to the agent-index phase, with explicit precedence over the
  existing ambient `GH_TOKEN`/`GITHUB_TOKEN` fallbacks (which are
  deliberately NOT given their own `_COMMAND`, being external-tool-owned
  conventions); renumbered remaining phases; will update the PR description
  itself to match this file's generic framing before the next push.

### 2026-10-02 — Review round 4 (PR #4910)
- Copilot review flagged: (1) a count error — the plan said "six" direct
  `AGENT_DISPATCH_TOKEN` read sites when the actual inventory is seven
  (including `client_token()` itself); (2) a side-effect risk — routing
  `config.py:231` (inside `load_config()`) through `client_token()` would
  make generic config loading (e.g. `client_url()`, which only needs
  host/port) execute a token-fetch command as an unwanted side effect,
  contradicting the existing `resolve_control_token()` discipline this
  effort is supposed to preserve; (3) an incomplete-propagation gap — the
  standalone helper `no_cli_prompts.py` generates and resolves tokens
  independently (won't inherit `client_token()` changes), and
  agent-codespaces/agent-containers' peer env-var allowlists don't yet know
  the new `_COMMAND` var name; (4) a precedence contradiction — the
  agent-index GitHub token sub-task named `resolve_direct_first()` but then
  described command-first behavior. All four addressed: corrected the count
  to seven and explicitly listed all seven sites; `config.py:231` now stays
  a raw read, excluded from the `client_token()` consolidation, with its own
  Validation Plan line; added explicit sub-tasks for the standalone helper
  and both peer allowlists; fixed the GitHub-token precedence description to
  match `resolve_direct_first()`'s actual semantics (direct value wins,
  command is the fallback, both still precede the ambient CLI fallbacks).
  Also removed "flagged by review"-style process narration throughout in
  favor of stating the technical facts/decisions directly (the Journal
  entries already carry the review history).
