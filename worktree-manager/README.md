# copilot-extensions Worktree Manager

The standalone, **out-of-plugin** harness control plane (installer, configurator
& worktree launcher) for the copilot-extensions harness. It is deliberately
**not** a Copilot plugin and is **not** delivered through the marketplace/plugin
pipe — it is its own payload, fetched and run directly, because the thing that
must guarantee the plugins' prerequisites cannot itself be one of those inert
plugins. It is the one piece that must work *before* the plugins do.

> **Status: active control-plane extraction.** The Manager bootstraps and updates
> itself, provisions the harness core, reads harness state, shells out to the
> worktree engine, and hosts the first independent Textual Picker. It now also
> owns the versioned contract for plugin-contributed pivots, actions, cards/forms,
> and configuration sections. The Picker now renders contributed pivot snapshots
> asynchronously from that contract; action/streaming parity, repo adoption/config
> editing, mux/profile relocation, and presets remain under umbrella issue
> [#352](https://github.com/ThomasMichon/copilot-extensions/issues/352) and the
> contract/render extraction is tracked by
> [#1165](https://github.com/ThomasMichon/copilot-extensions/issues/1165) and
> [#1174](https://github.com/ThomasMichon/copilot-extensions/issues/1174).

## Set up the harness

```bash
uv run python -m worktree_manager doctor        # report prerequisites + the core install
uv run python -m worktree_manager setup         # plan provisioning + core install (dry-run)
uv run python -m worktree_manager setup --apply # actually provision + drive the real installer
```

`doctor` is read-only; `setup` is **dry-run by default** and only changes the
machine with `--apply`. Provisioning is restart-aware (it tells you to restart
your shell after a PATH change) and idempotent (re-running heals a partial
install). The core install is never reimplemented — the Worktree Manager locates and
calls agent-worktrees' own `install.{ps1,sh}`.

## One-line bootstrap

**Windows (PowerShell):**

```powershell
iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.ps1)
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/worktree-manager/bootstrap.sh | bash
```

The bootstrap fetches this `worktree-manager/` payload and **version-installs** it
under the same convention as the harness's other installers — an immutable
`~/.worktree-manager/versions/<version>/` slot, a plain-text
`~/.worktree-manager/current-version` marker, and a `~/.local/bin/worktree-manager`
binstub (`.cmd`/`.ps1` on Windows) — then launches it. Re-running the one-liner is
**version-gated** (a no-op when already current). After the first run, invoke
`worktree-manager …` directly (ensure `~/.local/bin` is on `PATH`). The bootstrap
**auto-provisions its prerequisites**: `uv` is installed user-local (no admin) when
missing, and `git` is installed best-effort where a package manager exists —
otherwise the payload is fetched as a GitHub tarball, so a bare machine bootstraps
even without `git`. It amends the current session's `PATH` and prompts for a
restart when it can't. Set
`WORKTREE_MANAGER_ROOT` to relocate the install root. Inspect/repair the versioned
install with `worktree-manager self-install` (dry-run) / `--apply`, and see it in
`worktree-manager doctor`.

## Choose the update source (fork / canary branch)

By default the self-updater pulls the Worktree Manager payload from the canonical
GitHub repo's `main`. To track a **fork** or a **canary / different source
branch** for future updates, set a **user-level source override** — a small config
file at `~/.worktree-manager/config.toml` (`[source]` table), managed with the
`source` command (there is **no** environment variable for this):

```bash
worktree-manager source                                   # show the effective repo + ref
worktree-manager source set --ref canary                  # track a different branch
worktree-manager source set --repo https://github.com/<fork>/copilot-extensions.git
worktree-manager source reset                             # back to the canonical default
```

The override is honored by both `worktree-manager update` (the self-update step)
and the bootstrap one-liner (it reads the same config file on re-run), and shows
up in `worktree-manager doctor`. Resolution is simply **config file → built-in
default**; the file is human-editable:

```toml
[source]
repo = "https://github.com/<fork>/copilot-extensions.git"
ref  = "canary"
```

## Manage the harness (state views)

Once set up, the Worktree Manager is also the ongoing **Manager** — a read-only
window (today) onto the real config state, read from the files the harness
already writes:

```bash
uv run python -m worktree_manager projects        # harness repos (binstubs + profiles)
uv run python -m worktree_manager projects <name> # config dir, linked knowledge repo, profiles, enabled plugins
uv run python -m worktree_manager repos           # every known repo + indicators
uv run python -m worktree_manager repos <name>    # worktree mode · agent mode · pr model · ownership · remote
uv run python -m worktree_manager worktrees       # live worktree counts per project (via the engine)
uv run python -m worktree_manager worktrees <name> # one project's worktrees: state, sync tags, titles
uv run python -m worktree_manager plugins --status  # known plugins vs. what is enabled user-global
uv run python -m worktree_manager contracts --project <name> # contributed pivots/actions/cards/config
```

The **worktrees** views are the first slice of the extracted Picker: they read
**live** worktrees by shelling out to the `agent-worktrees` engine
(`agent-worktrees --project <p> list --json --classify`) — the **process
boundary** the Textual Picker will sit on. The Manager never imports the plugin;
the only coupling is the engine's stable `--json` verbs (the pinned
[engine ↔ Picker contract](../plugins/agent-worktrees/docs/engine-picker-contract.md)),
and the client ([`engine_client.py`](src/worktree_manager/engine_client.py))
tolerates an older engine by degrading a request rather than failing.

Plugin-contributed interactive surfaces are discovered independently from each
enabled plugin's installed payload. `worktree-manager contracts` validates the
versioned manifest contract and reports disabled, malformed, duplicate, legacy,
or missing-command contributions without importing plugin code or requiring a
plugin installer to write into Manager-owned state. See
[`docs/plugin-contribution-contract.md`](docs/plugin-contribution-contract.md).
Available pivot contributions appear beside Worktrees in the Picker. Their list
commands run in background workers, so the UI opens before cross-process reads
finish; each pivot keeps its own cached rows and loading/error/empty state, and
renders either its declared columns or the contract's id/title/badge fallback.
Streaming, actions, cards/forms, and configuration sections remain subsequent
parity slices; the bundled Picker stays in place until those are complete.

**Projects** are the repos promoted to first-class harness projects (worthy of
binstubs + profiles, in `projects.yaml`); **Repos** are everything else in the
registry. The views expose worktree mode, agent mode, PR model, ownership, the
linked knowledge repo, and per-project enabled plugins. Config *editing* (linking
a knowledge repo, per-plugin config, adoption) is being built out under Phase 3/4
([#356](https://github.com/ThomasMichon/copilot-extensions/issues/356) /
[#357](https://github.com/ThomasMichon/copilot-extensions/issues/357)).

## Plugin-knowledge model (Phase 1)

The Worktree Manager learns **which** plugins exist dynamically from the marketplace
— a nearby checkout when present, otherwise the remote published marketplace ref
(the same ref the bootstrap fetches; set with `worktree-manager source set --ref`). Its
installer-owned catalog
([`src/worktree_manager/data/plugins.toml`](src/worktree_manager/data/plugins.toml), read
by [`catalog.py`](src/worktree_manager/catalog.py) + composed in
[`model.py`](src/worktree_manager/model.py)) is a **knowledge overlay** on that
membership: `kind`, the ordered "what to do" steps, and any prereqs beyond what a
plugin publishes. A discovered plugin with no catalog entry is kept with
**inferred defaults**, so the installer never breaks on a newly-added plugin.

```bash
uv run python -m worktree_manager plugins              # effective model (discovered + overlay)
uv run python -m worktree_manager plugins agent-worktrees   # one plugin's prereqs/config/steps
uv run python -m worktree_manager plugins --prereqs    # de-duplicated union of prerequisites
uv run python -m worktree_manager plugins --reconcile  # coverage: uncovered / phantom / prereq drift
```

This resolves **DQ2** in favor of an installer-side catalog **over dynamic
membership**: it imports no plugin code and requires no plugin to publish
anything installer-specific, yet it reads metadata plugins already publish for
their own reasons (the marketplace entry, `scripts/service.yaml` prereqs). Because
membership is discovered there is no frozen list to police — `--reconcile` is a
*coverage* report (uncovered = discovered but not authored, running on inference;
phantom = authored but not discovered), not a hard gate. A contract test asserts
the one-way boundary holds in both directions.

## Boundary: one-way, dependency-free

The Worktree Manager *knows about* the plugins and their configuration and "what to
do" to make each ready — but **neither depends on the other**. No plugin
requires the Worktree Manager, and the Worktree Manager never joins a plugin's
dependency graph. A plugin installed and run with the Worktree Manager never present
behaves exactly the same.

## Develop

```bash
cd worktree-manager
uv run python -m worktree_manager          # run the app
uv run python -m worktree_manager --version
uv run pytest                          # tests
```

Python + [uv](https://docs.astral.sh/uv/); the Phase 4 visual worktree-manager uses
[Textual](https://textual.textualize.io/).
