# Optional consumer telemetry command

This is opt-in **source support**, not proof of release or installation.
Finite `agent-worktrees` CLI runs use the shared
[consumer telemetry command v1](../../../docs/patterns/consumer-telemetry-command.md)
finite-owner admission and the closed `worktrees.lifecycle.v1` projection;
its `sessionStart` hook journals `hook.lifecycle.v1` records for the same route.
It does not install a receiver, observability SDK or service.

## Admission

The machine owner may place `consumer-telemetry.json` in the registry/install
directory (normally `~/.agent-worktrees`):

```json
{"schema_version": 1, "root": "/absolute/approved/harness"}
```

Use a native absolute path on Windows. The approved root supplies the shared
`.agent-telemetry/config.json` command declaration and optional gated
`config.local.json`; their parser, precedence and local disable are unchanged.
The shared contract owns the policy grammar and the refusal semantics.

Without the file the run is unchanged: an observed verb reads only that one
path and starts no child. Unobserved verbs (status, listing, read-only lease
subcommands, `--help`) and preview modes that publish or land nothing
(`create-pr --dry-run`, `push-changes --dry-run` / `--title-only`,
`pr-merge --dry-run`, `finalize --dry-run` / `--explain-landing`, their
namespace aliases, and any unambiguous abbreviation the verb's parser accepts)
never look it up. A present invalid file refuses export
for the run; it never borrows another route. No CLI argument, environment
variable, repository or current directory selects a root or a command.

## What is observed

Each observed invocation offers exactly **one** record after its product work
returns, then drains within the contract's bounded shutdown budget. Receiver
absence, failure or refusal never changes the command's output or exit status.

| Invocation | Record |
|------------|--------|
| `create` | `kind: stage`, `stage: worktree_create` |
| `bind-session` | `kind: stage`, `stage: session_bind` |
| `lease acquire` / `borrow` / `renew` / `release` | `kind: stage`, `stage: lease` |
| `create-pr` / `pr-create` | `kind: pr`, `action: create` |
| `push-changes` | `kind: pr`, `action: push` |
| `pr-publication-labels` | `kind: pr`, `action: label` |
| `pr-merge` | `kind: pr`, `action: merge` |
| `finalize` / `finalize --abandon` | `kind: pr`, `action: merge` / `abandon` |

Namespace aliases that reach the same handlers record the same way:
`worktrees <verb>`, `worktree create` / `push` / `create-pr` / `finalize`,
`pr create` and `pr merge`. As in the CLI, the first project option
(`-p`, `--project`, `--project=`) is lifted from any position. Lease-global
options (`--origin`, `--pretty`) are skipped before the subcommand is read;
`inspect`, `status` and `list` are never observed.

Outcome is `success` for exit 0 and `failure` otherwise. Reasons:
`interrupted` (Ctrl-C or exit 130), `lease_conflict` (the lease verb's
documented conflict exit 3), and `other` for any other failure. Stage records
carry `attempt: 1` and `duration_ms` from a monotonic clock.

PR records add `label_result` when the run's publication-label reconciliation
reported one: `applied`, `already_present`, `repaired` (explicit label repair
that added the label), `not_requested`, `unsupported` or `failed`. A failed
label action reports `unavailable` for an unsupported provider and
`provider_rejected` for any other label failure.

Records carry only those fixed enums and bounded integers plus the envelope's
opaque source/route identities. Worktree, project, branch and repository
names, PR numbers, URLs, paths, command lines and error text are never offered.

## sessionStart hook records

The plugin's `sessionStart` hook never launches a receiver. When the same
`consumer-telemetry.json` admission file exists, the hook writes one
content-free start marker before its product work into
`consumer-telemetry-hooks/` beside it, through the shared
`process_telemetry.hook_journal`, and replaces it with a completion record
when it returns: `success`, or `failure` with `failure_category: exception`
when an exception escaped. A run the host stops at its hook timeout leaves its
marker, which a later start reports as `unfinished` /
`unfinished_prior_run`. Records carry only the fixed plugin and entry
(`agent-worktrees`, `sessionStart`), the outcome and category, the duration,
a local run sequence and the start time. The hook loads the library shipped with its payload, or the
selected runtime's installed copy; without either, or without admission, it
writes nothing.

After each observed finite command offers its own record, it drains the
pending hook records and offers them as `hook.lifecycle.v1` through a
separately admitted route. Admission is checked before draining, so a refused
route leaves them pending; a drained record whose offer fails is lost rather
than duplicated.

## Coverage inventory

| Registered value | Status |
|------------------|--------|
| `worktree_create`, `session_bind`, `lease` stages | Emitted |
| PR actions `create`, `push`, `label`, `merge`, `abandon` | Emitted |
| `account_resolution` stage, `snapshot_timeout`/`account_unresolved` | Registered, not yet emitted |
| `claim` stage, `claim_busy`/`force_claimed` | Registered, not yet emitted |
| PR reasons other than `none`/`interrupted`/`other`/label mappings | Registered, not yet emitted |
| `hook.lifecycle.v1` `agent-worktrees` sessionStart: `success`, `failure`/`exception`, `unfinished`/`unfinished_prior_run` | Emitted |
| `hook.lifecycle.v1` `timeout`, `nonzero_exit`, `unavailable_runtime`, `other` categories | Registered, not yet emitted |
| `hook.lifecycle.v1` hooks of other plugins | Registered, not yet emitted |

Registration alone is not adoption; later changes refine reasons at their
owning call sites.

## Support discovery

The selected executing package carries
`agent_worktrees/metadata/consumer-telemetry-contract.json`, advertising
`consumer-telemetry-command` version 1 over `command-jsonl-v1` with **only**
`worktrees.lifecycle.v1` and `hook.lifecycle.v1`. Missing or older metadata
means unknown support. Admit those exact schemas in the receiver before
configuring the route. Older
runtimes do not read the admission file; withdraw it before selecting one.

`tests/test_consumer_telemetry_command.py` drives the instrumented entry
through the real finite owner and a real receiver subprocess, and checks
legacy inertness, unobserved verbs, label mapping and receiver failure.
`tests/test_hook_telemetry.py` runs the hook journal begin/finish and drains
it through the same receiver.
