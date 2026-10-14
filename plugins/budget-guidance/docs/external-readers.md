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
shell execution, including case variants and trailing-space/period aliases.
Windows executable alternate streams are refused as well; select a trusted
interpreter explicitly when necessary.
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
contains ordinary static readings with runtime-only acquisition provenance,
so resolution remains pure and field
authority, contradictions, freshness, and arithmetic use the existing contract.
Direct resolution of an unacquired external reference is an explicit error.
Invocation errors are distinguished from invalid configuration in human output.
An explicit grant with missing configuration is an invocation error, rather than
an ordinary unconfigured posture that silently ignores the grant.
Grant mismatch diagnostics report unmatched/configured counts, not raw grant IDs:
a malformed invocation can put a path or credential into an identifier slot.

## Reader contract and bounds

The reader emits exactly one UTF-8 JSON object matching the versioned
[budget-reading contract](configuration.md), then exits successfully.
Its `source` must exactly match the configured source. The reader owns
`captured_at` and `freshness_seconds`; acquisition never substitutes the current
time for provider observation. Capture times later than acquisition or the
posture's evaluation instant are rejected, including direct library use of an
acquired config. Evaluation-time refusals preserve the reported timestamp and
expose `freshness: "future"` while withholding every field. An explicit `--at`
remains authoritative; the default evaluation instant is sampled after readers
finish. Inert manual/static readings retain their existing semantics.
Missing
fields remain missing, stale evidence remains stale, and explicit unavailable
or error readings retain that state without budget values.

Timeout is finite, positive, and at most 30 seconds (default 10); stdout is
bounded to at most 65536 bytes (default 65536). Reading and process completion
share a deadline beginning before process creation. Native process creation
itself is synchronous and not interruptible by this deadline; after it returns,
an already-expired deadline fails acquisition even if output and successful
completion are already queued. Cleanup has a
separate bounded wait. Reader stdin and stderr go to the null device; stderr floods
cannot fill a captured pipe, and diagnostic output never forwards reader
stdout, stderr, arguments, paths, or opaque reader error text.

Windows uses the canonical headless process helper and a required,
non-breakaway, kill-on-close Job before the executable can run. POSIX uses a
small isolated, no-site Python supervisor (`-I -S`) that atomically creates the
reader's owned process group with `posix_spawn(setpgroup=0)`, without leaving the
caller's session and while preserving contained-test supervision.
The host stays alive while its granted reader runs and after reporting that
reader's status. It observes completion with non-reaping `waitid(WNOWAIT)` and
retains the root until cleanup; its single-threaded, isolated process is the
only parent that can reap that root. An explicit control byte triggers the host
to terminate the retained reader group, reap the root, acknowledge cleanup over
its private pipe, and exit successfully. The supervisor stays outside that group.
The caller requires both the acknowledgment and successful host completion,
not a liveness check or an ordinary pipe close. The caller never signals a
numeric PID/group that an external waiter could have reaped and reused.
The host resets only its own `SIGCHLD` disposition for its private reader wait,
not the caller's signal policy.
Descendants are terminated after success as well as on timeout or output
overflow. The contract is for one-shot readers, never detached services.
POSIX platforms lacking the spawn, group, or non-reaping wait primitives report unsupported
execution, rather than silently falling back to unbounded spawning.
The host does not run ambient `.pth`/`sitecustomize` code or honor Python
startup overrides before containment. It preserves the original environment
for the explicitly authorized reader. Reader stdin/stderr remain null; the
host's separate stderr pipe carries only its bounded internal exit-status and
cleanup-acknowledgment protocol (at most 64 bytes per phase).
Caller termination closes the control pipe and triggers the same group teardown,
but EOF alone never produces an explicit cleanup acknowledgment.
Unexpected host exit or a missing acknowledgment refuses cleanup confirmation.

| Platform | Implementation and validation status |
|----------|--------------------------------------|
| Windows | Strict headless Job containment is implemented through the canonical helper. The native interactive-desktop lane passed on source `1b8da8b66`: two successful console-descendant cycles, timeout/tree cleanup, and five batch/stream grant cases (six passed, zero skipped); assertions and acquisition limits were unchanged. |
| Linux | The capability-gated group host is implemented; contained native regressions exercise timeout, output bounds, and descendant termination after root exit. |
| macOS | The same supervisor is conditional on `posix_spawn`, group signaling, and non-reaping `waitid(WNOWAIT)` support. Missing capabilities explicitly refuse reader execution; native macOS lifecycle validation is not yet recorded. No Linux `/proc` witness is claimed as macOS proof. |

The focused native Windows witness is opt-in and excluded from the ordinary
Linux/fast guard lane. From an interactive Windows desktop, set
`BUDGET_GUIDANCE_WINDOWS_NATIVE=1` for the test process and run the bounded
plugin runner with `-k native_windowless_parent_two_cycles_and_timeout_cleanup`.
It launches the real acquisition path from `pythonw.exe`, observes two console
reader/descendant cycles plus timeout, pins process creation identities, checks
window/foreground and Default Terminal acquisitions, and verifies every owned
root/descendant handle is signaled after cleanup. A noninteractive window station
is a failure when this lane is requested, not a headlessness pass.
The recorded native run used an interactive Windows Session 1 with
`WSF_VISIBLE=true` and verified supervisor cleanup. Earlier source snapshots
failed the first acquisition; their exact cause remains unproven, and the
successful current-source witness is not a claim about those earlier failures.

Empty/malformed/duplicate-key JSON or unrepresentable Decimal exponents, nonzero exit, oversized output, timeout,
source mismatch, future capture, launch/containment failure, and unconfirmed
cleanup produce an explicit error reading with no numeric values. Missing
authorization produces an unavailable reading. Neither becomes zero usage.
Reader-reported error text is reduced to a safe acquisition diagnostic.
When acquisition and cleanup both fail, the safe primary diagnostic is retained
alongside the cleanup failure; unconfirmed cleanup never becomes success.

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
