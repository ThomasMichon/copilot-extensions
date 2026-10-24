# Internal original-turn native custody

Back to the [runtime custody foundation](runtime-custody-foundation.md) and
[restoration effort](../../../efforts/active/bridge-retained-runtime-restoration/README.md).
This is an internal original-turn owner foundation with explicit, local
process-owned ACP selection, not a supported HTTP contract, restoration command
or deployment authorization.

## Original admission, never retrospective enrollment

An internal owner may supply `native_consumer` together with `runtime_admission`
to `Database.create_turn`. The consumer carries the exact Bridge/native session,
transport incarnation and process PID/birth observations. The existing DB
transaction retains a single immutable handoff on the original turn, including
the atomic queued-row consumption and keyed-native paths, before returning.
The ordinary caller default remains unqualified and unchanged.

Consumer observations do not authenticate a caller or prove live native custody.
The actual transport must establish its owner reference and recheck those
observations at its effect boundary before relying on this DB primitive.
Ordinary clients remain unselected. The internal local selection below wires
the SessionManager prompt task and synchronous turn-authoring seam; it does not
enroll every client or qualify the host/remote/lifecycle cohorts.

An existing incomplete turn may already have been sent. A producer association,
an absent completion timestamp or a lost native reply does not prove otherwise.
Migration therefore leaves every old handoff null. No update can attach a new
handoff to an existing turn, including through a keyed retry. A retry selected
with native custody must find the original matching consumer handoff; otherwise
it refuses rather than upgrading old history.

`prepare_native_turn_handoff` only observes a handoff already retained by that
original insertion. It checks the current installation/epoch, enrolled frontend,
original producer association, current DB native context and expected prompt.
It writes nothing and does not mint a handoff or send permission.
The separate original receipt reader remains usable after closure.

## Explicit local transport selection

An internal source owner may call `SessionManager.enroll_native_turn_transport`
with its existing installation and enrolled frontend admission. This selects
only an idle local process-owned `AcpClient`, retaining the actual manager,
database, Session, target/cwd, client, Process and SDK connection references.
The child and frontend births are observed through the existing process helper;
the consumer receives a fresh in-memory incarnation. Repeated selection is
idempotent only while that original owner remains current. No inbox
negotiation, ordinary construction, HTTP route or environment setting selects it.
Enrollment refuses while an existing lifecycle handoff is in progress, across
all its awaits through successor launch, seeding and predecessor retirement.
The handoff scope releases on completion, failure and cancellation; an
entry-only selected-consumer check would not cover concurrent enrollment.

New direct, queued-to-turn and keyed turns atomically retain this consumer
through the existing `create_turn` owner. `_run_prompt` carries the original
attempt rather than selecting a replacement client when its background task
runs. `_run_turn_sync` uses the same original admission. The selected client
refuses an unretained direct `send_prompt` call.

The final gate runs after the ACP prompt lock and request-task scheduling, not
just when the caller queues a coroutine. It rechecks the original live
references, process births, native conversation and DB admission, commits the
one-shot started marker, rechecks again, then calls the captured SDK connection
without an intervening await or replacement lookup. These bounded local DB and
birth observations follow the existing synchronous custody owner; no lock is
held while awaiting the SDK reply, and no helper process, scan or service is
introduced.

A reply to that exact ACP request records native acceptance only. It does not
invent an attributed user-message consumption observation. Failure or
cancellation after final admission retains unknown outcome. A lost begin reply
leaves the original started marker outstanding and cannot grant another send.
Original reporting remains valid after epoch closure or native replacement.

Host, remote, command/provider and private idle-continuation transports refuse
selection. Selected transports refuse implicit resume and lifecycle handoff
rather than downgrading a replacement/successor to ordinary custody.
Auto-handoff is ineligible for this selected cohort. Creation, start/resume,
background/host/remote custody and installer binding remain separate gates;
this internal opt-in is not an installation-wide activation mechanism.

## One-shot final admission and original outcomes

`begin_native_turn_handoff` repeats the installation, current open epoch,
producer association, exact original consumer, DB native context and prompt
checks under the existing SQLite write transaction. A true return is for this
invocation only. The original started marker commits with `synchronous=FULL`
before returning; a second invocation cannot receive true, even after process
exit or a lost first reply.

The interval between that commit and the native call is outstanding
custody, not evidence that the call did or did not happen. Closure can refuse
a not-yet-admitted effect, but cannot declare an admitted attempt settled.
Unknown acceptance never becomes a retry, another turn or a new conversation.

`record_native_turn_handoff` records `native_accepted`, attributed `consumed` or
`unknown` against the original consumer, turn and handoff ID. Reporting remains
available after epoch closure or DB native-context replacement. Acceptance and
consumption have separate retained timestamps; neither certifies semantic
completion. A completed turn cannot erase or settle the handoff.

`read_native_turn_handoff` returns original metadata and outcome timestamps,
never prompt text or fresh send permission. Prepared passive reads perform no
initialization, enrollment, schema repair or event-writer startup.

## Storage and qualification limits

Schema 34 adds nullable fields to the existing `turns` rows, not another queue
or operation ledger. Normal writable migration prepares exact guards before
publishing the version. Passive adoption and already-current writable opens
refuse missing or changed fields/guards instead of reconstructing history.
The guards preserve original turn identity, prompt, delivery key and keyed
receipt, forbid retrospective handoffs, and retain one-shot/outcome observations.
All custody mutations use the existing full-durability transaction owner and
restore the prior connection policy.

Full inventory includes every original turn's native handoff and outcomes,
including terminal and unknown work, and rejects malformed associations. It
continues to report native settlement, other producer cohorts, host/remote
custody and target compatibility as unqualified. No partial storage observation
acknowledges complete runtime closure or target restoration.

The bounded development tests exercise actual SQLite serialization, normal and
adopted owners, frozen legacy migration, strict passive refusal, original keyed
receipts and a real process exit after one-shot admission. These are source
controls without a native model conversation. Additional source controls use
the actual ACP SDK over stdio to a synthetic child, with independent SQLite
readback before receiving the exact prompt, including an actual child exit
without a reply. They do not launch a model or qualify an installed consumer.
Attributed consumption, full cohort enrollment, platform qualification and
guarded installer restoration remain separate open gates.

The current HTTP-50 source capture records schema 34 through actual receiver
observations in both fixture installation modes. Its HTTP generation and
capability floors are unchanged. The existing capture owner refreshes only its
owned current packets while retaining historical provenance; this does not
convert older captures into native-turn qualification.
