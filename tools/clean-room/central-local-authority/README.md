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
Pass the candidate image and an existing private, timestamped scratch directory
outside the checkout to the host owner. It creates a fresh results directory,
records every resource name before launch and exclusively creates the inner
evidence directory.

```text
python tools/clean-room/central-local-authority/run.py --central-image <candidate-image> --results-root <private-scratch>
```

The 300-second pytest item and 360-second contained-case budgets include actual
startup, native CLI operations, outage observation and cleanup. Docker commands
retain a 45-second bound. The host reserves 30 seconds **inside** the same
360-second clock for owner unwinding and verified cleanup, reusing the canonical
contained-case owner and exact-name cleanup helpers. There is no resampling or
longer deadline on failure. The durable `owned-resources.json` includes the
network, central, peer and named UID-probe container. Host cleanup survives a
fatal contained-worker exit and attempts removal and authoritative absence
checks for every owned name; unavailable inventory is a failure, not absence.
Inspect the outer `cr-report.json`, `runner.log` and inner
`evidence/cr-report.json` separately. An exit-zero or skipped case without
complete owner-bound stage evidence cannot pass; primary and cleanup failures
remain separate jams. Direct opt-in invocation without this outer owner refuses
before any Docker operation. The default suite skips this opt-in case.

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
