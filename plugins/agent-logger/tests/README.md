# Logger command-entry qualification

`test_managed_command_gate.py` runs the real six payload commands, runtime
resolver and prepared-UV selector against inert modules in a fixture-owned venv.
No session archive, transcript, sync target, network service or credential is used.

Prepare UV and managed CPython 3.10.20 through explicit setup, then run:

```text
tools/run-tests payload-invocation --test-file tests/test_logger_ready_source.py --allow-explicit-tiers -k bash
```

Select `powershell` or `pwsh` for native Windows hosts. Missing prerequisites
skip locally and are not qualification. The path-gated native workflow requires
all 24 cases per addressed shell without skips on Linux, macOS and both Windows
hosts. Windows argument transport explicitly exercises Legacy native passing.

Raw/materialized pairs cover every allowed command/module pair, last-known-good
and newest-complete fallback, a namespaced root with spaces/quotes/Unicode and
missing UV, provider or capture support. Successful cases run twice with exact
application selection, poisoned ambient commands/selectors, argument/stdio/cwd,
numeric exit status and scoped installation/timer/task environment assertions.
A mismatched command/module pair refuses before context evaluation or UV.
The native context evaluator stays independent of Python bootstrap selection;
each successful launch performs one prepared-only application UV lookup.
Payload/runtime bytes and inherited Bash application/caller descriptors survive.
Missing support refuses before application entry or installation.

This packet qualifies foreground command admission, not full project
environment selection, durable UV distribution, first-use provisioning,
scheduled sync or installed-host continuity. The existing native evaluator,
policy/provenance checks and first-use driver retain their contracts.
