# delegation-guidance (deprecated)

> **This plugin is deprecated.** Its content migrated into
> [`agent-conduct-guidance`](../agent-conduct-guidance/) as that plugin's
> coordinator-first delegation module. Enable
> `agent-conduct-guidance@copilot-extensions` instead, and disable this one.

## Why this plugin still exists

`agent-conduct-guidance` consolidates several independent, generally-good
ambient conduct-guidance modules (delegation, process-spawn hygiene, and any
future addition) under one roof, instead of proliferating single-purpose
plugins. This plugin's name is kept installable — a no-op placeholder — so
that any repository or personal settings still listing
`delegation-guidance@copilot-extensions` in `enabledPlugins` does not fail to
resolve. It ships **no skills and no instruction projections of its own**.

## Migration

In your Copilot settings (`~/.copilot/settings.json`, a repo's
`.github/copilot/settings.json`, or a workspace settings file), replace:

```json
"delegation-guidance@copilot-extensions": true
```

with:

```json
"agent-conduct-guidance@copilot-extensions": true
```

Leaving both enabled is harmless but wasteful — this plugin contributes
nothing further, so there is no duplicate content risk, but there is no
reason to keep the old entry either.

See [`agent-conduct-guidance`'s README](../agent-conduct-guidance/README.md)
for the current delegation guidance, the `delegating-work` skill, and the
model-routing configuration this module still supports.

Contributions follow the repository's PR-required workflow in
[`CONTRIBUTING.md`](../../CONTRIBUTING.md). File issues in the
[`ThomasMichon/copilot-extensions`](https://github.com/ThomasMichon/copilot-extensions/issues)
repository.
