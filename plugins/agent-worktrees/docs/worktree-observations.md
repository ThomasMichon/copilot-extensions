# Selected worktree observations

The optional owner-observation provider serves one selected worktree through the
existing elected resident. It materializes **accepted current-roster
observations**, not a second worktree ledger or a lossless history of canonical
mutations. Ordinary roster, agenda and canonical-write behavior does not depend
on this optional index.

This describes source support. Independently installed executables and residents
may lack it; a source merge does not install or activate a running receiver.

## Passive commands and discovery

Use the attributed `agent-worktrees` command from the session catalog:

```text
<agent-worktrees catalog argv[0]> --project <project> worktree-observations \
  --worktree <full-worktree-id> --session <selected-head> --contract-version 1 --json

<agent-worktrees catalog argv[0]> --project <project> worktree-observations \
  --worktree <full-worktree-id> --session <selected-head> --follow \
  --after-cursor <owner-position> --contract-version 1 --json

<agent-worktrees catalog argv[0]> --project <project> worktree-observation-history \
  --worktree <full-worktree-id> --session <selected-head> \
  --source-generation <journal-generation> --before-cursor <owner-position> \
  --limit 64 --contract-version 1 --json
```

`--session` may be omitted to request explicitly unknown head qualification.
`--after-cursor` and `--before-cursor` default to no requested boundary; neither accepts a
roster revision, agenda revision or session-event ID in place of its opaque
owner position. History requires its source generation and accepts a limit from
1 through 64. Unknown flags and unsupported CLI contract versions are rejected.
Output is JSON/JSONL; `--json` makes that intent explicit.

The selected executable reads only an existing endpoint and credential. It
negotiates the running receiver's `worktree-observations-feed-v1` or
`worktree-observations-history-v1`, independent observation readiness, published
state and actual owner generation before sending a new action. It does not
ensure a monitor, provision a runtime, adopt a project, start an agent, copy
credentials or fall back to a durable roster cache.

Both payload dispatchers suppress first-use self-provisioning for these reads,
including the `worktree`/`worktrees` namespace aliases and explicit project
prefixes, with or without the leading `agent-worktrees` compatibility prefix.
With an existing runtime but no receiver, the command emits an
unavailable error rather than starting one. Without a project context it emits
a structured `project-unavailable` error. An explicit project name is a
read-only namespace selection, not evidence that adoption occurred.

## Data and coverage

The resident uses its existing SQLite index path and owner election. Selected
facts come only from an accepted current-roster row with current provenance and
a known record incarnation. A yielded head or ambiguous execution-leg resume
target cannot become an asserted canonical session.

Frames declare `history_scope: retained-range` and partial observation coverage.
They retain exact project/worktree, journal and resident generations, head
qualification, position, availability, durability and observation time.
Unavailable or unqualified facts are not an empty observed worktree.

Stable selected fields omit timestamp-only metadata. A timestamp refresh or
heartbeat therefore does not create an event or advance the history cursor.
`observed_at` describes the accepted roster observation; it is not the canonical
record's commit time, proof of activity or a rigid freshness deadline.

Sampled changes are labelled `observed-change`, with compact integrity
references instead of duplicated large before/after facts. Claim-count
differences do not manufacture `claim-added`, effort binding or disposition
events. Changes between samples, before demand or outside supported fields are
not reconstructed.

## Advisory hook files

The current-roster owner also has an optional small file projection for shell
consumers. It is separate from the selected observation journal and does
not replace managed-session admission, canonical write receipts or hook policy.
The prepared Windows read, native-write and single-shell consumers below use it; other
ordinary events retain their existing execution path.

The monitor's original passive discovery record advertises
`hook_facts_version: 2` and `hook_facts_generation`, a process-local publisher
identity. Do not substitute `runtime_generation`: that existing field identifies
a build and can be shared by replacement processes. The original project-state
owner resolves `project_dir(project)/worktrees/<id>/hook-facts/`; read only the
advertised `<hook_facts_generation>.json`, never a directory scan or newest file.
Adopted-anchor admission remains separate; this projection covers tracked
worktree rows with known incarnation, workspace, current record provenance and
sampled live-session membership/flags. Inactive rows do not create file homes.
Those eligibility observations do not certify process liveness or head custody.

Each UTF-8 envelope has exactly `version`, `quality`, `completeness`, `scope`,
`producer`, `revision` and `facts`. Scope is exact project/worktree/incarnation/
workspace. Producer metadata retains selected runtime root, build/publisher/
projection generations and the existing installation-context fields. Facts
contain only `status`, `status_note_at`, `last_session_id` and `head_yielded`.
Version 2 additionally carries `record_head_session`, the canonical record's
resolved head, independently of the listing/resume target. A yielded session or
an execution-leg resume target is not a recorded head. The producer passes this
observation separately from its original in-memory records, matching worktree,
incarnation and workspace against the accepted row. It never extends the legacy
roster wire or durable row schema. Missing or mismatched observations retain
version 1; absence is unknown, not an asserted empty head.
The listing session is an observation, **not head custody or permission to
bind, resume or retire anything**. Missing facts stay null. The whole envelope
is always advisory and partial, even when its source record was current.

Bounds are 16 KiB per file, 4 KiB per string, a fixed four-level admitted shape
and 4096 rows per project publication. Safe regular-file replacement occurs in
the same directory with no retry loop. Configured project-root aliases retain
their original admission; derived children and producer output files cannot
be links/reparse points or hard links. Unrelated row-field/timestamp changes
do not rewrite unchanged facts. The retained revision names the accepted row
that last changed this view, not the latest whole-roster observation.
The original project root resolves once per publication or withdrawal, never
once per row or as a lifetime cache. Derived-path checks still run before each
replacement or removal.

The original runtime withdraws affected files on publication failure,
unqualified/removed rows, admission closure and shutdown. Old publishers only
own their own filenames; closing one cannot remove a successor's view.
Failure of this optional projection does not change canonical writes or the
existing roster. If withdrawal itself is refused, the publisher retains its
owned path and reports the error; it does not delete aliases or claim fresh data.
A crash can leave an old advisory file. Presence is never daemon-liveness or
current-authority proof.

`Read-PostToolHookFacts` in the separately shipped `scripts/hook-facts.ps1` library is a
read-only point reader. Its caller must
already supply original-owner discovery, selected runtime/project roots and
exact expected project/worktree/incarnation/workspace after applicable live
admission/input checks. It validates the full envelope and scope, reads at most
16 KiB plus one overflow byte with replacement-compatible file sharing, and
returns null for absent, malformed, wrong-scope or unsupported data. It performs
no command discovery, Python fallback, daemon call, directory enumeration or
wait for publication. The existing bounded-file input is decoded in process. PowerShell 7 uses its
bundled JSON reader with depth 4, duplicate-field rejection and date coercion
disabled, preserving timestamp strings exactly; PowerShell 5 uses the existing
framework JSON library with a separate depth-4, 16-KiB decoder. Missing parser support is unavailable, not
permission to install one or invoke Python. Ordinary event decoding retains
its shell semantics. No lifecycle operation follows from a returned advisory view.

Version support expands only this passive file contract. Old hook callers keep
their existing behavior; new readers facing old/rolled-back discovery report
unavailable rather than guessing support. A complete shell-only event transition
still needs managed-admission publication, reminder/reset and late-guidance
consumers, nonblocking invalidation and a qualified POSIX decoder. Those
requirements are not supplied by these files.
### Windows read-event consumer

The ordinary PowerShell entry loads the library in the same shell.
For prepared native Windows `view`, `rg` and `glob` events within the 1 MiB
prepared-event bound, including outstanding late-guidance watches, evaluation
has no synchronous resident request, command
discovery or Python fallback. Read events do not need mutation invalidation.
The native-write subset below shares this shell-safe evaluation. Other tools,
unprepared installation cells and oversized events keep their original entry and
behavior. This is not the complete shell-only transition.

Prepared entry uses native file/path operations without loading PowerShell's
Management module. Fully qualified source paths defer cwd repair until the
original fallback is needed; relative, drive-relative and root-relative paths
retain early repair. Runtime-root resolution preserves both normalized-path
validation and literal ancestor/reparse checks, including ancestors hidden by
dot segments. This changes neither prepared admission nor fallback policy.

Prepared JSON conversion avoids initializing Utility cmdlets in a fresh shell.
PowerShell 6+ uses its public object-to-hashtable decoding engine; serialization
uses the public JSON context when available, retaining the cmdlet on older
engines without that API. PowerShell 5 keeps the framework decoder and the
shell's metadata type resolver, and serializes its JSON graph with the same
framework library. Unicode, numeric/date-like values and metadata retain their
shell representation. Generic post-tool fallback conversion is unchanged.

The reader checks the original prepared membership and all its input witnesses.
It then reads the binding owner's `worktree-binding.json`. Only the owner's
guarded exact-record recheck adds the `hookFacts` version-1 locator with resolved
`projectRoot` and actual record `workspace`; the caller-supplied display path is
not that locator. Expected project/worktree/incarnation come from this binding,
not the advisory file being checked. Identity/path comparisons are ordinal.
The current event must still be within the exact workspace. No home or
incarnation is inferred from a directory name.

The original discovery selects one publisher file under `AGENT_HOME` when
configured, otherwise `USERPROFILE`, matching prepared membership and the
publisher. This does not relocate reminder accounting: the counter and shared
lock remain under `USERPROFILE/.agent-worktrees/nudge-state` in both shell and
Python. Version-2 facts drive the
existing disposition window/call counter and bind-reminder cooldown at their
original sidecar paths. Shell and Python counter updates and terminal clears
share a byte-range transaction on `nudge-state/<worktree>.lock`, waiting at most
100 ms for contention (or the remaining Python event deadline). A contended
event skips this advisory counter without changing it or replaying the event;
the independent bind reminder remains eligible. The stable lock file is not
deleted on release. Timestamp note identities normalize to UTC microseconds in
both paths, including existing sidecar values, so YAML's equivalent space/T or
offset representations do not reset mixed read/edit accounting.
Note changes reset the window, terminal observations
clear its counter, live environment controls remain live, and a listing target
does not suppress the no-recorded-head reminder. Neither a reminder nor its
absence changes head custody or permission. No current event is sent or replayed.

Missing, changed, malformed, old-contract or unsupported prepared observations
produce no advisory result for these read events, without blocking on cold
reconstruction or a resident. They are unavailable data, not complete-empty
state. The bounds are 1 MiB for event/admission, 16 KiB each for binding,
discovery and facts, and 4 KiB for the existing advisory counter. Binding and
discovery decoding has depth 8; the fact envelope retains depth 4.
Derived paths reject reparse components. Registered-entry tests use the real
membership, binding and current-row publishers, with interpreter/resident
traps, and distinguish hook-entry time from shell-process startup.
These isolated source controls do not establish installed, ACP/model,
POSIX, complete mutation or whole-hook throughput acceptance.

After prepared admission, late-guidance notification is independent of the
binding locator and current-row files. It consumes only the original session
watch (8 KiB), request (256 KiB) and publication revision (4 KiB), with depth-8
decoding and the same canonical local-path restriction. Pending requests retain
their watch unless the native process probe establishes that the recorded PID
is absent/exited. Inaccessible, malformed, out-of-range or live/reused process
identities remain unknown; this probe does not establish incarnation ownership.
Terminal requests must complete at or after the watch's start. Partial/failure,
possible changes and later publication revisions retain the original fallback,
repair, restart/new-context and no-model-admission wording.

Windows shell and retained Python consumers atomically rename an eligible watch
to a unique same-directory consumption path. Only the winner with the evaluated
snapshot emits a notice. The entire claim/check/restore transaction shares a
bounded byte-range guard at `local-guidance-watch.json.lock`, waiting at most
100 ms. Contention skips consumption without replay; the stable guard file is
not removed. This prevents an overlapping stale claimant's restored old watch
from being mistaken for a new publication. A replacement is restored without overwriting a newer
watch; a superseded claimed snapshot is removed. Failed restoration without a
replacement preserves the claimed file rather than discarding it. This is
best-effort advisory consumption, not durable delivery or a new preparation
store; interruption after consumption can still lose a notice. A notice skips
disposition accounting, even when enabled; reminder opt-out never suppresses
late guidance, and independent bind advice still runs when its own facts exist.

#### Optional read demand

After prepared admission, the shell can send a separate `readHookDemand`
notification through the existing hook TCP endpoint. The running receiver
advertises `read-hook-demand-v1` in `hook_capabilities` and its process-local
`hook_generation` through the same bounded discovery record. This support is
independent of advisory-row availability, reminder opt-out and late guidance.
It is selected for native Windows prepared read events, including the
qualified installation-cell subset below.

The version-1 notification contains only `version`, `kind`, `token`,
`hook_generation` and `deadline`; it carries no tool arguments, session content
or mutation targets. It is at most 2 KiB, newline terminated, and uses one
loopback connection with a combined 100 ms allocation/connect/write wait. The shell
disposes that connection without reading any response. Missing, malformed,
wrong-installation or old discovery skips the optional send. A connection or
write failure has unknown acceptance and is not retried, replayed through
another endpoint or replaced by Python. Provider rollback therefore retains
the shell-safe advisory baseline, not a blocking compatibility call.
The sender allocates an explicitly IPv4 client for its IPv4-only loopback
endpoint, avoiding the modern runtime's default dual-mode socket selection.
Client allocation remains inside the send clock. Both connection and write
waits subtract the elapsed time; allocation cannot receive a separate wait
allowance. The 100-ms ceiling gives cold framework initialization headroom
without making the hook wait for a response or adding a retry.
The sender first checks the runtime's IPv4 support. That capability query can
initialize the socket subsystem on first use; its cost is part of whole-hook
entry, not the 100-ms client-allocation/connect/write clock. No probe connection
or request is made by this preflight, and it does not establish receiver readiness.

Only the advertised process generation can accept the notice. The receiver
rechecks its own admission and the monitor's live published/non-draining state,
requires a finite unexpired deadline at most one second ahead, and records the
original 120-second recent-tool idle grace. It does not invoke policy,
advisory accounting, mutation invalidation, a sweep wake or lifecycle startup.
Admission and renewal take the monitor's existing state lock before the handler
lock, serializing them with the final idle-check/drain transition. A contended
receiver waits at most 50 ms, then skips the optional renewal without replay.
The existing active-handler drain accounting and all other idle predicates
remain in force. A sent notice is not an acknowledgement, durable delivery,
fresh-status observation, permission or readiness proof. A missed notice may
allow ordinary idle expiry; this operation does not recover an absent resident.
Old callers retain their original request/response semantics with new receivers.

### Windows native-write consumer

Prepared single native writes (`create`, `edit`, the supported `str_replace`
variants, `write`, `write_file`, `insert`, `apply_patch`, `new_file` and
`multi_edit`) share the same shell reminders and late-guidance evaluation.
They do not request advisory computation from the resident or fall back to
Python. Batches outside the supported subset below retain their original
path. Single shell events have their separate consumer below.

The existing hook IPC owner optionally advertises `native-hook-mutation-v1`.
The shell sends one `nativeToolMutation` notification with the same version,
token, process generation and one-second deadline envelope as read demand,
plus `payload`. That payload contains only the event's tool name, arguments,
current admitted workspace and session; tool results are not transmitted.
The newline-terminated wire is bounded to 64 KiB, with the same combined
100 ms allocation/connect/write wait and no response, retry or uncertain-event replay.
Absent, old or invalid discovery and an oversized notification skip the
optional send, not shell advisory evaluation.

The receiver rechecks managed-session membership, current published admission,
governance and shutdown under the original monitor state lock, with at most
50 ms lock contention. It rechecks the wire deadline after acquiring that lock;
an expired notice cannot invoke the mutation callback or renew demand.
It rejects batches and non-native tools. Native targets
come from the guard-owned `tool_call_forms.write_targets`, including every
structured edit and both patch-move endpoints. Relative paths use the event
workspace. Unknown targets retain the original workspace invalidation;
extractor overflow/unavailability conservatively invalidates all segments.
No new target parser, permission cache or durable mutation store is introduced.
The same native extraction also corrects the retained request/response path's
first-target-only invalidation.

Only cache invalidation, the original mutation-triggered sweep wake and recent
tool idle grace follow acceptance. There is no project discovery, policy,
advisory reply, recomputation or lifecycle boot in this notification branch.
Transport completion is not acceptance or fresh-status proof. Lost/rejected
notices leave the existing periodic/TTL refresh backstop in charge; they are
not durably queued. These isolated source controls do not establish installed
composition, cold/unprepared-cell/POSIX entry or whole-hook throughput acceptance.

### Windows native-batch consumer

The same prepared Windows shell path accepts nonempty `toolCalls` arrays of
at most 128 calls, each containing only `name`, optional `args` and optional
`id`. Every name must identify a supported native write or `view`, `rg` or
`glob`. Single-call arrays stay arrays. A malformed call, unknown tool,
per-call workspace override, empty batch or excessive call count leaves the
entire event on its prior path; no supported prefix is sent or evaluated
separately. The existing 1 MiB event and live prepared-membership bounds remain.

The original receiver separately advertises `native-hook-batch-mutation-v1`.
One send-only `nativeToolBatchMutation` carries the batch plus the original
event workspace/session fields, not tool results. The 64 KiB wire limit,
process-generation/token/deadline validation, combined 100 ms allocation/connect/write
wait, current admission/state lock and no-reply/no-retry semantics are the
same as single native mutations. New consumers skip the optional notice when
old discovery advertises only single-write support; they do not reinterpret
that capability as batch support or fall back to a synchronous request.

Before any effect, the receiver validates every call and uses the existing
`tool_call_forms.tool_events` normalization. Its existing permissive callers
remain unchanged; this notification requires the strict complete-batch subset.
Top-level cwd and argument inheritance retain that owner's semantics.
The original native extractor gathers unique targets across the batch before
invalidation. The aggregate limit is 128 targets, not 128 per call; overflow
or an unavailable target extractor invalidates all segments conservatively.
Unknown native targets invalidate the event workspace. Read-only batches
renew idle grace without invalidation or a sweep wake. Accepted writes
invalidate their targets and use the original mutation wake.

Reminders and late-guidance consumption run once per outer event, not per
call. No current event is replayed, and a lost notification retains the
existing refresh backstop rather than asserting fresh status. This source
consumer does not qualify shell batches outside the mixed subset below, installed behavior, cold/unprepared-cell/POSIX
entry, complete hook composition or actual throughput.

### Windows mixed-batch consumer

A prepared Windows batch may also contain the original shell aliases together
with native writes and `view`, `rg` or `glob`. The complete strict shape, 128-call
limit, canonical event/workspace/argument normalization and once-per-outer-event
advisory accounting are unchanged. Unknown tools, extra per-call fields,
malformed calls and empty/oversized batches retain the entire prior route.
The outer tool name never overrides the batch's actual calls.

The original receiver separately advertises `mixed-hook-batch-mutation-v1`.
One send-only `mixedToolBatchMutation` uses the existing 64 KiB wire,
generation/token/deadline, selected-installation, live admission, state-lock and
100-ms allocation/connect/write contracts. Native-only batch or single-shell capabilities
do not establish mixed-batch support. Missing support skips the optional
notification while preserving shell advisory evaluation, not a blocking fallback.

Before any effect, the receiver validates every call through the original
strict normalizer and then reuses the native target extractor and single-shell
mutation classifier. Read-only shells do not hide native targets; any
conservatively mutating shell or aggregate target overflow invalidates all.
Otherwise targets are deduplicated across the complete batch with a shared
128-target bound. Entirely read-only batches renew idle demand without a sweep
wake. Permission policy and legacy `postToolUse` behavior remain unchanged.

No commands are executed to classify a batch. There is no reply, retry,
current-event replay, new publisher, boot operation or durable notification.
Lost delivery retains the existing refresh backstop and does not certify fresh
status. These source consumers cover prepared legacy and eligible installation
cells, not cold/directory/POSIX entry, installed composition or actual throughput.

### Windows single-shell consumer

Prepared single events for the original shell tool aliases use the same
in-process reminders and late-guidance evaluation. The original IPC owner
separately advertises `shell-hook-mutation-v1`; a `shellToolMutation` carries
the tool, original argument fields, workspace and session, without tool results.
Native-write support alone is not shell support. The same 1 MiB event,
64 KiB newline-terminated wire, one-second generation-qualified deadline and
combined 100 ms allocation/connect/write bound apply. Missing/old discovery skips the
optional send. No reply, interpreter fallback, retry or event replay follows.
Mixed shell/native batches have the separately negotiated consumer below.

The receiver reuses the original guard argument normalizer, including JSON
strings and raw command strings. Its mutation-only classifier composes the
original shell segmentation, substitution, literal-target and Git-option
parsers. It does not evaluate or cache permission: `pull --ff-only` and `fetch`
can change observed repository state even though the anchor guard permits them.
Known Git reads and read-only branch flags do not invalidate or wake a sweep;
an output-file option before the path delimiter makes an otherwise read-only
Git command a mutation. PowerShell dot invocation is normalized for the same
classification, including assignment right-hand commands.
The original literal reader normalizes module-qualified command names and
reports known-write occurrence even when the target is unresolved. Explicit
command-evaluation forms invalidate conservatively; quoted evaluation text
passed to an ordinary read command remains data.
Other Git subcommands are conservative mutations. Supported literal writes and
redirections invalidate globally, retaining the original shell target scope.
Missing commands/parser support and traversal overflow are conservative too,
with at most 128 segments, eight nested substitution levels and a shared
131,072-iteration substitution-scan budget per classification. The existing
invalidation and wake owners can each classify; this adds no permission memo.
Quoted literal parentheses are not executable PowerShell grouping; assignment
right-hand commands retain mutation detection without treating quoted data as
a command. Substitutions are examined before environment-assignment
normalization. Unsupported executable block/control-flow and complex assignment
shapes conservatively invalidate rather than being asserted read-only. The
original quote-aware tokenizer reports unquoted block/type syntax for this
classification, including pipeline/invocation blocks and typed assignments;
quoted data does not become executable syntax. Type conservatism applies to
typed-variable prefixes, not bracketed read paths or ordinary static read
calls. The original literal reader recognizes clustered in-place `sed` flags
without treating attached expression/file option values as flags.
Ordinary tokenizer callers do
not request that observation and keep their original output. Read-only `sed`
does not invalidate unless it independently redirects output, while in-place
forms remain mutations. Batch presence takes precedence over outer shell
metadata, preserving the original native-batch request kind. Classification
remains a heuristic, not execution of an arbitrary shell program or proof that
external programs cannot write.

Unclassified forms retain the original stateless guard's write heuristic rather
than being silently declared read-only. Confirmed literal/read forms avoid
that fallback, and source-bound controls use the actual original pattern.
Dialect-ambiguous aliases and the not-yet-qualified CMD dialect retain the original occurrence rule entirely;
the receiver does not guess their dialect. This is conservative compatibility,
not a new permission decision or an assertion of complete command semantics.
The original tokenizer treats ordinary braced parameter expansions as data,
without hiding executable substitutions inside them. Post-only assignment
normalization distinguishes data values and complete read-expression containers
from unknown executable forms. Control keywords require a complete command
token and use the declared dialect, so `Select-String`, `Select-Object` and
PowerShell's `select` alias are not mistaken for POSIX control flow.
Parameter, command and arithmetic boundaries share one iterative quote-aware
matcher. Quoted/escaped closing braces and nested executable forms cannot
swallow a following command; the same boundary is used by the original
pre-tool guard and post-tool invalidation. Unknown or unterminated post-tool
input remains conservative, and the original scan-work limit is unchanged.

Original managed admission, current governance/state locks and idle grace
remain authoritative. Accepted reads renew demand without mutation wake;
accepted mutations use the original invalidation and wake. Lost notifications
retain the original refresh backstop and never certify fresh status. This
qualifies only the isolated prepared Windows source consumer, not installed
composition, cold/unprepared-cell/POSIX entry or actual throughput.

### Prepared installation-cell post-tool consumer

The same registered Windows entry also consumes the complete prepared-cell
membership described below. Supported reads, native writes, native/read
batches and single shell events use the existing event normalization,
notification capabilities, reminder accounting and late-guidance owners.
They introduce no new wire format, parser, publisher or lifecycle.

Only after current prepared membership succeeds does the shell select the
runtime directory beside the exact context pointer. That membership has
already validated the SDK's canonical marketplace/plugin layout and all
receipt/manifest witnesses. Passive discovery must match that cell's
marketplace directory identity, exact install receipt and plugin root before
either notification or advisory-row consumption. Legacy or foreign
discovery in the selected cell is unavailable, not a reason to try another
cell or the legacy endpoint. Producer metadata in the advisory view still
must match the selected discovery and runtime exactly.

Cell reminders retain their original cell-local `nudge-state` directory;
legacy discovery and reminder locations above are unchanged. Live opt-out
does not suppress demand or late guidance. Notifications and reminder
accounting remain once per outer event, and late notices suppress only the
original disposition counter. Missing rows do not suppress independent
idle demand or late guidance.

Old or incompatible cell membership, cold admission, directory-source
namespaces, oversized events and unsupported batches retain their original
entry route. This warm-path migration does not remove or qualify their
Python fallback. A qualified warm event with unavailable/old discovery
stays shell-only and skips the optional notification without retry or replay.
The existing 1-MiB event/membership and 16-KiB discovery/fact bounds remain;
the Windows sender shares the 100-ms allocation/connect/write ceiling above.
Source-native controls are not installed,
composed-latency, POSIX, recovery or actual-throughput acceptance.

### Prepared Windows membership reuse

The existing `managed-session.ps1` admission entry first checks the original
owner's exact-session `agent-worktrees/managed-session.json` association.
For a supported legacy Windows caller it validates the session, workspace,
registry, platform, original project/repository sources and every recorded
filesystem witness in process before returning positive membership. The
reader uses the original size, kind and 100-nanosecond timestamp semantics;
path and scope equality is ordinal, and event extraction preserves JSON array
types instead of treating singleton arrays as scalar identities.
It does not reparse registries, run Git, discover a runtime or start Python.
Membership remains distinct from permission, head custody and liveness.

The read is capped at 1 MiB plus one overflow byte, 256 witnesses, 4,096
characters per path and 128 path components. Only canonical local Windows
paths without reparse components are eligible. Missing, changed, malformed,
oversized, unsupported-cell, UNC and alias observations retain the existing
owner admission path. Cold reconstruction still uses that original path.
This is validated warm-path reuse, not a complete shell-only hook transition
or a replacement for installation-context validation. Registered post-tool
tests compose the actual owner association, unchanged shell entry and private
resident with interpreter lookup trapped; their synthetic project/provider
does not establish installed, ACP/model or whole-hook throughput acceptance.

#### Prepared installation-cell membership

After the original installation-context resolver validates an explicitly
selected cell, the same membership producer adds an optional `installation`
observation to its version-1 association. That observation has version 1,
the resolved `namespaceReceipt`, `installReceipt`, `payloadRoot`, `cellRoot`
and `pluginRoot` paths, an explicit `sourceKind`, three `declaredPaths`,
exactly three `files` digests, and seven `roots`
entries retaining each receipt-declared lexical path and its resolved path. The original
registry adapter borrows receipt digests from the SDK's active validated-read
scope and the manifest digest from its original payload read; it does not
re-read or re-validate those documents to manufacture evidence. It captures the
declared roots during the original SDK's root resolution, not another invocation.
It also captures the SDK's original string reads for receipt `payload.root`,
`pluginRoot` and `namespaceReceipt`. Each must occur exactly once and directly
match its validated canonical result; aliases, dot segments and incomplete
capture retain original admission without a second path resolution or receipt read.
The original
sources also witness both receipts, the selected roots and manifest.
Membership publication adds
no permission, current-runtime assertion or installation operation.
Only original validated GitHub, Git URL and opaque namespace identities qualify.
Directory namespaces retain original admission, including stable-ID directory
sources: prepared reuse does not duplicate their source normalization or assume
an external directory's identity remains unchanged. Older observations missing
the explicit source eligibility cannot qualify.

The Windows reader requires that complete observation, exact selected context
and payload, the canonical same-cell/plugin layout, every mandatory source
witness and all original ownership/input checks before reusing membership.
All three `declaredPaths` must be present, still canonical and unlinked, and
match their selected paths. Older observations missing them cannot qualify.
Every declared root and its lexical ancestors must remain canonical and unlinked,
including roots not used by project membership. Missing directories may be
created normally; root existence is not cached, so publishing the association
into an initially absent cache does not invalidate its own observation.
It neither parses receipts itself nor runs the resolver in shell. Each of
the three witnessed files is read in process with a 1-MiB-plus-overflow-byte
bound and compared to the original validated content digest, so restored
size/timestamps cannot hide changed receipt or manifest bytes. The existing
association's 1-MiB/256-witness/path bounds are unchanged. Inherited payload
conflicts, changed or missing witnesses and incomplete older associations refuse reuse.
Association and content buffers use the opened file's bounded length plus
one overflow byte, rather than eagerly allocating the maximum for small files.
A file that already exceeds the cap or fills that sentinel buffer is
unavailable; a concurrent append cannot turn a valid JSON prefix into a
complete admitted document. All original content and witness checks remain.
The original resolver still owns cold or incompatible admission. On a valid
old association it can publish the additive view without Git/registry traversal;
an unchanged complete association needs no rewrite.

The registered Windows pre-tool entry also uses that per-call native result
for its [source-bound native proofs](architecture.md#native-windows-pre-tool-entry).
The exact `view`/`rg`/`glob` read exemption still checks all four guard-source
digests; complete native read/write batches retain their own additional proof.
A single prepared PowerShell event may also use the existing relevance owner's
exact no-argument literal declaration, bound to that source as well as the
guards. Its object command selection follows the identical ordered key
declarations derived from all four original guards: the first nonempty string
wins, including an earlier protected or whitespace value. Optional selection
metadata absence keeps the command-only baseline; malformed supplied metadata
retains whole raw fallback. Older command-only readers do not gain alternate-key
support merely by accepting a new declaration. Complete bounded read-only batches
can compose the same proof over each shell member's own arguments, alongside
`view`/`rg`/`glob`. Shared declaration and source verification runs once per event,
including the original batch and relevance owners. Any uncertain or protected
member retains whole raw fallback; top-level fields cannot rescue it.
Older readers retain their original shell-batch path. The optional version-1
argument facts extend the same proof to one or more original-safe unquoted ASCII
arguments, using the original relevance grammar and every original write/PR
invocation veto. The renderer extracts those expressions without importing their
owners and refuses unsupported syntax/flags. Predicates are compiled once per
event and reused across members, with one declaration load and shared source
pass. Missing/invalid argument facts preserve an independent exact-literal proof;
any batch needing an unproved argument member falls back whole. Prior readers
ignore those facts and retain argument fallback; new readers with old data do
likewise. JSON strings, Unicode, unsupported whitespace, quoted or arbitrary
shell syntax and write/PR-looking arguments do not gain this proof.
The same exact literal can come from a raw string, or from `tool_input` only
when `toolArgs` is absent. A present null, empty or invalid primary value never
falls through to the fallback carrier. Raw text is compared unchanged, not
trimmed, unquoted or decoded.
Fallback admission does not qualify a prepared proof, even if the original
resolver refreshes the association during that call. Changed sources, incomplete
or changed cell observations and incompatible declarations retain original
validation/policy. The default-installation emergency stop never governs a cell.
This does not qualify installed or composed hook latency.
After guard or proof-dependency source changes, regenerate the shipped contract
with `python tools/render-hook-read-policy.py`. Registered-consumer fixtures use
the actual packaged declaration; literal comparator legs explicitly omit only
its optional proof and record that contract identity. Argument comparator legs
omit only the optional argument facts, retaining the existing literal proof.
Stale source digests are
never repaired in fixtures to manufacture qualification.

New producers remain compatible with old readers, which ignore the additive
field and retain their old cell path. New readers use an old producer's
association only after its original resolver supplies the required observation;
they never infer cell support from the schema number alone. An older SDK
without validated-read capture support supplies no prepared cell observation,
and retains its normal original-owner admission. This membership proof alone
does not establish the post-tool consumer above, shell-only cold reconstruction,
installed hooks or composed throughput.

## Qualified resident native saves

The resident can additionally ingest complete committed-save receipts from
canonical transactions executed through its existing `tracking_write` server.
The producer registers explicitly at receiver activation. Its callback only
queues immutable evidence after all physical/logical record and pair holds;
the existing publication/wake path performs materialization. There is no public
receipt-publication RPC, new listener, spool or per-panel producer.

Supported transactions are ordinary claim add/settle/release, disposition and
the shared effort bind/release transactions. An admitted `claim_release` with
`--remove` emits `claim-released` with the saved local claim as `before` and
`after: null`, including removal of an already-released ledger entry. This
describes local ledger removal, not destruction of or custody over the referenced
resource. Missing claims outside an admitted release do not imply a native release.
Effort writes retain their global
binding exclusion, conflict checks, completion/transfer rechecks and direct
fallback; they do not start a resident merely to obtain observation coverage.
One compute can save zero, one or several times to its exact admitted record.
A successful record save retains its receipt if the broader command subsequently
fails. Receipt intake and materialization both enforce the admitted normalized
path and full worktree ID; an observer's reentrant direct save of another record
does not inherit that admission. Path comparison is lexical normalization, not
proof that symlink/hardlink aliases are interchangeable with the canonical path.
Nested computes and computes entered with outstanding notification holds,
delivery or queued work have uncertain attribution and remain diagnosed partial
coverage. They still execute their canonical operations normally.

Native qualification requires complete selected before/after evidence, matching
record incarnation and versioned execution identity, the owning canonical path,
and strong sidecar exclusion at capture. Partial, omitted, degraded, legacy-
identity, ambiguous or producer-managed claim evidence does not become an
authoritative native transition. Resource references are relations; save receipt
IDs and transition discriminators form native event identities.

Native entries use the existing v1 kind/evidence vocabulary and
`persisted-mutation`; actors remain unknown. Caller session IDs, tokens and
record heads are not performer identity. The frame still carries the latest
selected snapshot head/facts; each native entry retains its own captured head
and canonical-save fence. A delayed A receipt cannot reinstall A's facts over B.
Index positions order admitted evidence, not a total canonical commit order.
Before appending native evidence, the owner materializes any sampled difference
from the accepted roster. A queued receipt therefore cannot hide an unrelated
direct write in the same sample; that observed change retains current-view
integrity references while the native entry keeps its captured save fence.

The native-specific dual-fence path validates current-view facts separately
from saved-record evidence. Ordinary post-save publication retains its original
fence-equality check. Both paths reuse source/batch/native identity conflict
guards and durable admission intent, with provider/runtime/producer epochs and
writer authority rechecked before materialization and resolution.

Retries reuse notification evidence and native identities only; canonical work
is never replayed to repair a missing acknowledgement. Loss before intake/first
intent, direct-fallback writers, unsupported producers and bounded-queue loss
remain explicit partial/unknown native coverage. The running receiver reports
that limited scope in `worktree_observations.native_receipts`; it does not
advertise complete canonical history. Native observation failure never changes
the canonical write result or disables the ordinary roster.

Ordinary claim/disposition and effort-binding CLI writes retain their normal
resident wake path. When no usable resident exists they check the current user's
maintenance notice and selected installation maintenance flags, wait one brief
100ms discovery backoff, then use the existing bounded ensure/start path if
needed. Maintenance or unqualified notice contents prevent automatic recovery;
they never trigger a restart, installation or marker removal. The normal ensure
also checks maintenance before spawning. Live incompatible publishers retain
their original same-code compatibility fallback without a forced replacement.
User maintenance resolves from the canonical OS profile, not redirected
`HOME` or `AGENT_HOME`; selected installation flags retain their own root.
Held ordinary claim, disposition and effort writes return their normal
text/JSON error rather than a traceback.

A refused connection before any request bytes were sent may use one bounded
recovery/retry; readiness connection probes send no mutation bytes and wait
within the startup budget even if the same address remains published.
A possible post-send outcome never retries. Deliberately no-wake
native paths remain unchanged. Startup failure without a maintenance hold can
still use the existing logged direct fallback, whose native coverage stays
partial/unknown. This reduces avoidable fallback; it neither recovers missing
past receipts nor promises complete history. Observation/history commands remain
passive and never wake the resident.

## Point reads, pages and held feeds

The control plane uses separate `worktree-observations-read`,
`worktree-observations-history` and `worktree-observations-feed` actions. History
cannot fall through to a held roster feed. Authentication and strict request
validation precede dispatch.

Semantic frames are bounded to 35 KiB with 32-KiB bodies; point replies use the
existing version-1 control envelope with at most 64 additional bytes. JSON
duplicates, nonfinite values, invalid UTF-8, boolean versions and unknown
request fields are rejected. A valid but unsupported integer version remains
distinct from malformed input.

A held stream begins with an atomic snapshot, followed by bounded updates,
explicit gaps and connection-only heartbeats. Its 0.5-second heartbeat is inside
the CLI's five-second communication budget. The client rejects wrong selection,
changed owner generation, regressing positions and invalid base boundaries.
Backward pages do not consume a live delivery cursor.
An expired before boundary is returned as the provider's source-qualified
`gap`, retaining its floor, reason and reconciliation metadata rather than
turning an expected retention response into a transport error.

Cold selection returns explicit `unready`, retains bounded demand and wakes
the existing producer. The request itself does not materialize a snapshot or
scan the fleet. Existing owner publication supplies subsequent observations;
there is no per-panel polling producer.

Closing the CLI closes only its observer connection. Retirement closes admission
and accounts for point/history requests, held subscriptions, producers and
publication qualification before releasing owner custody. Failed accounting is
not interpreted as zero active work.

During asynchronous owner handoff, loss of writer election refuses new roster
and observation publications without closing still-admitted cached reads.
Snapshot, history and heartbeat delivery retain their generation and source
fences until read admission ends or the stored owner is replaced. Read
admission never grants activation or write authority.
If election is lost after an observation intent was admitted, `writer-fenced`
refuses that materialization without retiring still-admitted readers. The
affected selection and its unresolved intent remain explicit; unrelated cached
selections remain readable. A changed stored owner or revoked read admission
still supersedes the receiver.
Each request also captures its provider and reusable runtime admission epochs. Stale
requests retire only their own admission, never a receiver activated afterward,
including reactivation with the same stored owner. Held feeds wait and retry
transient publication qualification at initial, before-read and after-read
gates without losing their consumer delivery boundary.

## Failure and recovery boundaries

Optional index failure leaves ordinary roster and canonical operations usable.
Observation errors remain explicit; they are not permission to repair head,
release claims, replay work or restart a service.

The auxiliary admission-intent protocol protects admitted index operations
across failure and takeover. It does not make canonical record persistence and
observation intake one transaction, promise complete native history or recover
events that were never captured. Fresh current facts and retained history
coverage remain separately qualified.

Agenda planning remains advisory: unavailable or rewound agenda state does not
halt otherwise-authorized work. The
[efforts-owned recovery guidance](../../efforts/skills/driving-effort-agendas/SKILL.md#unavailable-stale-or-rewound-agendas)
describes reconstruction from context, current evidence and history. This
provider does not move agenda storage, add logger archival or change cleanup.

Frames may contain private purpose, claim and relationship metadata. Existing
repository, publication and archival policies still apply; receiving a frame
grants no additional data-publication or restoration authority.

## Validation and related contracts

Focused source coverage lives in `test_owner_observation_provider.py`,
`test_owner_observation_provider_races.py`,
`test_owner_observation_provider_control.py`, `test_owner_observation_cli.py`,
`test_owner_observation_cli_process.py` and `test_owner_observation_payload.py`.
The shared CLI transport also retains the existing agenda-relay tests. See
[repository testing](../../../TESTING.md) for bounded execution and explicit-tier
process fixtures; unavailable PowerShell is not Windows qualification.

- [Observation wire implementation](../src/agent_worktrees/owner_observation_contract.py)
- [Current Sidecar plan and remaining acceptance](../../../efforts/active/mux-companion-manual-cutover-diagnostics/sidecar-live-timeline.md)
- [Proposed cross-owner observation boundary](../../../efforts/active/mux-companion-manual-cutover-diagnostics/sidecar-observation-contract.md)
