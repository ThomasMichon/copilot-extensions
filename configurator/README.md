# copilot-extensions Configurator

The standalone, **out-of-plugin** installer & configurator for the
copilot-extensions harness. It is deliberately **not** a Copilot plugin and is
**not** delivered through the marketplace/plugin pipe — it is its own payload,
fetched and run directly, because the thing that must guarantee the plugins'
prerequisites cannot itself be one of those inert plugins. It is the one piece
that must work *before* the plugins do.

> **Status: Phase 1 — plugin-knowledge model.** On top of the Phase 0
> out-of-plugin skeleton, the Configurator now carries a **dependency-free
> **Status: Phase 2 — prerequisites & core install.** On top of the Phase 1
> plugin-knowledge model, the Configurator now **detects** the baseline
> prerequisites, **plans/provisions** the missing ones (restart-aware), and
> **drives the harness's own** agent-worktrees core install (idempotent; heals a
> partial install). The remaining work — repo adoption/discovery, the visual
> configurator, and presets — is being built out under umbrella issue
> [#352](https://github.com/ThomasMichon/copilot-extensions/issues/352) and the
> vision [`visions/installer/`](../visions/installer/README.md).

## Set up the harness

```bash
uv run python -m configurator doctor        # report prerequisites + the core install
uv run python -m configurator setup         # plan provisioning + core install (dry-run)
uv run python -m configurator setup --apply # actually provision + drive the real installer
```

`doctor` is read-only; `setup` is **dry-run by default** and only changes the
machine with `--apply`. Provisioning is restart-aware (it tells you to restart
your shell after a PATH change) and idempotent (re-running heals a partial
install). The core install is never reimplemented — the Configurator locates and
calls agent-worktrees' own `install.{ps1,sh}`.

## One-line bootstrap

**Windows (PowerShell):**

```powershell
iex (irm https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/configurator/bootstrap.ps1)
```

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/ThomasMichon/copilot-extensions/main/configurator/bootstrap.sh | bash
```

The bootstrap fetches this `configurator/` payload and runs it. Phase 0 assumes
`git` and `uv` are already present; automatic prerequisite provisioning (and
restart prompts) lands in Phase 2
([#355](https://github.com/ThomasMichon/copilot-extensions/issues/355)). Set
`CONFIGURATOR_REF` to fetch a ref other than `main`.

## Manage the harness (state views)

Once set up, the Configurator is also the ongoing **Manager** — a read-only
window (today) onto the real config state, read from the files the harness
already writes:

```bash
uv run python -m configurator projects        # harness repos (binstubs + profiles)
uv run python -m configurator projects <name> # config dir, linked knowledge repo, profiles, enabled plugins
uv run python -m configurator repos           # every known repo + indicators
uv run python -m configurator repos <name>    # worktree mode · agent mode · pr model · ownership · remote
uv run python -m configurator plugins --status  # known plugins vs. what is enabled user-global
```

**Projects** are the repos promoted to first-class harness projects (worthy of
binstubs + profiles, in `projects.yaml`); **Repos** are everything else in the
registry. The views expose worktree mode, agent mode, PR model, ownership, the
linked knowledge repo, and per-project enabled plugins. Config *editing* (linking
a knowledge repo, per-plugin config, adoption) is being built out under Phase 3/4
([#356](https://github.com/ThomasMichon/copilot-extensions/issues/356) /
[#357](https://github.com/ThomasMichon/copilot-extensions/issues/357)).

## Plugin-knowledge model (Phase 1)

The Configurator learns **which** plugins exist dynamically from the marketplace
— a nearby checkout when present, otherwise the remote published marketplace ref
(the same ref the bootstrap fetches; override with `CONFIGURATOR_REF`). Its
installer-owned catalog
([`src/configurator/data/plugins.toml`](src/configurator/data/plugins.toml), read
by [`catalog.py`](src/configurator/catalog.py) + composed in
[`model.py`](src/configurator/model.py)) is a **knowledge overlay** on that
membership: `kind`, the ordered "what to do" steps, and any prereqs beyond what a
plugin publishes. A discovered plugin with no catalog entry is kept with
**inferred defaults**, so the installer never breaks on a newly-added plugin.

```bash
uv run python -m configurator plugins              # effective model (discovered + overlay)
uv run python -m configurator plugins agent-worktrees   # one plugin's prereqs/config/steps
uv run python -m configurator plugins --prereqs    # de-duplicated union of prerequisites
uv run python -m configurator plugins --reconcile  # coverage: uncovered / phantom / prereq drift
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

The Configurator *knows about* the plugins and their configuration and "what to
do" to make each ready — but **neither depends on the other**. No plugin
requires the Configurator, and the Configurator never joins a plugin's
dependency graph. A plugin installed and run with the Configurator never present
behaves exactly the same.

## Develop

```bash
cd configurator
uv run python -m configurator          # run the app
uv run python -m configurator --version
uv run pytest                          # tests
```

Python + [uv](https://docs.astral.sh/uv/); the Phase 4 visual configurator uses
[Textual](https://textual.textualize.io/).
