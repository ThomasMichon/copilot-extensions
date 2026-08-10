# copilot-extensions Configurator

The standalone, **out-of-plugin** installer & configurator for the
copilot-extensions harness. It is deliberately **not** a Copilot plugin and is
**not** delivered through the marketplace/plugin pipe — it is its own payload,
fetched and run directly, because the thing that must guarantee the plugins'
prerequisites cannot itself be one of those inert plugins. It is the one piece
that must work *before* the plugins do.

> **Status: Phase 0 skeleton.** Today this only proves the app is delivered and
> runs outside the plugin pipe. The real work — prerequisite provisioning, core
> install, repo adoption/discovery, the visual configurator, and presets — is
> being built out under umbrella issue
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
