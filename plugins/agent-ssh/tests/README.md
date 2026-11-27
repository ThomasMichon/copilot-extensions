# SSH command-entry qualification

`test_managed_command_gate.py` runs the real payload command, runtime resolver
and prepared-UV selector against an inert application in a fixture-owned venv.
No SSH client, listener, transport, service, credential or installed configuration
is used.

Prepare UV and managed CPython 3.10.20 through explicit setup, then run:

```text
tools/run-tests payload-invocation --test-file tests/test_ssh_ready_source.py --allow-explicit-tiers -k bash
```

Select `powershell` or `pwsh` for native Windows hosts. Missing prerequisites
skip locally and are not qualification. The path-gated native workflow requires
all 14 cases per addressed shell without skips on Linux, macOS and both Windows
hosts. Windows argument transport explicitly exercises Legacy native passing.

Each raw/materialized pair covers current, last-known-good and newest complete
slots, a namespaced root with spaces/quotes/Unicode, and missing UV, provider or
capture support. Successful cases run twice with exact application selection,
poisoned ambient commands/selectors, argument/stdin/stdout/stderr/cwd/root/context
and numeric-exit assertions. The native context evaluator still executes without
a Python bootstrap selection: each successful command performs one application
UV lookup. Missing support refuses before the application or installer runs.
Payload/runtime snapshots remain unchanged, and Bash's inherited application
and caller descriptors survive admission.

This packet qualifies foreground command admission, not full project
environment selection, durable UV distribution, readiness import isolation,
first-use provisioning or installed-host continuity. The existing first-use
driver and pinned/custom-root/nested-owner refusals retain their contracts.

## POSIX bootstrap metadata

```text
tools/run-tests payload-invocation --test-file tests/test_ssh_metadata_source.py --allow-explicit-tiers
```

The backend-free metadata packet reads raw and materialized hooks using poisoned
Python/UV commands and an inert driver. It distinguishes invoked-reader ownership
from the manifest-selected payload, exercises current markers, cold/stale actions,
holds, invalid documents, decoded newlines and atomic manifest replacement.
Each repeated current check performs two native field queries and no interpreter
or driver launch. The existing native workflow requires all 56 metadata controls
on Ubuntu and macOS, with 22 retained handoff/routing controls additionally on
Linux, without skips. Actual driver submission remains Linux/Bash-qualified;
macOS retains its existing refusal. These are isolated source controls, not
SSH connectivity, installed rollout or complete hook-latency qualification.
