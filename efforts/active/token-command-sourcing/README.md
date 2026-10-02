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

**Scope note:** `agent-mcp`'s `AGENT_MCP_CONTROL_TOKEN` is not an
operator-managed credential. A fresh token is generated per cutover
generation
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

**Architectural pattern reconciliation:** this effort adds a new cross-plugin
shared runtime dependency (`libs/token-resolve/`), so it is checked against
the two governing patterns rather than treated as pure below-altitude
plumbing:
- **`docs/patterns/vendor-pointer.md`** (canonical-to-shipped materialization):
  `libs/token-resolve/` follows the existing **canonical reference** kind
  already used by `agent-procutil`, `zdd`, etc. — a live `dev`-branch
  `[tool.uv.sources]` path reference with no local vendored copy, materialized
  into a real local copy in each consumer at promotion time. This effort
  introduces no new vendoring kind; it is one more instance of an
  already-established, already-compliant pattern.
- **`docs/patterns/a-la-carte-independence.md`** (independent installability):
  the shared lib does **not** become a mandatory central coordinator or a
  runtime dependency on a sibling *plugin*. Each consumer (`agent-dispatch`,
  `agent-vault`, `agent-index`) gets its own materialized local copy via the
  same vendored-lib mechanism the existing libs already use — installing any
  one of these plugins alone still installs and runs standalone, exactly as
  today, with no dependency on another plugin being present. Cross-platform
  parity (POSIX + Windows installers, both updated per consumer in Phases
  2-4) is carried the same way the existing vendored libs already require it.
- The "no governing vision" conclusion in the header stands: these two
  patterns govern *how* a cross-plugin shared dependency is vendored and
  installed (a mechanical/structural concern, already satisfied by following
  precedent exactly), not *whether* a new architectural capability or
  guarantee is being introduced (which would need vision-level treatment).

## Request

Operator (verbatim, private repo name redacted per this repo's public-artifact
conventions): "For the control token, we need to make sure all agent-*
services which need tokens support a user- or repo-configurable 'command'
slot, for a shell/pwsh/python command that will pipe it a token. For
[our private deployment], we'll prefer sourcing from Vault." Follow-up,
clarifying the underlying goal: "just wanted to avoid putting tokens in ENV.
Prefer on-demand sourcing from contained locations." Scope decisions
confirmed: shared-lib extraction preferred over per-plugin duplication; track
as a formal effort. The concrete per-plugin consumer set is `agent-dispatch`,
`agent-vault`, and `agent-index` (see Context's scope note on `agent-mcp` and
the Journal for how that set was determined) — implementation-detail
corrections to the same original ask, not a change in intent.

## Plan

### Phase 1 — Extract the shared helper
- [ ] Create a new small vendored lib (`libs/token-resolve/` — mirrors the
      existing `libs/<name>/` vendoring convention used by `agent-procutil`,
      `zdd`, etc.) exposing:
      - `run_token_command(command: str) -> str | None` — the low-level
        primitive (parses with `shlex.split`, runs without a shell, returns
        stripped stdout), lifted from agent-dispatch's `_run_token_command()`.
        **Launch it consoleless:** pass `agent_procutil`'s
        `**no_window_kwargs()` to the `subprocess.run(...)` call (a no-op off
        Windows) — several consumers (e.g. `agent-index`) run this from a
        headless background service, and a plain subprocess launch can flash
        a visible console window on Windows. Add a headless-child test case
        alongside the POSIX/Windows parsing cases.
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
- [ ] **Register the new lib's tests in CI**, not just locally: add
      `python -m pytest -q libs/token-resolve/tests` to the shared-library
      test lane in `.github/workflows/ci.yml` (~lines 345-356, alongside the
      existing `libs/agent-procutil/tests`, `libs/zdd/tests`, etc. entries) —
      a lib with tests that only ever run locally leaves regressions
      unchecked in the gate that actually blocks merges. **This lane runs on
      `ubuntu-latest` only** — since the Windows-safe parsing fix branches on
      `os.name == "nt"`, the Windows code path is never actually exercised
      there. Add a small dedicated `windows-latest` job (following the
      existing narrow-scope pattern already used by `windows-hooks`/
      `bootstrap-killswitch-powershell-5-1` — checkout, `setup-python`, `pip
      install pytest`, then `python -m pytest -q libs/token-resolve/tests`
      only) so the Windows branch has real CI coverage, not just local
      developer-machine testing. **Wire it into the required gate:** add the
      new job's name to `pr-gate`'s own `needs:` list
      (`.github/workflows/ci.yml:~772-780`) — a job that exists but isn't
      listed there can fail silently without blocking the one aggregate
      check branch protection actually watches.

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

Full per-call-site inventory and precedence design extracted to
[`phase-4-remaining-tokens.md`](phase-4-remaining-tokens.md) (read it when
working this phase). Summary:

- [ ] `agent-dispatch`: `AGENT_DISPATCH_TOKEN` is read directly from
      `os.environ` at seven call sites with different risk profiles —
      consolidate the actual consumption sites (`client_token()`,
      `board_cli.py` x4, `webhook.py:167`) onto `client_token()`, add
      `_COMMAND` support there; leave `config.py:231`'s `load_config()` read
      raw (no side-effecting command fetch during generic config load); add
      explicit resolution at `_cmd_serve` (preserving the `--token` CLI
      override) and at `build_app()`/`serve()`'s own default-`cfg` path;
      update the `no_cli_prompts.py` standalone helper; propagate through the
      canonical `libs/peer-launch/peer_launch.py` source (re-synced to all 8
      consumer plugins, each needing its own changefile), `agent-containers`'
      separate `copilot_detach.py` allowlist, and the detached waiter's
      `--shared` re-exec translation (`execution_cli.py:_spawn_detached_waiter()`)
      — including clearing a stale inherited local token when only the
      shared `_COMMAND` form is configured.
- [ ] `agent-index`: add `AGENT_INDEX_ADO_TOKEN_COMMAND` and
      `AGENT_INDEX_GITHUB_TOKEN_COMMAND`, each preserving its source's
      existing explicit-constructor-override precedence ahead of both the
      direct env and the new command resolver; re-verify the
      internally-generated-token exclusion for agent-index's other tokens.
- [ ] Packaging for agent-index covers **both** its service venv AND its
      separate engine venv (`ENGINE_VENV_PYTHON`) preinstall paths in
      `install.sh`/`install.ps1` — adding the new lib to only one leaves the
      other's install broken.

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
      explicit `_cmd_serve` server-side resolution, the standalone
      `no_cli_prompts.py` helper, the canonical `libs/peer-launch/
      peer_launch.py` sync (regenerating every consumer's `_peer_launch.py`/
      `peer_launch.py` copy), and `agent-containers`' separate
      `copilot_detach.py` `_DISPATCH_ENV_KEYS` tuple, but explicitly *not*
      routing `config.py:231`'s `load_config()` read through command
      resolution), and agent-index (`AGENT_INDEX_ADO_TOKEN` and
      `AGENT_INDEX_GITHUB_TOKEN`) tests (Phase 4) pass, each proving:
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

### 2026-10-02 — Review round 5 (PR #4910)
- Copilot review: all five findings from round 4 resolved; one new Low
  finding — the effort adds a new cross-plugin shared runtime dependency,
  which is architectural enough to warrant explicit reconciliation against
  `docs/patterns/vendor-pointer.md` and `docs/patterns/a-la-carte-independence.md`,
  not just a bare "no governing vision" conclusion. Addressed: added an
  "Architectural pattern reconciliation" subsection to Context confirming
  the new lib follows the existing canonical-reference vendoring kind
  (no new pattern introduced) and preserves standalone per-plugin
  installability (no mandatory coordinator, no cross-plugin runtime
  dependency) — both already-established patterns, applied rather than
  reinvented.

### 2026-10-02 — Review round 6 (PR #4910)
- Copilot review: the pattern-reconciliation fix from round 5 resolved; two
  Medium findings carried forward from an earlier pass that hadn't yet been
  addressed: (1) the plan kept `config.py:231`'s `load_config().token` raw
  (correctly, to avoid the `load_config()` side-effect risk), but never
  added explicit resolution at the server's OWN consumption sites —
  `coordinator_cli.py:_cmd_serve` builds `cfg.token` from that same raw
  value, which then gates `server.py`'s unsafe-bind guard and request auth,
  so a `_COMMAND`-only configuration would leave the coordinator itself
  believing no token is set even though clients resolve one; (2) the
  peer-propagation sub-task named only agent-codespaces/agent-containers,
  but `_peer_launch.py` (`peer_launch.py` for agent-dispatch itself) is a
  **generated, synced copy** in 8 different plugins
  (`tools/sync-peer-launch.py`) — editing a copy directly breaks the sync
  guard; the actual edit point is the canonical
  `libs/peer-launch/peer_launch.py` source, followed by re-running the sync
  tool. Also found a second, genuinely separate allowlist in
  `agent-containers/copilot_detach.py`'s own `_DISPATCH_ENV_KEYS` tuple that
  the peer-launch sync doesn't touch at all. Both addressed: added an
  explicit `_cmd_serve`-level `resolve_direct_first()` call (not via
  `load_config()`) for the server's own token resolution; corrected the
  propagation sub-task to edit the canonical `peer_environment()` allowlist
  and re-run the sync tool, plus a separate sub-task for
  `copilot_detach.py`'s own tuple.

### 2026-10-02 — Review round 7 (PR #4910)
- Copilot review: two new findings plus three carried-forward previously-missed
  findings. New: (1) Medium — the round-6 `_cmd_serve` fix as drafted would
  have replaced `args.token or base.token` outright, silently dropping the
  explicit `--token` CLI override's precedence; (2) Low — the PR description
  had gone stale (wrong round count, wrong site count). Previously missed:
  (3) Medium — `libs/token-resolve/tests` needs registering in
  `.github/workflows/ci.yml`'s shared-library test lane, not just runnable
  locally; (4) Medium — Azure DevOps's `AzureDevOpsSource.__init__` already
  has an explicit `token=` constructor override that must keep precedence
  over both the direct env and the new command resolver; (5) Low — a few
  standing (non-Journal) sections still narrated "review round N" history
  inline instead of stating the current system plainly. All five addressed:
  `_cmd_serve`'s fix is now `args.token or resolve_direct_first(...)`,
  preserving the CLI override; added an explicit CI-registration sub-task to
  Phase 1; both Azure DevOps and GitHub (which has the identical
  constructor-override pattern, caught while fixing this) now specify the
  full `explicit arg → direct env → command → ambient CLI fallback`
  precedence chain; removed "review round" framing from the Guiding
  Intent/Request/Scope-note prose, keeping only the Journal as the review
  history record. PR description will be refreshed to match before the next
  push.

### 2026-10-02 — Review round 8 (PR #4910)
- Copilot review: the CLI-override precedence fix resolved; three new/carried
  findings. (1) Medium — `build_app()`/`serve()` have their own independent
  default-`cfg` path (`server.py:85,308`) bypassing `_cmd_serve` entirely,
  so a direct caller (library use, tests, a future entry point) would still
  get an unresolved token; (2) Medium — regenerating the canonical
  `libs/peer-launch/peer_launch.py` source changes the materialized payload
  of all 8 listed consumer plugins at once, each needing its own pending
  changefile per `CONTRIBUTING.md`'s changefile-presence check, not just
  `agent-dispatch`'s; (3) Low — the 557-line README violated the "extract
  substantial inventories to sibling docs" rule. The PR-description
  staleness finding was carried forward from a stale review cache; the
  description was already corrected before this round ran. Addressed: added
  the `build_app()`/`serve()` default-`cfg` fix (extending both
  `replace(...)` calls to also resolve `token=`, mirroring how
  `control_token` is already resolved there); added the explicit
  per-plugin changefile requirement to the peer-launch sync sub-task;
  extracted Phase 4's full per-call-site inventory and precedence design to
  `phase-4-remaining-tokens.md`, leaving a short summary + link in the main
  Plan (557 → 459 lines).

### 2026-10-02 — Review round 9 (PR #4910)
- Copilot review: four findings resolved (per-plugin changefiles, command-aware
  config at all server entry points, Phase 4 extraction, PR description); one
  new finding plus one lingering pre-existing gap. New: `execution_cli.py`'s
  `_spawn_detached_waiter()` translates a `--shared` invocation's raw shared
  tokens for its re-exec'd child but never the `_COMMAND` variants, so a
  `_COMMAND`-only shared configuration leaves the detached child with no
  matching local `_COMMAND` var (and risks a stale inherited local token
  silently winning). Pre-existing: the CI shared-library lane only runs on
  `ubuntu-latest`, so the Windows-safe parsing fix's `os.name == "nt"` branch
  is never actually exercised by CI. Addressed: added the matching
  `_COMMAND` translations (plus clearing a stale inherited local token) to
  the detached-waiter sub-task, with a dedicated regression test requirement;
  added a small dedicated `windows-latest` CI job (following the existing
  narrow-scope `windows-hooks` pattern) to Phase 1.

### 2026-10-02 — Review round 10 (PR #4910)
- Copilot review: the detached-waiter `_COMMAND` translation fix resolved;
  four new findings. (1) High — `agent-index` has TWO separate preinstall
  paths (a service venv and a separate engine venv, each installing its own
  copy of the base package) in both `install.sh`/`install.ps1`; the plan
  only covered one, which would leave the other's install broken; (2)
  Medium — `run_token_command()`'s subprocess launch needs
  `agent_procutil.no_window_kwargs()` so a headless consumer (e.g.
  agent-index) never flashes a visible console on Windows; (3) Medium — the
  new Windows CI job wasn't added to `pr-gate`'s `needs:` aggregator, so it
  could fail without blocking the one required check branch protection
  watches; (4) Low — the plan referenced a nonexistent
  `AzureDevOpsSource` class name instead of the real
  `AzureDevOpsConnector`. All four addressed: added the engine-venv
  preinstall requirement alongside the service-venv one; added the
  `no_window_kwargs()` requirement plus a headless-child test case; added
  the `pr-gate` `needs:` wiring requirement; corrected the class name
  throughout.
