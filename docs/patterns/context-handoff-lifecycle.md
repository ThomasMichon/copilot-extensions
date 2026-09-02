# Context handoff lifecycle

## Problem

A context handoff is not just a prompt copy. It transfers an active objective
between Copilot sessions while preserving the target checkout, authoritative
worktree head, terminal title, retry state, and the predecessor until the
successor has proved it can recover the baton.

Naive implementations fail in several ways:

- environment pane variables do not reliably identify the owning mux;
- a successor session does not exist until its first prompt is submitted;
- embedding the continuation in terminal or command argv loses formatting and
  creates quoting and length hazards;
- moving the head or terminating the predecessor at launch strands a failed
  successor;
- a numeric PID may identify a reused, unrelated process;
- ambient `PATH`, project wrappers, shell interpolation, Python import paths,
  or locale encodings can redirect or corrupt cross-plugin calls;
- treating a knowledge repository or active effort as mandatory blocks an
  otherwise self-contained continuity mechanism.

## Standard architecture

`context-handoff` owns orchestration. `agent-worktrees` owns ground-layer
session, process, mux, and worktree lifecycle primitives. `agent-dispatch`
optionally supplies durable task storage. Efforts optionally supply durable
objective context.

The normal sequence is:

1. Resolve the predecessor by exact Copilot session id. Process ancestry yields
   the Copilot PID, creation identity, mux pane, and mux session even when pane
   environment variables are absent.
2. Persist the complete continuation in exactly one home:
   - a worktree-pinned agent-dispatch task when a managed worktree and
     coordinator are available; or
   - a one-time file in resolvable managed-worktree or adopted-anchor state.
3. Build a maximum-200-character ASCII seed containing exactly three logical
   parts: task lead, canonical consume recommendation, and one opaque
   `task:<id>` or `file:<id>` recovery locator. Executable source and installed
   paths remain outside the seed.
4. Launch the successor without changing the worktree head. Submission of the
   initial prompt creates the real Copilot session; `sessionStart` may then
   record it as the handoff token's candidate.
5. The successor consumes the stored baton. Task-backed consumption writes a
   durable checkpoint before the one-time consume.
6. One atomic token acknowledgement binds the successor, links succession, and
   verifies the new head.
7. Update the work-stream title.
8. Retire the predecessor only after revalidating its originally recorded mux,
   PID, and process creation identity. The mux receives bounded graceful/hard
   pane shutdown; any surviving Copilot process is reaped through an atomic
   creation-verified platform primitive.

If any step before acknowledgement fails, the predecessor remains authoritative
and the stored baton remains available. Same-successor retry resumes from its
checkpoint instead of replaying a consumed task.

## Prompt and storage invariants

1. **Persist before launch.** Terminal creation is never the durability point.
2. **One baton, one home.** Do not write both a task and a file.
3. **The seed is a locator.** Full continuation markdown, executable source,
   shell commands, and installed paths never enter the seed.
4. **Prompt submission precedes session identity.** Candidate registration
   cannot be treated as takeover.
5. **Consumption is setup, not objective completion.** A deferred dispatch task
   remains owned until the inherited completion gate is met.
6. **Effort support is enrichment.** A valid active effort selects a compact
   relay delta; without one, the stored continuation is standalone. A knowledge
   repository is not a storage or lifecycle dependency.

## Ownership and retirement invariants

1. **The predecessor remains head until acknowledgement.**
2. **Takeover ordering is fixed:** checkpoint/consume, acknowledge and bind,
   succession/head verification, title, then retirement.
3. **PID is not identity.** Retirement requires the creation token captured in
   the original ancestry snapshot.
4. **Prevalidate mux retirement; reap survivors atomically.** Do not begin mux
   shutdown unless the recorded mux, PID, and creation identity still match.
   After bounded pane shutdown, Windows reaps a surviving Copilot through the
   same creation-verified process handle and POSIX uses pidfd primitives. If
   that atomic reap is unavailable, report retirement failure and never signal
   a numeric PID unsafely; the pane may already have exited.
5. **Free-form errors prove nothing.** Retry after uncertain task consumption
   requires structured task status, owner, and successor session identity.

## Cross-plugin invocation invariants

The extension and payload-local CLI share one SDK-free core and must reach
`agent-worktrees` and `agent-dispatch` without changing prompt bytes or command
ownership.

1. Resolve the sibling beneath the current plugin's marketplace installation
   root, and verify its manifest name and canonical repository provenance.
   Never search arbitrary marketplaces or use ambient `PATH` to choose the
   sibling payload or its Python runtime.
2. Use the sibling payload's authoritative resolver to locate or first-use
   provision its versioned runtime. Provisioning receives an installation-sized
   timeout independent of the shorter operation timeout.
3. Invoke the resolved Python interpreter directly with exact argv in isolated
   UTF-8 mode (`-I -X utf8`).
4. Remove inherited `PYTHONHOME` and `PYTHONPATH`; set the owning payload-root
   environment required by the target plugin.
5. Never render user-controlled prompt, title, identifier, or payload text into
   batch source, shell source, or a PowerShell-to-native argument boundary.

These rules are stricter than ordinary static command-glossary invocation
because handoff operations still carry non-ASCII payload text and quote- or
shell-sensitive titles even though the startup seed itself contains only an
opaque recovery locator.

## Degraded modes

| Available surface | Behavior |
|---|---|
| Extension + mux | Store, launch a seeded successor, consume, take over, and retire |
| Extension, no mux | Store and return a clearly delimited manual seed |
| No extension, payload available | Use payload-local `handoff-cli.mjs`; storage and lifecycle semantics remain identical |
| Managed worktree, no dispatch | Use one-time worktree-state file |
| Adopted anchor | Use one-time `@anchor` state file; task pinning is unavailable |
| No active effort | Store the complete standalone continuation |
| Unresolvable checkout | Fail rather than writing a baton into the repository |

Automatic creation of a new non-mux terminal is a separate capability. It must
preserve this pattern's persist-first, lossless prompt, verified takeover, and
identity-bound retirement contracts.

## Evidence

Deterministic contracts cover seed length and structure, extension/CLI parity,
one-time storage, checkpoint convergence, exact takeover ordering, identity
verification, and full payload round-tripping. The opt-in clean-room evaluation
additionally measures submitted prompts, turns, consume calls, takeover timing,
retirement decisions, and byte-for-byte payload fidelity.

## Serves and exemplars

- **Vision:** `agent-fabric` delegate-and-hand-off, handoff orchestration above
  primitives, survivable work, and context-pressure continuity.
- **Vision:** `plugin-services` installation cells and attributable invocation.
- **Exemplar orchestrator:** `plugins/context-handoff`.
- **Ground-layer exemplar:** `plugins/agent-worktrees`.
- **Optional durable-store exemplar:** `plugins/agent-dispatch`.
