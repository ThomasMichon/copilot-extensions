# Explicit retained-good client recovery

`fleet-manager client-state recover-retained` and
`fleet_manager.client_recovery.recover_client_release` compose the existing
owned selector and command publishers. The canonical installation lease spans
both transitions, including health and acknowledgement. No older release is
admitted again, no candidate is built, and no admission/selection bytes change.
Default planner, inspection and doctor commands remain dependency-free.

The combined transition runs installed dependency, version and doctor health
once under that uninterrupted custody span. Its private in-memory handoff binds
the result to the exact custody object, acknowledged active publication,
candidate bytes, admitted receipt, compatibility target and recovery decision.
Command registration still freshly verifies those inputs and actual candidate
bytes and executes every platform command probe. Standalone activation and
registration always perform their own fresh installed-health pass; no persisted
health receipt or caller-supplied skip flag authorizes this reuse.

This operation requires an explicitly selected, still-retained completed client,
its original selected release, immutable release evidence authenticated under
current trust, and explicit supported current API/data/config compatibility.
The existing `release-verification` and construction/health toolchain
prerequisites apply. It does not select a policy, sign releases, restore user
data, migrate schemas, change PATH, acquire a provider resource or grant a
remote/live rollout.

## Decision and current inputs

Supply all the existing activation inputs (`--root`, `--identity`, `--selection`,
`--release`, `--proof`, `--policy`, `--sources`, `--staging-parent`, `--target`),
plus `--command-directory` and required `--recovery-decision`. All JSON arguments
are explicit local files, not inline credentials or authority inferred from a
directory scan. CLI file suppliers are reread at execution/publication
checkpoints. API callers must provide fresh trusted `current_recovery`,
`current_policy` and `current_target` suppliers; the API does not authenticate
their source.
The supplied command directory itself must be absolute and exactly match the
decision. Relative input is refused before installation custody, health or
selector effects rather than resolving it in only one phase.
Recovery also validates the complete generated command family before health or
selector publication, including when no command registration exists. Unsupported
control characters in command or runtime paths cannot become a later,
predictable registration failure after selectors change.

The exact `fleet-manager.client-recovery-decision`, version 1, has these fields:

| Field | Binding |
|-------|---------|
| `identity` | Exact initialized client installation identity |
| `owner` | That installation's `selection_owner` |
| `decision`, `revision` | Explicit local decision identifier and positive revision |
| `admitted_digest` | Canonical digest of the complete current higher admission receipt |
| `expected_active` | Exact currently acknowledged active receipt, or `null` for settled withdrawal |
| `expected_withdrawal` | `null` when expecting active state; otherwise the digest of the exact settled withdrawal |
| `retained_active` | Exact historical acknowledged healthy activation receipt for the retained candidate |
| `completion_digest` | Canonical digest of that candidate's exact construction completion |
| `target_digest` | Canonical digest of the current explicit compatible target |
| `command_directory` | Exact absolute existing private command directory |

The decision's canonical content digest is returned with the outcome.
An absent, changed or revoked decision refuses; a new revision is not an
implicit update to a prior operation. A different floor, active publication or
withdrawal generation fences stale replay. Merely observing absent selectors
does not establish settled withdrawal.

## Proof and ownership

Normal activation retains `acknowledged.json` in its existing
`client-activations/<publication>` history after current installed health,
complete producer proofs and selector acknowledgement. Recovery requires that
exact historical acknowledgement, original completion/selection and complete
historical producer receipts. It also verifies the retained candidate's actual
current bytes. Missing legacy acknowledgement is not synthesized from a
`last-known-good` filename or an old incomplete receipt. A fresh ordinary
activation of an *already current, exactly admitted* client can acknowledge its
current proven state after real health; this is not authority to recover an
unacknowledged older candidate.

Selector withdrawal retains its settled outcome in its existing deactivation
history and `client-withdrawn.json`. A later withdrawal has a different token.
These records extend the existing lifecycle history; there is no second
operation ledger or signer.

Historical receipts prove provenance, not ownership of a current replacement.
Recovery still reacquires current installation, candidate and publication
custody, preflights the current command family, and refuses same-byte foreign
selectors/commands or unsettled writers before retained execution. Fresh
compatibility and trust checks precede health and publication. The operation
preserves user configuration/data rather than transforming it to fit older
code.

The new selector history stores its exact decision in `retained-recovery.json`.
`register-commands --recovery-decision <file>` uses the **same** decision/floor/
target guard, including exact reruns. Registration without that current
decision cannot use a recovered lower client. Ordinary lower admission,
construction and activation remain refused.

## Failure, retry and reporting

The publishers remain non-atomic across multiple physical targets. Complete
history/context and the framed producer header precede pending intent and
effects. An interrupted selector publication retries only its exact unchanged
decision with the existing sixteen-attempt bound. Unknown or malformed proofs
remain blocking; recovery does not repair missing legacy framing.

If selectors succeed but command registration does not settle, the error says
so rather than claiming a complete recovery or rolling back silently. Preserve
the evidence. When a command intent is pending, first use the existing explicit,
execution-free `unregister-commands` for its exact directory; its physical
producer proofs, not recovery history, authorize withdrawal. Then retry the
same unchanged decision. New command attempts are bounded to sixteen within
that selector's recovery history, including attempts after command withdrawal.
Exact completed reruns do not consume an attempt.
Attempt markers are published atomically from unique staging files before
command effects. An interrupted staging write leaves its original bytes as
evidence but cannot poison the next same-decision retry or consume a published
attempt. A malformed committed marker still refuses; recovery does not repair
or adopt it.

A successful `fleet-manager.client-retained-recovery` result reports the
unchanged complete `admitted` receipt separately from `recovered_active`, plus
the decision digest, actual command family and fresh installed health.
`admission_modified`, `constructed`, `path_modified` and
`live_readiness_verified` are false. A completed source change is not evidence
of a live rollback, controller/node deployment, installation retirement or a
complete managed installer.
