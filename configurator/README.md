# copilot-extensions Configurator

The standalone, **out-of-plugin** installer & configurator for the
copilot-extensions harness. It is deliberately **not** a Copilot plugin and is
**not** delivered through the marketplace/plugin pipe — it is its own payload,
fetched and run directly, because the thing that must guarantee the plugins'
prerequisites cannot itself be one of those inert plugins. It is the one piece
that must work *before* the plugins do.

> **Status: Phase 1 — plugin-knowledge model.** On top of the Phase 0
> out-of-plugin skeleton, the Configurator now carries a **dependency-free
> declarative model** of every plugin (prerequisites, managed config, and "what
> to do" to make each ready). The remaining work — prerequisite provisioning,
> core install, repo adoption/discovery, the visual configurator, and presets —
> is being built out under umbrella issue
> [#352](https://github.com/ThomasMichon/copilot-extensions/issues/352) and the
> vision [`visions/installer/`](../visions/installer/README.md).

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

## Plugin-knowledge model (Phase 1)

The Configurator carries its **own** declarative catalog of every plugin —
prerequisites, the config each manages, and the ordered "what to do" to make it
ready — in [`src/configurator/data/plugins.toml`](src/configurator/data/plugins.toml),
read by [`catalog.py`](src/configurator/catalog.py). Inspect it:

```bash
uv run python -m configurator plugins              # list what the installer knows
uv run python -m configurator plugins agent-worktrees   # one plugin's prereqs/config/steps
uv run python -m configurator plugins --prereqs    # de-duplicated union of prerequisites
uv run python -m configurator plugins --reconcile  # cross-check vs a nearby checkout
```

This resolves **DQ2** in favor of an installer-side catalog: it imports no
plugin code and requires no plugin to publish anything installer-specific, yet
`--reconcile` opportunistically cross-checks it against metadata plugins already
publish for their own reasons (the marketplace entry, `scripts/service.yaml`
prereqs) when a checkout is present — the forcing function that keeps the catalog
honest. A contract test asserts the boundary holds in both directions.

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
