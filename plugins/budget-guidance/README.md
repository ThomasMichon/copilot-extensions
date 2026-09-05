# budget-guidance

`budget-guidance` resolves current, provider-neutral usage-budget readings into
one attributable posture. The first slice is deliberately offline: strict
manual/static adapters provide partial or complete readings, deterministic
field authority composes them, and one posture drives both JSON and concise
human status.

```bash
budget-guidance status --config ./budget.json --json
budget-guidance status --config ./budget.json
```

Without `--config`, the command reads
`$BUDGET_GUIDANCE_CONFIG` or `~/.budget-guidance/config.json`. A missing file is
reported as unavailable; it is never interpreted as zero consumption or a full
allowance.

Configuration is inert JSON. The plugin does not source configuration, execute
commands from it, call a network service, store a longitudinal ledger, qualify
models, or alter routing decisions. Provider and bounded external-reader
adapters, concise session guidance, and routing composition are later slices.

See [Configuration](docs/configuration.md) and
[Architecture](docs/architecture.md).
