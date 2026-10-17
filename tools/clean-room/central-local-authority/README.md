# Central/local authority -- host-driven Tier-P acceptance

This F1 lane uses the canonical contained test runner, not `-Scenario`.
The host owns only bounded Docker control commands. Both services, their
independent databases, the actual federation runner and every task CLI process
run in separate fresh Linux containers. There are no host port publications,
socket mounts, privileged containers or cloud logins.

Prepare the candidate `agent-dispatch` test runtime with
`python tools/run-plugin-tests.py agent-dispatch --prepare-only`.
Build the candidate `services/dispatch/Dockerfile` from the repository root,
using the environment's approved `PYTHON_INDEX_URL` build argument when needed.
Set `AGENT_DISPATCH_CENTRAL_AUTHORITY_E2E=1` and
`AGENT_DISPATCH_E2E_CENTRAL_IMAGE` to the candidate image. Optionally set
`AGENT_DISPATCH_AUTHORITY_E2E_EVIDENCE_DIR` to a **nonexistent absolute path**
under an existing private, timestamped scratch directory outside the checkout.
The fixture exclusively creates that evidence path; do not precreate it.

```text
python tools/run-plugin-tests.py agent-dispatch --test-file tests/test_central_authority_docker.py --timeout 360 --plugin-timeout 360
```

The 300-second pytest item and 360-second contained-case budgets include actual
startup, native CLI operations, outage observation and cleanup. Docker commands
retain a 45-second bound. There is no resampling or longer deadline on failure.
Without an evidence override, the canonical runner owns temporary artifacts;
with one, inspect `cr-report.json` for stage results, classified jams and cleanup
status, alongside the runner's normal failure output. The default suite skips
this opt-in case.

The proof must observe a real authenticated `peer` registration, reject a
lease-eligible role and bad central credentials, and create independent central
and explicitly local-only tasks through the native CLI. Reads and claims must
not cross databases. Declared qualified claim-provider callbacks must retain
their target even when the caller's default authority is reversed; old
unqualified callbacks fail unavailable in explicit mode rather than guessing.
Disconnecting the central container from its private
network leaves its actual database and owner intact while making both reads
and completion fail at transport.
The local task must remain usable and unchanged, without a local copy of the
central task. After reconnect, the same central owner/generation must complete.
Cleanup removes only generated owned names and verifies absence.

The fixture partitions the network rather than pausing the server: a paused
process can still receive a buffered TCP request and commit it after resuming,
even when the client has already reported a timeout. Transport failure is not
proof that a write was unaccepted. Reconcile that result from the central
authority; never replay it against a local database.

This is authority/federation acceptance, not a model evaluation, a replicated
offline queue, an ownership-transfer implementation, automatic pool placement,
or a production rollout.
