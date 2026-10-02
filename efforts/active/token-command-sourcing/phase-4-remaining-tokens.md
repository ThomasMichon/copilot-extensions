# Phase 4 detail — agent-dispatch's remaining token sites + agent-index

Linked from [`README.md`](README.md)'s Plan, Phase 4. This sub-doc carries the
full per-call-site inventory and precedence design that grew too large for
the main effort README (per `efforts/README.md`'s "extract substantial
phase designs or inventories into sibling documents" rule). Read this when
actually working Phase 4; the main README's Plan keeps only a short summary
and this link.

## agent-dispatch: `AGENT_DISPATCH_TOKEN`

`AGENT_DISPATCH_TOKEN` is read directly from `os.environ` at **seven call
sites total** (see the main README's Context): `client_token()` itself,
`config.py:231` (inside `load_config()`), `board_cli.py` x4, and
`producers/webhook.py:167`. These sites split into groups with different
risk profiles:

- **Actual consumption sites** (`client_token()`, `board_cli.py` x4,
  `webhook.py:167`): consolidate each direct
  `os.environ.get("AGENT_DISPATCH_TOKEN")` read to call `client_token()`
  instead (a mechanical, behavior-preserving refactor — test each call site
  still gets the same value it did before), **then** add
  `AGENT_DISPATCH_TOKEN_COMMAND` support to `client_token()` via
  `resolve_direct_first()` so every one of these consumers benefits
  automatically.
- **`config.py:231`, inside `load_config()`: leave this one as a raw env
  read, do NOT route it through the command-resolving `client_token()`.**
  `load_config()` is called for purposes that don't need the token at all
  (e.g. `client_url()` only needs host/port for addressing); making it
  execute a token-fetch command as a side effect of unrelated config
  resolution would violate the same discipline `resolve_control_token()`'s
  own docstring already states for the existing `_COMMAND` pairs
  (deliberately *not* called from `load_config()`). `Config.token` stays the
  raw direct value; only actual use sites resolve the command-backed form.
- **Server-side consumers also need explicit resolution, not just clients:**
  `coordinator_cli.py:_cmd_serve` builds
  `effective_token = args.token or base.token` — `base.token` is
  `load_config().token`, the deliberately-raw value above — and passes it as
  `cfg.token` into `server.py`'s `build_app()`/`serve()`, where it gates the
  unsafe-bind guard (`server.py:74-95`) and request authentication
  (`server.py:308`). If an operator sets only `AGENT_DISPATCH_TOKEN_COMMAND`
  (no direct value), the coordinator would see no token at all even though
  `client_token()`-based clients now resolve one — a functional mismatch,
  not just an inconsistency. Fix: `_cmd_serve` resolves `effective_token` as
  `args.token or resolve_direct_first("AGENT_DISPATCH_TOKEN",
  "AGENT_DISPATCH_TOKEN_COMMAND")` — **preserving the existing
  explicit-CLI-override precedence** (`--token` still wins outright; only
  the fallback changes from a bare env read to the shared lib's resolver)
  **at this one specific server-startup call site**, rather than through
  `load_config()`. This keeps `load_config()` itself side-effect-free while
  ensuring the actual point where the token gates bind safety and auth
  resolves the command-backed form too, without ever silently dropping an
  operator-supplied `--token`.
- **`build_app()`/`serve()` have their OWN independent default-`cfg` path,
  bypassing `_cmd_serve` entirely:** both functions default a `None` `cfg`
  argument via
  `cfg = cfg or replace(load_config(), control_token=resolve_control_token())`
  (`server.py:85` and `:308`) — a caller invoking either directly (library
  use, tests, or any future entry point besides the CLI's `_cmd_serve`) gets
  `token` from the same deliberately-raw `load_config()` value, with no
  `_COMMAND` resolution at all. Fix: extend both `replace(...)` calls to also
  pass `token=resolve_direct_first("AGENT_DISPATCH_TOKEN",
  "AGENT_DISPATCH_TOKEN_COMMAND")`, mirroring exactly how `control_token` is
  already resolved inline on that same line — so every entry point that can
  construct a default `Config`, not just the CLI path, resolves the
  command-backed token.
- `no_cli_prompts.py:92` generates a **standalone helper script** that
  independently resolves `AGENT_DISPATCH_TOKEN` in a separate process — it
  will NOT inherit `client_token()`'s changes automatically. Update the
  generated helper's own token-resolution logic to call the shared lib
  directly (or shell out to the same command), with its own test.
- **Peer-launch propagation goes through the canonical source, not the
  generated copies:** `_peer_launch.py` (and `agent-dispatch`'s own
  `peer_launch.py`) in every consumer plugin (agent-bridge, agent-dispatch,
  agent-codespaces, agent-containers, agent-worktrees, agent-logger,
  agent-index, agent-machines) are byte-identical generated copies of
  `libs/peer-launch/peer_launch.py`, synced via `tools/sync-peer-launch.py`
  — editing a generated copy directly breaks that sync guard. Add
  `AGENT_DISPATCH_TOKEN_COMMAND` to the canonical
  `libs/peer-launch/peer_launch.py`'s `peer_environment()` allowlist
  (following its own documented 3-step process: the mapping entry,
  target-specific environment rebinding, and confirming
  `tools/sync-installation-context.py` registration is unaffected), then run
  `tools/sync-peer-launch.py` to regenerate every consumer copy — never
  hand-edit a `_peer_launch.py`/`peer_launch.py` copy. **Changefile
  requirement:** regenerating the canonical source changes the materialized
  payload of all 8 listed plugins at once — per `CONTRIBUTING.md`'s
  changefile-presence check, this PR must carry a pending
  `tools/changefile.py add --plugin <p>` entry for **every one of those 8
  plugins**, not just `agent-dispatch`, or the promotion pipeline won't
  publish the regenerated copies for the others.
- **A separate, non-generated allowlist also needs the new var:**
  `agent-containers`' `copilot_detach.py` has its own independent
  `_DISPATCH_ENV_KEYS` tuple (lines ~24-35) governing which dispatch vars
  forward into a detached container session; it already lists
  `AGENT_DISPATCH_SHARED_TOKEN_COMMAND` but not a plain
  `AGENT_DISPATCH_TOKEN_COMMAND`. Add it there too (lines ~161-164 is where
  the tuple is consumed) — this is distinct from the `peer-launch` sync
  above, not covered by it.

## agent-index: `AGENT_INDEX_ADO_TOKEN` and `AGENT_INDEX_GITHUB_TOKEN`

- Add `AGENT_INDEX_ADO_TOKEN_COMMAND` via `resolve_direct_first()`, consumed
  by the Azure DevOps source (`azure_devops.py:62-66`). **Preserve the
  existing constructor-override precedence:** `AzureDevOpsSource.__init__`
  already accepts an explicit `token: str | None = None` parameter that wins
  over the env read (`self._token = token or
  os.environ.get("AGENT_INDEX_ADO_TOKEN")`, `azure_devops.py:45-66`). The
  full resolution order must become **explicit constructor `token=` arg →
  `AGENT_INDEX_ADO_TOKEN` (direct env) → `AGENT_INDEX_ADO_TOKEN_COMMAND`** —
  never let the new command resolver run ahead of an explicitly-passed
  `token=`. Confirm whether `agent-index`'s other tokens
  (`CELL_TRANSACTION_TOKEN`/`CELL_LOCK_TOKEN`/`CELL_START_TOKEN`) are
  genuinely internally-generated (as currently assumed, hence out of scope
  per the main README's Context) before closing this phase — re-verify,
  don't just repeat the earlier assumption.
- Add `AGENT_INDEX_GITHUB_TOKEN_COMMAND` via `resolve_direct_first()`,
  consulted in `_env_token()`'s resolution chain (`sources/github.py:283-287`).
  The GitHub source's `__init__` also has the identical
  explicit-constructor-override pattern as Azure DevOps's (`token: str |
  None = None`, `self._token = token or _env_token()`, `github.py:33-48`) —
  preserve it the same way. **Full precedence, matching
  `resolve_direct_first()`'s actual (not inverted) semantics:** explicit
  constructor `token=` arg → `AGENT_INDEX_GITHUB_TOKEN` (direct env, wins
  when set) → `AGENT_INDEX_GITHUB_TOKEN_COMMAND` (the `_COMMAND` fetch runs
  only when the direct env is unset) → ambient `GH_TOKEN` → `GITHUB_TOKEN`
  (unchanged CLI fallbacks). Do **not** add a `_COMMAND` variant for the
  ambient `GH_TOKEN`/`GITHUB_TOKEN` names themselves — those are shared,
  external-tool-owned conventions outside this plugin's own credential
  surface.
- Packaging for agent-index, following the same per-plugin pattern as
  Phase 3.
