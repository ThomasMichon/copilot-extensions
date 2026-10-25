# Last-reported root attention

`agent-worktrees session-attention <session-id> --json` reads one root agent's
existing `substatus.json` live-pulse register. It serves the
[Mux Companion](../../../visions/mux-companion/README.md)'s attention indicators
without exposing intent, question text, permissions, prompts or transcripts.
It does not scan other sessions, read event history, create a sidecar or cache,
start a service, repair state, or enable source-repository agent hosting.

## Version 1 contract

The invoked executable declares `contract: "agent-worktrees.session-attention"`,
`version: 1`, `supported_versions: [1]`, and capability
`last_reported_root_attention`. This is independent of its package version.
Readers accept additive JSON fields, validate the identity/version and required
fields, and preserve unknown evidence. Unknown CLI arguments fail with parser
status 2. Recognized invocations return 0 for an evidence envelope, including
unknown evidence; success is not a claim that the agent needs input.

This is a provider-first additive operation. Existing commands and their
semantics are unchanged. No old form is deprecated or retired. An optional
consumer probes its selected executable's read-only operation, not a newer
payload's package stamp. An old or rolled-back provider rejecting this command
means unsupported attention, not failure of the consumer's other features.
Invalid/missing metadata means unknown or unsupported; there is no equivalent
transcript, event-tail or legacy idle-boolean fallback. Independent upgrades
and provider rollback do not require synchronized installation.

The envelope contains:

- `session_id`: the selected lexical identifier, or null for invalid input.
  Paths, traversal, drive-relative identifiers and resolved sidecar/directory
  redirects are refused. Worktrees' existing Copilot home resolution applies.
- `availability`: `observed` or `unknown`. Only an unchanged readable JSON object
  with matching `sessionId`, a recognized explicit `rest` and a valid `restAt`
  is observed. Legacy coarse `idle` alone is insufficient.
- `rest`: `busy`, `idle`, `awaiting-operator`, or null when unknown.
  These are the producer's last-reported root states, **not a currently pending
  question or proof that no input is pending**. The producer ignores subagent
  requests. It writes only after a root intent exists; absence before that is
  legitimately unknown.
- `reported_at`: the producer's `restAt`, normalized to UTC, with
  `reported_at_source: "restAt"`. An initial `busy` report may have explicit
  null `restAt`; both fields are then null. A missing timestamp key or a
  null/non-timezone-aware timestamp for other states is unknown. `updatedAt`
  timestamps intent, not attention or a heartbeat, and is never substituted.
- `as_of`: the reader's UTC observation time, not producer freshness.
  `evidence_scope` is always `last_reported_root_state` and
  `runtime_continuity` is always `unknown`. A sidecar can survive a restart;
  old evidence is neither TTL-expired into idle nor promoted to live truth.
- `source.kind`: `copilot.substatus.json`; `max_bytes`: 65536.
  A read is capped at that limit plus one overflow-detection byte. Missing,
  oversized, non-regular, unreadable, partial/malformed, foreign-identity or
  observably changed/replaced sources produce unknown state. Filesystem checks
  are best-effort observations, not an atomic runtime snapshot.
- `issues`: sanitized reason-code counts only. Paths, exception strings and
  raw source fields are never included.

## Consumer and performance guidance

A consumer may nudge the operator to return to the CLI; it must not answer,
approve, dismiss or send input automatically. Keep the indicator visibly
last-reported and distinguish idle from awaiting-operator. Do not infer session
retirement or release from either state.

This operation reads one small file per call with bounded memory, independent
of retained transcript size. It adds no background polling on its own.
Consumers should query only the selected/current session while visible, off
the UI loop, with cancellation, stale-selection fencing and failure backoff.
Reuse applies only to the same selected session; a refresh/head change must
invalidate displayed evidence. No production latency or polling-rate
measurement is claimed here.

Focused validation:

```text
python tools/run-plugin-tests.py agent-worktrees --test-file tests/test_session_attention.py --test-file tests/test_session_attention_cli.py --test-file tests/test_session_observations_cli.py --test-file tests/test_lazy_dispatch.py
```

Fixtures exercise synthetic old/missing/malformed pulses, real fresh-home CLI
subprocesses, content omission, no writes and negative source-hosting policy.
No runtime deployment or live human-input/cutover acceptance follows from them.
