# Configurable token-command sourcing for every agent-* service

- **Slug:** `token-command-sourcing`
- **Repo:** copilot-extensions (generalizes a pattern already proven in
  `plugins/agent-dispatch`; consumers: `plugins/agent-mcp`, `plugins/agent-vault`)
- **Branch(es):** per-phase PRs against `dev`
- **Created:** 2026-10-02
- **Status:** Draft
- **Umbrella issue:** _to file once this effort's plan clears review_
- **Sub-issues:** _TBD, one per Plan phase_
- **Hybrid split:** this is the canonical, generalized effort. A private
  facility-specific companion (vault wiring, docs, deployment env files) lives
  in `aperture-labs` as `facility-token-command-adoption` and links back here;
  it does not duplicate this plan.

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
parallel ones), and add `_COMMAND` support to the plugins currently missing it.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Lambda-Core (host) | Designs the shared lib, migrates agent-dispatch, adds support to agent-mcp/agent-vault, drives PRs | local `copilot-extensions` worktree |

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

**Already has the `_COMMAND` pattern (the reference implementation to
extract):**
- `agent-dispatch`: `AGENT_DISPATCH_CONTROL_TOKEN[_COMMAND]`,
  `AGENT_DISPATCH_SHARED_TOKEN[_COMMAND]`,
  `AGENT_DISPATCH_SHARED_CONTROL_TOKEN[_COMMAND]` — see
  `resolve_control_token()` / `_run_token_command()` in
  `plugins/agent-dispatch/src/agent_dispatch/config.py`.

**Explicitly out of scope** — internally-generated coordination secrets, not
operator-sourced credentials, so a `_COMMAND` slot doesn't apply:
`agent-index`'s `CELL_TRANSACTION_TOKEN`/`CELL_LOCK_TOKEN`/`CELL_START_TOKEN`,
`agent-ssh`'s `AGENT_SSH_KEEPER_TOKEN`, `agent-worktrees`' handoff/assignment
tokens.

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
      `zdd`, etc.) exposing a `resolve_token(direct_var, command_var) -> str |
      None` function and the underlying `run_token_command()` helper, lifted
      from `agent-dispatch`'s `resolve_control_token()` /
      `_run_token_command()`.
- [ ] Preserve the existing documented semantics: direct env value wins over
      the command; the command's stdout (stripped) is the token; never called
      as a side effect of unrelated config resolution (only where the token is
      actually needed).
- [ ] Unit tests for the new lib (direct value, command fetch, neither set,
      command failure, command producing empty output).

### Phase 2 — Migrate agent-dispatch onto the shared lib
- [ ] Replace `agent-dispatch`'s own `resolve_control_token()` /
      `_run_token_command()` with calls into the new shared lib, for all three
      of its token pairs (`AGENT_DISPATCH_CONTROL_TOKEN`,
      `AGENT_DISPATCH_SHARED_TOKEN`, `AGENT_DISPATCH_SHARED_CONTROL_TOKEN`).
- [ ] No behavior change for existing agent-dispatch deployments — this is a
      pure refactor; existing tests must continue to pass unmodified in
      intent (updates only for the new call shape).

### Phase 3 — Add `_COMMAND` support to agent-mcp
- [ ] `AGENT_MCP_CONTROL_TOKEN_COMMAND` via the shared lib, consumed wherever
      `AGENT_MCP_CONTROL_TOKEN` is read today
      (`plugins/agent-mcp/src/agent_mcp/__main__.py:620`).
- [ ] Tests mirroring agent-dispatch's existing coverage shape.

### Phase 4 — Add `_COMMAND` support to agent-vault
- [ ] `AGENT_VAULT_CORE_TOKEN_COMMAND` via the shared lib, consumed wherever
      `AGENT_VAULT_CORE_TOKEN` is read today
      (`plugins/agent-vault/src/agent_vault/core_ext.py:47,106`).
- [ ] Tests mirroring agent-dispatch's existing coverage shape.

### Phase 5 — Docs
- [ ] Each touched plugin's own docs/README gains the `_COMMAND` variable in
      its documented env-var table, following the existing
      `AGENT_DISPATCH_*_TOKEN_COMMAND` documentation shape as the model.
- [ ] Note the new shared lib in `CONTRIBUTING.md`'s vendored-libs table if
      that table is exhaustive (confirm during Phase 1).

## Validation Plan

- [ ] New shared-lib unit tests (Phase 1) pass via the bounded test runner.
- [ ] agent-dispatch's full existing test suite passes unmodified in intent
      after the Phase 2 migration — proves the refactor preserves current
      behavior exactly.
- [ ] New agent-mcp and agent-vault tests (Phases 3-4) pass, each proving:
      direct env wins when both are set; command fetch works when only
      `_COMMAND` is set; absence of both resolves to `None`/not-configured,
      matching each plugin's existing behavior for "no token."
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
