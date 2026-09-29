# agent-session-liveness-probe

Shared, **transport-agnostic** Copilot CLI session-liveness probe: scans
`~/.copilot/session-state` for `inuse.*.lock` marker files, checks each
marker's PID against `/proc/<pid>`, and backstops the result against a
`*copilot*`/`--acp` process/cmdline scan. This is a property of the Copilot
CLI's own session-state layout, not of any one transport, so the same probe
script and parser run identically whether the caller reaches the venue via
`docker exec` (agent-containers) or SSH (agent-codespaces). **The script
requires Bash** (`set -o pipefail` is a Bash extension a plain POSIX
`/bin/sh` rejects) -- invoke it with `bash -c`, not a bare shell.

```python
from session_liveness_probe import build_probe_script, parse_probe_output

# Sync transport (e.g. docker exec):
result = my_docker_exec([*prefix, "bash", "-c", build_probe_script()])
liveness = parse_probe_output(result.returncode, result.stdout, result.stderr)

# Async transport (e.g. ssh-manager's exec_with_retry, which returns a
# CommandResult with an `exit_code` field, not `returncode`):
result = await exec_with_retry(
    manager, host, f"bash -c {shlex.quote(build_probe_script())}"
)
liveness = parse_probe_output(result.exit_code, result.stdout, result.stderr)
```

This lib owns only the shell script and the pure-Python output parser --
never the transport call itself, and it exports no `async def`. Each
consumer supplies its own sync-or-async transport around
`build_probe_script()`'s output and feeds the raw
`(returncode, stdout, stderr)` to `parse_probe_output()`; the parser itself
is always synchronous (pure string processing), so it composes with either
caller style with no `asyncio.run()` conflicts.

## Vendoring

**In dev**, every consumer's `pyproject.toml` references this library through
a `uv`-editable canonical pointer (`vendor-pointer-generalization` effort,
Phase 1) --
`agent-session-liveness-probe = { path = "../../libs/session-liveness-probe",
editable = true }` -- so every consumer resolves to this one source tree;
there is no per-plugin dev copy to keep in sync.

**At release**, `tools/materialize_main.py` rewrites that same pointer into a
real, promoted copy at `plugins/<plugin>/libs/session-liveness-probe/` for each
consumer -- non-editable, so a published plugin installs a self-contained
source tree with no cross-plugin `path` reference. `tools/sync-vendored-libs.py
--check` verifies every materialized copy's `src/` tree and version stay
byte-identical to this canonical one and to each other.
