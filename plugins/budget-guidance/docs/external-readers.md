# Operator-authorized external readers

An `external-command` adapter references a reader without granting execution:

```json
{
  "type": "external-command",
  "id": "usage-report",
  "authority": 10,
  "source": "usage-report-reader"
}
```

Place this entry in the configuration's `adapters` array. It accepts only
`type`, `id`, `authority`, and `source`: repository data cannot supply an
executable, arguments, working directory, environment, credentials, or limits.
Without a matching invocation-owned grant, it resolves as **unavailable**, with
an explicit authorization diagnostic and no budget fields.

## Explicit invocation

```bash
budget-guidance status --config ./budget.json --json \
  --reader-id usage-report --reader-cwd /absolute/operator/directory \
  --reader-timeout 10 --reader-output-limit 65536 \
  --reader-command /absolute/operator/reader --format budget-reading
```

All budget-guidance options must precede `--reader-command`; subsequent tokens
are literal reader arguments. The executable and working directory must be
absolute. Batch executables (`.cmd` and `.bat`) are rejected to avoid implicit
shell execution; select a trusted interpreter explicitly when necessary.
The CLI authorizes exactly one adapter. There is no environment-variable,
configuration-file, or session-start grant path.
Explicit reader options require the complete grant even when they equal their
defaults. The POSIX host also refuses an empty, relative, missing, or
non-executable `sys.executable`; it never searches PATH for an interpreter.

Library callers may pass multiple grants to
`external_reader.acquire_config(config, readers)`:

```python
from pathlib import Path
from budget_guidance.external_reader import CommandReader, acquire_config

readers = {
    "usage-report": CommandReader(
        argv=("/absolute/operator/reader", "--format", "budget-reading"),
        cwd=Path("/absolute/operator/directory"),
        timeout_seconds=10,
        max_output_bytes=65536,
    ),
}
acquired = acquire_config(config, readers)
# Pass acquired to the ordinary build_posture(acquired, evaluated_at).
```

Every grant must match a configured external adapter ID. Each adapter executes
once per acquisition, not once per field or presentation. The resulting config
contains ordinary static readings, so resolution remains pure and field
authority, contradictions, freshness, and arithmetic use the existing contract.
Direct resolution of an unacquired external reference is an explicit error.
Invocation errors are distinguished from invalid configuration in human output.
Grant mismatch diagnostics report unmatched/configured counts, not raw grant IDs:
a malformed invocation can put a path or credential into an identifier slot.

## Reader contract and bounds

The reader emits exactly one UTF-8 JSON object matching the versioned
[budget-reading contract](configuration.md), then exits successfully.
Its `source` must exactly match the configured source. The reader owns
`captured_at` and `freshness_seconds`; acquisition never substitutes the current
time for provider observation. Future capture times are rejected. Missing
fields remain missing, stale evidence remains stale, and explicit unavailable
or error readings retain that state without budget values.

Timeout is finite, positive, and at most 30 seconds (default 10); stdout is
bounded to at most 65536 bytes (default 65536). Reading and process completion
share a deadline beginning before process creation. Native process creation
itself is synchronous and not interruptible by this deadline; after it returns,
an already-expired deadline immediately fails acquisition. Cleanup has a
separate bounded wait. Stdin and stderr go to the null device; stderr floods
cannot fill a captured pipe, and diagnostic output never forwards reader
stdout, stderr, arguments, paths, or opaque reader error text.

Windows uses the canonical headless process helper and a required,
non-breakaway, kill-on-close Job before the executable can run. POSIX uses a
small Python host that establishes an owned process group without leaving the
caller's session, preserving contained-test supervision. It retains the root
PID until group cleanup to avoid signaling a reused process identity.
Descendants are terminated after success as well as on timeout or output
overflow. The contract is for one-shot readers, never detached services.
POSIX platforms lacking `setpgid` or non-reaping `waitid` report unsupported
execution, rather than silently falling back to unbounded spawning.

Empty/malformed/duplicate-key JSON, nonzero exit, oversized output, timeout,
source mismatch, future capture, launch/containment failure, and unconfirmed
cleanup produce an explicit error reading with no numeric values. Missing
authorization produces an unavailable reading. Neither becomes zero usage.
Reader-reported error text is reduced to a safe acquisition diagnostic.

## Trust and credentials

A grant authorizes executing the exact command and cleaning up its descendants.
The reader is **trusted operator code, not sandboxed code**. It inherits the
invoking process's environment and OS permissions and can use its own existing
credential store or approved credential broker. The plugin does not fetch,
refresh, export, override, or broaden those credentials. Do not pass secrets as
command-line arguments. Do not grant a repository-selected interpreter or
script merely because its path is absolute. A grant is request-scoped; it must
not be persisted as ambient permission over arbitrary repository configs.

This seam permits private account readers to remain outside the public plugin.
It neither implements a provider refresh API nor makes a reader's claims
authoritative account-identity proof. Those obligations remain with the reader
and its caller. It grants no model eligibility, worker control, automatic sleep,
or operator-work blocking authority.

See [Configuration](configuration.md), [Architecture](architecture.md), and
the [plugin overview](../README.md).
