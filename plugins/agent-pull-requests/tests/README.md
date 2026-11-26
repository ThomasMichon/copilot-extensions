# Pull Requests command-entry controls

Parent: [plugin documentation](../README.md).
Runner and isolation contract: [repository testing guide](../../../TESTING.md).

`test_managed_command_gate.py` runs actual raw/materialized Bash and PowerShell
dispatch with real prepared UV, an inert CPython 3.10.20 application and an
independently declared 3.11.18 bootstrap. Prepare those prerequisites explicitly;
the tests never acquire them, and missing preparation skips rather than passes.
Run the source-only packet from the repository root:

```sh
tools/run-tests payload-invocation --test-file tests/test_pull_requests_ready_source.py --allow-explicit-tiers
```

The path-gated native workflow requires all twenty-four cases/reports per addressed
shell on Ubuntu, macOS and Windows within eight-minute job limits. Positive
current/LKG/newest/cell paths run twice and retain exact UV work, argv, streams,
exit status and slot/payload bytes. Native PowerShell variables include numbers
and booleans. Missing UV/provider/capture/bootstrap/application support and
selection changes during admission refuse before application entry. Long-body
controls retain 25,000 characters, including non-BMP Unicode, without Base64
command-line expansion or consuming stdin. Temporary argument environment values
are removed before application entry and the caller's prior values are restored.
Inert applications are precompiled before snapshots; operational isolation and
bytecode flags remain unchanged. Materialized controls use payload-embedded
process support and packaged capture, not a canonical-checkout import.

These controls isolate governance/selection decisions, not OS-account identity.
`test_cell_invocation.py` separately exercises canonical receipts, complete
selection, cold refusal and post-admission revalidation. Its ordinary regression
subset can run in the hardened network-disconnected devcontainer:

```sh
tools/run-python tools/run_tests_in_devcontainer.py agent-pull-requests --test-file tests/test_cell_invocation.py --allow-explicit-tiers -k 'not normal_init_and_generated_command'
```

Unavailable native-account/platform cases remain skips. The explicitly omitted
full provisioning/network case is not qualified by that subset. Neither packet
establishes real PR/backend, broker, network, installed-host adoption or complete
command-latency acceptance.
