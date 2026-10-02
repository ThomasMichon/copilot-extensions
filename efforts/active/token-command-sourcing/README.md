# Configurable token-command sourcing for every agent-* service

- **Slug:** `token-command-sourcing`
- **Repo:** copilot-extensions (generalizes a pattern already proven in
  `plugins/agent-dispatch`; consumers: `plugins/agent-mcp`, `plugins/agent-vault`)
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-10-02
- **Status:** Draft
- **Vision:** None — below-altitude operational/config capability. No
  existing vision governs cross-plugin credential-sourcing configuration;
  `visions/installer` covers machine bootstrap, not per-plugin runtime token
  resolution, and neither `agent-mcp` nor `agent-vault` has its own vision
  doc under `visions/plugins/`. _(agent-recommended: if a future related
  change warrants one, author a leaf vision then; this effort doesn't invent
  one to satisfy the template.)_
- **Umbrella issue:** _to file once this effort's plan clears review_
- **Sub-issues:** _TBD, one per Plan phase_
- **Hybrid split:** this is the canonical, generalized effort. A private
  facility-specific companion (vault wiring, docs, deployment env files) lives
  in `aperture-labs` as `facility-token-command-adoption` (umbrella
  [aperture-labs #7863](https://gitea.michon.ski/tmichon/aperture-labs/issues/7863))
  and links back here; it does not duplicate this plan.

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

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Lambda-Core (host) | Designs the shared lib, migrates agent-dispatch, adds support to agent-mcp/agent-vault, drives PRs | local `copilot-extensions` worktree |

## Coordination

- **Topology:** independent per-phase PRs — each phase lands and deploys
  cleanly on its own (Phase 1 is a pure addition; Phase 2 is a same-behavior
  refactor; Phases 3-5 are additive per-plugin features), so no shared feature
  branch is needed.
- **Host (owns PRs):** Lambda-Core (solo effort; no delegates yet).
- **Delegates:** none currently.
- **Handoff:** n/a (single participant).

## Context

Surveyed during the `lambda-core-wsl-20260928-122423-8918` aperture-labs
session (durable fix for the self-reintroducing agent-bridge packaging bug;
this effort was carved out as a follow-up request, not part of that fix).

**Operator-managed credential tokens found reading a plain env var with no
`_COMMAND` alternative:**
- `agent-mcp`: `AGENT_MCP_CONTROL_TOKEN`
  (`plugins/agent-mcp/src/agent_mcp/__main__.py:620`)
- `agent-vault`: `AGENT_VAULT_CORE_TOKEN`
  (`plugins/agent-vault/src/agent_vault/core_ext.py:47,106`)
- `agent-dispatch`: `AGENT_DISPATCH_TOKEN`, the plain client bearer resolved by
  `client_token()` (`agent_dispatch/config.py:533-535`) — distinct from the
  three pairs below that already have `_COMMAND`; flagged by review as a gap
  in the original scope statement.
- `agent-index`: `AGENT_INDEX_ADO_TOKEN`, the Azure DevOps source's PAT
  (`agent_index/sources/azure_devops.py:62-66`) — also flagged by review.

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
`agent-index`'s `CELL_TRANSACTION_TOKEN`/`CELL_LOCK_TOKEN`/`CELL_START_TOKEN`,
`agent-ssh`'s `AGENT_SSH_KEEPER_TOKEN`, `agent-worktrees`' handoff/assignment
tokens.

**Packaging precedent (the pattern every consumer's installer change
follows):** `agent-mcp/scripts/init.sh`'s "Preinstall workspace path deps
(non-uv fallback)" loop (~line 440) enumerates each vendored
`[tool.uv.sources]` workspace path dep as a `'<libs-dir-name>:<pypi-pkg-name>'`
string and explicitly `pip install`s it when `uv` is unavailable (bare `pip`
doesn't honor `[tool.uv.sources]` at all) — `init.ps1` (~line 664) does the
same for Windows. **Every plugin this effort touches has its own equivalent
preinstall list and needs the new shared lib added to it**, in addition to a
normal `pyproject.toml` dependency + `[tool.uv.sources]` entry. A shared
package that exists only as source with no installer/dependency wiring is not
actually deployable.

## Request

Operator (verbatim): "For the control token, we need to make sure all
agent-* services which need tokens support a user- or repo-configurable
'command' slot, for a shell/pwsh/python command that will pipe it a token.
For aperture-labs, we'll prefer sourcing from Vault." Follow-up, clarifying
the underlying goal: "just wanted to avoid putting tokens in ENV. Prefer
on-demand sourcing from contained locations." Scope decisions confirmed:
candidate list (agent-dispatch/agent-mcp/agent-vault) accepted as proposed;
shared-lib extraction preferred over per-plugin duplication; track as a
formal effort.

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
- [ ] Unit tests for the new lib covering both resolvers (direct value,
      command fetch, neither set, command failure, command producing empty
      output) plus the low-level primitive directly.

### Phase 2 — Migrate agent-dispatch onto the shared lib
- [ ] Replace `agent-dispatch`'s own `resolve_control_token()` with a thin
      wrapper over the shared lib's `resolve_direct_first()`, for all three
      of its existing `_COMMAND` pairs (`AGENT_DISPATCH_CONTROL_TOKEN`,
      `AGENT_DISPATCH_SHARED_TOKEN`, `AGENT_DISPATCH_SHARED_CONTROL_TOKEN`).
- [ ] Replace `producer_capability()`'s inline logic with a thin wrapper over
      the shared lib's `resolve_command_first()` — **preserve its exact
      existing precedence**; this is the highest-risk migration step (flagged
      by review) and must ship with its own explicit before/after test
      proving identical behavior for: command present+succeeds, command
      present+fails, command absent+direct value present, both absent.
- [ ] Add `agent-dispatch` as a consumer of the new lib: `pyproject.toml`
      dependency + `[tool.uv.sources]` entry, plus the matching preinstall
      addition in its own `scripts/init.sh`/`init.ps1` if it has an
      equivalent non-uv fallback list (confirm during this phase).
- [ ] No behavior change for existing agent-dispatch deployments — this is a
      pure refactor; existing tests must continue to pass unmodified in
      intent (updates only for the new call shape).

### Phase 3 — Add `_COMMAND` support to agent-mcp
- [ ] `AGENT_MCP_CONTROL_TOKEN_COMMAND` via the shared lib's
      `resolve_direct_first()`, consumed wherever `AGENT_MCP_CONTROL_TOKEN`
      is read today (`plugins/agent-mcp/src/agent_mcp/__main__.py:620`).
- [ ] Packaging: `pyproject.toml` dependency + `[tool.uv.sources]` entry, plus
      add the new lib to `agent-mcp/scripts/init.sh`'s (~line 440) and
      `init.ps1`'s (~line 664) preinstall workspace-path-dep lists.
- [ ] Tests mirroring agent-dispatch's existing coverage shape.

### Phase 4 — Add `_COMMAND` support to agent-vault
- [ ] `AGENT_VAULT_CORE_TOKEN_COMMAND` via the shared lib's
      `resolve_direct_first()`, consumed wherever `AGENT_VAULT_CORE_TOKEN` is
      read today (`plugins/agent-vault/src/agent_vault/core_ext.py:47,106`).
- [ ] Packaging: same pattern as Phase 3 — `pyproject.toml` +
      `[tool.uv.sources]` + the plugin's own preinstall list, confirming its
      exact location during this phase (agent-vault's installer layout may
      differ from agent-mcp's).
- [ ] Tests mirroring agent-dispatch's existing coverage shape.

### Phase 5 — Expand scope to the two review-flagged gaps
- [ ] `agent-dispatch`: add `AGENT_DISPATCH_TOKEN_COMMAND` via
      `resolve_direct_first()`, consumed by `client_token()`
      (`config.py:533-535`).
- [ ] `agent-index`: add `AGENT_INDEX_ADO_TOKEN_COMMAND` via
      `resolve_direct_first()`, consumed by the Azure DevOps source
      (`azure_devops.py:62-66`). Confirm whether `agent-index`'s other
      tokens (`CELL_TRANSACTION_TOKEN`/`CELL_LOCK_TOKEN`/`CELL_START_TOKEN`)
      are genuinely internally-generated (as currently assumed, hence out of
      scope above) before closing this phase — re-verify, don't just repeat
      the earlier assumption.
- [ ] Packaging for both, following the same per-plugin pattern as Phases
      3-4.

### Phase 6 — Docs
- [ ] Each touched plugin's own docs/README gains every new `_COMMAND`
      variable in its documented env-var table, following the existing
      `AGENT_DISPATCH_*_TOKEN_COMMAND` documentation shape as the model.
- [ ] Note the new shared lib in `CONTRIBUTING.md`'s vendored-libs table if
      that table is exhaustive (confirm during Phase 1).

## Validation Plan

- [ ] New shared-lib unit tests (Phase 1) pass via the bounded test runner,
      covering both `resolve_direct_first()` and `resolve_command_first()`.
- [ ] agent-dispatch's full existing test suite passes unmodified in intent
      after the Phase 2 migration — proves the refactor preserves current
      behavior exactly, **including `producer_capability()`'s command-first
      precedence** (the highest-risk migration step).
- [ ] New agent-mcp, agent-vault, agent-dispatch (`AGENT_DISPATCH_TOKEN`), and
      agent-index tests (Phases 3-5) pass, each proving: direct env wins (or
      loses, per the correct precedence for that call site) when both are
      set; command fetch works when only `_COMMAND` is set; absence of both
      resolves to `None`/not-configured, matching each plugin's existing
      behavior for "no token."
- [ ] Packaging validation: for each touched plugin, a clean non-uv install
      (bare `pip`, simulating `HAVE_UV=0`) succeeds and the plugin can import
      the shared lib — proves the installer preinstall-list additions are
      correct, not just the `pyproject.toml`/`[tool.uv.sources]` entries.
- [ ] Manual smoke: for at least one of agent-mcp/agent-vault, set only the
      `_COMMAND` var to a trivial `echo <value>` and confirm the service
      actually authenticates using the fetched value.

## Proposal

_Pending — Phase 1 design (exact lib name/shape, which existing vendored lib
conventions to mirror) to be elaborated once this plan clears review._

## Journal

### 2026-10-02 — Kickoff
- Effort created from an operator request surfaced immediately after landing
  the durable agent-bridge packaging fix (PR #4908) in the same aperture-labs
  session. Candidate token inventory, shared-lib approach, and effort
  tracking confirmed with the operator before any implementation started.

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
