# Watchdog exit observation and repeated tombstone work are distinct

Canonical contract: [README.md](README.md).

## Qualification and outcome

Preserved #6880/#8862 through three whole original/comment texts, 7,240
characters, after unchanged complete metadata/editor/history, collaborator
roster and protected-reference checks. Five distinct whole messages supply six
ranked comparisons; none establishes completion of either finite outcome.

## #6880 - Root watchdog exit must not wait for captured-output EOF

The original POSIX defect is the ordering of owned installer exit 124,
captured output draining and descendant cleanup. A logger enables the real
consumer's captured pipe; a descendant retaining it may publish and exit before
the outer deadline, so `communicate` returns only after that publication.
The reported synthetic subprocess check was not a production installer run.

Retain independent owned-root exit observation and prompt sweep of the remaining
installer session BEFORE descendant publication, rather than relying on EOF
or the outer timeout. Existing process-identity/session/pidfd and legitimate
detached-daemon distinctions are useful cleanup mechanisms, not proof the
trigger is timely.

The exact regression supplies a logger, lets an owned descendant inherit the
captured stream and attempt delayed publication before the outer deadline,
then requires prompt watchdog failure, dead owned descendant and no marker/
slot. Output draining must not postpone cleanup. No runtime rollout, full-machine
stress, new installer execution or process termination is authorized here.

## #8862 - Avoid selecting physically reaped history that cannot advance

Retain the original owner's bounded fix for automatic finished-session sweep
amplification. Already-reaped terminal records with a paired tombstone that
cannot advance retirement should not repeatedly trigger physical cleanup,
ownership/configuration checks, branch deletion and retirement on every exit.
Synchronous post-exit waiting makes that repeated selection user-visible.

When retirement CAN advance, preserve exact reciprocal/locked checks, liveness
and obligations. Never cache cleanup authorization across records. Retain
metadata, explicit cleanup semantics, deterministic work-count and lifecycle
regressions. Bounded individual Git observation is separate from selecting
already-processed history repeatedly; neither age nor a tombstone grants
deletion or custody release.

## Completed-work comparisons

| Original | Whole candidate | Contract comparison |
|---|---|---|
| #6880 | `a1e629c7d884cb1af9f0138fd330e3c20a6f718e` | A dormant Bash seed-reply reader's EOF/error controls are not prompt watchdog-root observation before inherited captured-output EOF. |
| #6880 | `93601f8619aacfa9fe44f2d0cc5728806dff3f2a` | Deferred source-progress streaming preserves capture/owner boundaries but does not establish the requested exit-124 cleanup trigger and no-publication regression. |
| #6880 | `236950f312c23e7afdd4d5dde062311e489c6a34` | A dormant POSIX exec boundary and finite seed-custody controls are not the actual logger-enabled installer watchdog consumer. |
| #8862 | `7b0d58030f64d89622b1482a362a19b3a9eddb58` | Dispatch decline closeout deliberately separates finalized checkout, logical assignment and native retirement; it does not prove bounded automatic tombstone sweep selection. |
| #8862 | `3b7a166351ddb59a2ebe8ec8d4cc4d4bd81d2f61` | Instruction copying at lifecycle boundaries is not avoidance of repeated physical cleanup for already-reaped records. |
| #8862 | `a1e629c7d884cb1af9f0138fd330e3c20a6f718e` | Literal Bash seed reading does not implement retained-tombstone work-count/lifecycle selection. |

Both originals remain OPEN. No product import/test, installer, process cleanup,
checkout deletion or source/resource custody changed. See the
[dated journal](<journal/2026/11.04 exit-observation-history-work-contracts.md>).
