# Command error evidence contract

Parent: [Discoverable Command Errors](README.md). Tracking: #14336 under #8377.
This is the implementation/acceptance contract. The parent records delivered
slices and remaining coverage; this document is not a universal implementation
claim or authorization to activate logging on a shared service.

## What must remain after the terminal disappears

Each owned binstub boundary must retain enough local evidence to answer:

1. Which command/component ran, when, and through which selected implementation?
2. Which boundary failed: entry/bootstrap, argument parsing, application,
   delegated/native command, or completion observation?
3. What was the actual sanitized error, and what primary result was observed?
4. Which related owner record contains deeper evidence, if it exists?
5. Is the capture complete, truncated, unavailable or of unknown completion?

A bare `engine-error`, an exit code without the available inner cause, a
zero-returned wrapper or an undated warning is not sufficient causal evidence.
Do not fabricate an inner cause that the owner never observed.

Use the existing installation/configuration and diagnostic location owners.
Standalone command use must not require a collector, daemon, Gateway, working
application environment or access to the prior agent's session. A newer
payload must not require a new argument on an older receiving command merely
to enable its own diagnostics.

## Coverage

| Boundary | Required evidence |
|----------|-------------------|
| Native entry and bootstrap | Resolution/selection errors before the application interpreter or parser; a useful native fallback when application logging cannot initialize |
| Parser | Invalid syntax/value diagnostics and observed result, without private argument or prompt disclosure |
| Application | Caught error reports, uncaught failures and error-level structured events, including errors followed by exit zero |
| Native/delegated invocation | Actual available child error and result through its existing process owner, retaining stream/cancellation/lifetime semantics |
| Interactive or process-replacing entry | Producer-owned diagnostics without replacing the terminal with a pipe or implying that handoff means backend success |
| Interrupted or incomplete observation | Explicit unknown/incomplete evidence; no synthesized success, inferred error cause or automatic replay |
| Nested command | Link the existing diagnostic context without persisting command-line payloads or creating a second operation authority |

Coverage starts when an owned entry can execute. A platform refusing to execute
the first instruction, a power loss or unavailable storage cannot be turned into
a durable-success claim. Document that boundary and expose missing/degraded
coverage rather than silently treating it as an error-free invocation.

## Storage, privacy and discovery

- Keep command evidence local and private by default. No automatic upload or
  optional telemetry sink is necessary for basic diagnostic retrieval.
- Retain UTC timestamps, fixed command identity, observed phase/result,
  bounded diagnostic context and existing correlation/source facts.
- Never persist stdin, full argv, private prompt fields, credentials, whole
  environments, exception locals or arbitrary structured-request bodies.
  Compose existing redaction and privacy shaping before persistence.
- Preserve a useful cause without claiming that an arbitrary raw stderr
  capture is inherently safe. Streams that can contain sensitive input need
  their actual producer's shaping, not a second unreviewed archive.
- Keep evidence append/concurrency safe and bounded. Truncation, abandoned
  records, interrupted writes and storage errors are visible limitations.
- Reuse #8377's elapsed diagnostic retention and #8370's protected installer
  custody. An incomplete operational request is not a disposable log file.
- Read-only discovery identifies recent command errors, selected and fallback
  paths, historical segments, source identity and unavailable coverage.
  Missing storage stays missing; inspection must not provision or repair it.
- Test discovery through a fresh process/session that has no cached path from
  the failing invocation. Manual console scraping is not the retrieval API.

## Compatibility and cost

Keep protocol stdout byte-for-byte unchanged. Do not mix path announcements or
diagnostic envelopes into JSON, IPC, binary output or another command's stdin.
Existing human stderr remains useful; diagnostic lookup must not depend on
adding another message to a machine-readable protocol.

Keep interactive TTY detection, inherited/binary stdin, progressive output,
Unicode and argument fidelity. Do not replace every `exec` with a generic
capturing supervisor, serialize stdout/stderr draining, await descendant-held
pipe EOF indefinitely, detach bounded children, or weaken Windows Job custody.
Extend the process/entry owner that already knows whether it is an ordinary
command, interactive handoff or independent daemon.

Do not download a prerequisite, start a daemon, scan all historical logs or
spawn an extra full interpreter merely to log an ordinary command. Measure
added cold/warm invocation work and emission cost using the existing native
qualification lanes. Bound retained context and reader work, not the command's
legitimate execution time.

An exiting standalone command whose runtime cannot meet the native storage
contract in-process may use the performance pattern's
[bounded error-only persistence exception](../../../docs/patterns/performance-measurement.md#standalone-command-error-boundary).
Its caller qualification is required separately from provider qualification;
the exception never adds a successful-command helper or a recurring reporter.

Logging initialization or persistence failure must preserve the primary command
result and expose degraded evidence through an appropriate existing stderr or
structured owner status. It must not turn a successful effect into a
retry-shaped command failure or hide an original nonzero result.

## Acceptance

The finite source inventory must map every entry family to its implementation
owner and native tests. Generator conformance protects against a future
uncovered command being added. Prove ordinary and early failure, zero-exit
error reporting, nested calls, private data exclusion, failed logging,
concurrent writes, fresh-session retrieval, streaming and process lifetime.
Keep source tests, released artifacts and authorized installed proof separate.
