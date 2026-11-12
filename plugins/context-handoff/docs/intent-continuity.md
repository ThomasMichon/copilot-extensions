# Durable Goal and steer continuity

The extension records root operator `user.message` inputs and peer-origin
candidate steers into immutable local records at receipt. Skill loads, child
instructions, system/plugin messages and bridge notify/status-check envelopes
are excluded; peer provenance is retained and never becomes operator authority.
Bridge prompt envelopes are conservatively retained as candidate significant
steers; this is origin classification, not authentication or semantic approval.

## Scope and persistence

The corpus lives under `~/.copilot/context-handoff/intent/<scope>/`, outside
the checkout and session folder. Scope uses the canonical Git worktree root
and metadata incarnation; standalone use keys the canonical working directory.
A single optional, non-provisioning binding lookup after extension join keeps
bare resumes on their continuing worktree instead of the shell's home directory.
When session identity arrives late, the next receipt/read/lifecycle boundary
performs that lookup once and moves only still-provisional pending inputs into
the resolved scope. Automatic replay and Goal discovery wait for identity;
known captured inputs are never rebound.
Records retain exact prompt text, source, session/event identity and timestamps.
Ordering uses event time, then receipt time/order and a stable identity tie-break;
concurrent sessions with indistinguishable timestamps have no claimed causal
order. Exact event retries deduplicate; conflicting identities refuse visibly.
If a host omits event IDs, a generated identity preserves repeated genuine
prompts rather than guessing duplicates; redelivery dedup is then unqualified.
Writes fsync the file then atomically link it into place without replacing an
existing record. POSIX also syncs the containing directory; Windows does not
claim power-loss-proof directory barriers. Unsupported filesystems report
failure instead of silently switching to session-only storage.

## Reconstruction and paging

After startup/reconnect, the first root input/tool activity supplies current
intent through a system-origin immediate message, without starting an idle turn
merely because an extension loaded. Root `session.context_cleared` and successful
root compaction also supply it. Native pressure deferral does not disable this
path. `mode: off` suppresses automatic replay, while local receipt capture and
explicit facts/reads remain available.
Config loading and live reload follow the resolved continuing worktree, not the
extension launch directory. Queued replay rechecks its worktree after awaited
discovery and discards stale-scope data. Failed sends rearm on later root activity
without changing retained records.
Handoff generation and consumption include current corpus and Goal references.
Emergency drafting uses only cached, scope-local corpus/source pointers before
storing the baton: current Goal and corpus freshness are explicitly unqualified,
and later retained inputs may be absent from the cached page. It never performs
optional Goal discovery on that last-chance pre-store path.
With an active worktree effort binding, Goal is read fresh
from the resolved effort home; legacy Guiding Intent is explicitly labelled.
For external-state pairs, that home is the owning knowledge worktree, not its
registered anchor checkout. Stale bindings retain their source pointer and
failure state instead of masquerading as absent bindings.
Missing optional worktree tooling never installs it or invents a binding.
History intentionally spans successive sessions in the same continuing
worktree, not only the current session; rereading it does not reactivate old
objectives.

Snapshots pin all record hashes. Replay and facts include the first bounded
page; call `read_intent_corpus` with its snapshot and `next` record/offset until
`next: null`. A long individual prompt is split without discarding characters.
Each default page fits 12,000 UTF-8 bytes of compact serialized JSON, including
provenance, offsets and escaping; many short steers therefore page too.
An unchanged corpus reuses its immutable snapshot without another write.
Snapshots remain readable for in-flight readers and durable handoff pointers;
there is no automatic garbage collection or retention cap. Storage use therefore
grows with captured history and distinct boundary snapshots.
Without extension tools, the payload-local `handoff-cli.mjs facts --json`
returns the same local corpus/Goal pointers and first page; use
`handoff-cli.mjs intent-page --snapshot <id> --record <n> --offset <n> --json`
for continuation pages from that same working directory. This read-only
fallback does not retroactively capture prompts missed by an absent extension.
Oversized current or legacy intent keeps a canonical file pointer for bounded direct reading, not
a shortened replacement. Corpus inputs are historical evidence: research each
against current source, completed work, supersession, original approvals and
current holds before acting. No captured prompt grants permission or executes
an effect.

## Limits and privacy

Capture uses local synchronous file commits after extension readiness, with no
per-prompt CLI, network request or transcript crawl. Failures are visible and
retain exact pending input in the running observer for lifecycle retry; a crash
before a failed input commits cannot be claimed durable. These tests qualify
registered event/SDK-fixture behavior, not native-host scheduling or model
semantic reading. Event observation cannot intercept the host before its model
turn, enforce that the model read every page, or recover prompts missed while
the extension was absent. Host acceptance and end-to-end live qualification
must be reported separately. Records contain potentially sensitive prompt
content; they are local, created with restrictive permissions where supported,
not indexed/uploaded/committed, and have no automatic deletion policy.

## See Also

- [Context Handoff](../README.md)
- [Continuation procedure](../skills/context-handoff/SKILL.md)
