# agent-session-liveness-probe

Shared, **transport-agnostic** Copilot CLI session-liveness probe: scans
`~/.copilot/session-state` for `inuse.*.lock` marker files, checks each
marker's PID against `/proc/<pid>`, and backstops the result against a
`*copilot*`/`--acp` process/cmdline scan. This is a property of the Copilot
CLI's own session-state layout, not of any one transport, so the same probe
script and parser run identically whether the caller reaches the venue via
`docker exec` (agent-containers) or SSH (agent-codespaces).

```python
from session_liveness_probe import build_probe_script, parse_probe_output

# Sync transport (e.g. docker exec):
result = my_docker_exec([*prefix, "bash", "-c", build_probe_script()])
liveness = parse_probe_output(result.returncode, result.stdout, result.stderr)

# Async transport (e.g. SSH exec_with_retry):
result = await my_ssh_exec(build_probe_script())
liveness = parse_probe_output(result.returncode, result.stdout, result.stderr)
```

This lib owns only the shell script and the pure-Python output parser --
never the transport call itself, and it exports no `async def`. Each
consumer supplies its own sync-or-async transport around
`build_probe_script()`'s output and feeds the raw
`(returncode, stdout, stderr)` to `parse_probe_output()`; the parser itself
is always synchronous (pure string processing), so it composes with either
caller style with no `asyncio.run()` conflicts.

## Vendoring

This lib is **vendored per plugin** at
`plugins/<plugin>/libs/session-liveness-probe` (a marketplace-installed
plugin can only reference libs inside its own dir via
`[tool.uv.sources] agent-session-liveness-probe = { path = "libs/session-liveness-probe" }`).
Every copy's `src/` tree must stay **byte-identical** and declare the
**same version** — enforced by `tools/check-vendored-libs-sync.py`. A
source change to one copy MUST be propagated to all, with a version bump.
