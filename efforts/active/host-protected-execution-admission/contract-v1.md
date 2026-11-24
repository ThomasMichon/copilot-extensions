# Proposed host execution-admission contract v1

Back to the [effort](README.md) and [validation plan](validation-and-delivery.md).

**PROPOSAL ONLY.** The names, fields, tables, operations and capability below
do not exist by virtue of this document. Implementations must not advertise
support until the provider and corresponding effect-side guards are delivered.
No numeric host policy or deployed authority is selected.

## 1. Owner and supported guarantee

Dispatch's selected execution-admission owner serializes the protected budget
in its existing native queue store. Worktrees and Bridge retain workspace,
launch, native-incarnation and outcome authority. The topology controller is
an intent/consumer adapter, not a mutable capacity authority.

The admitted resource is a declared **native execution unit**, not a process
count or a conversion from free CPU/RAM. One native conversation/body and its
accounted descendants have one stable execution identity; policy must explicitly
describe any different weighting. A cold retained body consumes execution
headroom again when its native resumption is admitted. Retained custody alone
is not a running unit, but unresolved launch/send/retirement still holds its
accountable units. Do not copy a reservation-state shortcut over the native
owner's existing accountable/unknown classification.

V1 can guarantee protected capacity only for a complete, enforceable
participating host/environment scope. Its result is not a physical-resource
guarantee against arbitrary operating-system programs or an autonomous pressure
controller. CPU/RAM/disk, venue authority, spend and other admission constraints
retain their separate owners and may independently hold an operation.

## 2. Policy and cohort inputs

Proposed `HostExecutionPolicyV1` is loaded through Dispatch's existing
configuration/policy owner, not supplied by an untrusted task or topology JSON:

| Required field | Meaning |
|---|---|
| `version: 1` | Semantic contract version, independent of package version. |
| `scope` | Canonical native machine/environment and selected admission authority identity. More than one conflicting authority holds the scope; no election or fallback. |
| `policy_revision` | Positive native revision plus its attributable configuration source identity. |
| `total_units` | Positive host budget from the actual policy owner; null/unknown is unsupported, never unlimited. |
| `interactive_free_units`, `review_free_units` | Independently protected additional free headroom required after general/trial admission. Required nonnegative integers; missing is unknown, not zero. |
| `class_policy` | Trusted classification and any stricter class budgets/reserves. Task labels cannot self-grant interactive/review privileges. |
| `participants` | Required native producer/launcher/accounting adapters and their supported contracts. A configured name alone is not proof that its executing guard participates. |
| `coverage_policy` | Which entrant classes are included and which positive native evidence establishes completeness/freshness. Unsupported or uncovered entrants hold host-wide admission. |

The exact configuration-file encoding belongs to the reviewed provider change;
this semantic record is not a new user-authored authority manifest. Policy
publication must reject an enabled scope lacking a supported writer fence or
the required participant coverage. Changing policy does not stop existing work.
Resource scope describes the actual execution host, not another qualification
of Git checkout membership or Copilot session/worktree identity. It must not
reintroduce copied machine/platform metadata as authority over those records.

Proposed `HostExecutionCohortV1` is derived by the native owner from retained
admissions and qualified native observations, with `scope`, `cohort_revision`,
policy/source revisions, accounted identities/classes/units, unresolved effects
and explicit coverage gaps. A caller's `complete: true`, timestamp or digest
cannot establish completeness or authority.

Finite pools alone are insufficient. Relevant standing, interactive, review,
direct Worktrees/Bridge and external/manual entrants must be accounted for and
controlled at their real admission boundaries, or the result explicitly reports
`coverage_incomplete`/`unsupported_participant`.

**Unmanaged limitation:** observing a direct `copilot` process does not fence its
next launch. An ungoverned launcher cannot be certified by polling or an owner
label. Either a real existing owner makes that path participate and admits its
retained execution, or V1 refuses the host-wide claim. A narrower scope can be
reported as narrower evidence, never substituted for a host-wide requirement.
There is no automatic process adoption, shutdown or shell interception.

## 3. Proposed callables and transport

The implementation adds `HostExecutionAdmissionV1` semantics to the actual
`TaskQueue` owner. Proposed synchronous Python entry points are:

| Callable | Effect and result |
|---|---|
| `preview_host_execution_v1(scope, candidate)` | Read-only consistent policy/cohort view; `ready`, `held` or `unsupported`, classified remaining headroom and reasons. No reservation, credential ceremony, service start, projection write or future permit. |
| `admit_host_execution_v1(conn, request)` | Internal transaction helper at actual execution admission. Uses the caller's existing native transaction; atomically returns the original `admitted` result or refuses. No nested independent transaction. |
| `observe_host_execution_v1(scope, execution_key, request_digest)` | Read-only exact original result, or explicit not-retained/unknown; never admit, rediscover another owner or fabricate no-effect. |
| `conclude_host_execution_v1(conn, outcome)` | Internal native conclusion helper; retains unknowns or records exact positive no-launch/terminal evidence before releasing this budget entry. |

Existing finite/standing mutation operations call the internal guards at their
real execution boundary; controllers do not run a mandatory preview RPC before
every ordinary operation. A task assignment/reservation is not execution.

Non-task Worktrees/Bridge execution must reach the same owner without creating a
fake finite task. For that cross-process integration, propose the following
operations in Dispatch's **existing authenticated control transport**:

| Proposed route | Semantics |
|---|---|
| `POST /host-execution-admission/preview` | Read-only preview; sensitive policy/cohort metadata still uses the selected control authority. |
| `POST /host-execution-admission/admit` | Admit one exact native execution request immediately before its launch/resume effect. |
| `POST /host-execution-admission/observe` | Exact original result only; safe after an uncertain response. |
| `POST /host-execution-admission/conclude` | Native owner supplies the exact lifecycle outcome; not an arbitrary capacity-release API. |

These are proposed route spellings for review, not callable current endpoints.
Use the existing control dependency, task-scoped-authority refusal, executing
owner identity, drain gate and transport deadline. Health/data-read access does
not grant mutation authority. No new daemon, auth system, universal handshake
or CLI flag is proposed.

Each body has strict `version: 1`, `scope` and the operation's semantic record.
Unknown request fields, booleans coerced to integers and mismatched scope refuse.
Responses have a stable proposed protocol `dispatch.host-execution-admission`,
`version: 1`, and the operation result; additive response fields are tolerable.
Malformed/missing required response fields leave effect outcomes unknown.
Retain existing authentication status meanings; syntax uses `422`, unsupported
semantics an explicit structured `409`, and held admission a structured `409`
with no launch. A read-only preview can return `200` with `held`/`unsupported`.

## 4. Request identity and native state

Proposed `HostExecutionRequestV1` carries:

- The original native `execution_key`, positive `decision_revision` and immutable
  request digest, selected scope/authority, expected policy and cohort revisions.
- Trusted resource class, declared unit cost and the complete native effect
  reference: existing task/spawn or standing relationship when applicable;
  otherwise the actual original native launch request.
- Actual workspace/project/worktree/creation nonce and expected current-head
  fence where those identities exist. For a new conversation, retain the
  original launch request and later bind only its observed native result;
  do not invent a future session UUID.

Do not allocate a new logical request/key when crossing the resource boundary.
A proposed `host_execution_admissions` table in the **existing queue store**
indexes `(scope, execution_key, decision_revision)` and retains the digest, native effect reference,
policy/cohort revisions, resource class/units, row revision, lifecycle state and
original result. This is native resource accounting, not a second assignment
or task lifecycle database. Existing native owners remain authoritative for
workspaces, standing acceptance and launch/process outcomes.

States are `launching`, `running`, `retiring`, `released`, with a retained
`outcome_unknown` condition that always counts until reconciled. The first
admission commits `launching` **before** the external process effect:

| Transition | Required evidence |
|---|---|
| Absent -> `launching` | Atomic admission and immutable original native effect identity. |
| `launching` -> `running` | Exact native launcher result/incarnation matches the original request and row revision. |
| `launching` -> `released` | Original owner positively proves no launch; not timeout, expiry or missing PID alone. |
| `running` -> `retiring` | Existing native conclusion/retirement begins; budget still held. |
| `retiring` -> `released` | Original native owner proves terminal execution and any admission-specific conclusion fences. This does not release unrelated claims. |
| Any nonterminal + uncertain effect | Preserve state, original identities and units; observation/reconciliation only, never automatic retry or TTL release. |

Repeated immutable admission requests return the **original** result marked
`replayed`, never a fresh permission to dispatch the external launch. A
conflicting digest at the same decision revision refuses. The launch owner's
existing native journal/fence must prove whether the original external effect
was dispatched; neither a duplicate admission nor exact observation starts it.

A previous hold is not an admitted launch. A later legitimate reevaluation
uses the next decision revision under a CAS against that held result, with
fresh policy/cohort revisions and retained history, not a fresh allocation
identity. A decision revision cannot advance past a launching, unknown or
otherwise unresolved original effect. Resumption of an existing body retains
its custody but uses the actual native resume effect's identity, rather than
reusing a terminal launch admission as permission to start again.

## 5. Atomic admission and fences

For general/trial admission, within the native write transaction:

1. Validate selected authority/scope, control permission, active drain state,
   native workspace/effect/current-head fences and trusted class/unit cost.
2. Read current attributable policy and complete accounted cohort. Recheck
   expected revisions; stale or incomplete inputs hold without startup.
3. Count all running, launching, retiring and unresolved accountable units.
   Never release old capacity merely because a reservation TTL or native
   observation expired. Do not count a parent/child process chain twice.
4. Require `total_units - accounted_units - candidate_units >=
   interactive_free_units + review_free_units`, plus all stricter applicable
   class budgets. The two free reserves are disjoint additional headroom;
   they are neither occupied review lanes nor aliases for the same free unit.
5. Commit the original `launching` entry/result and advance the cohort revision.
   No other participating caller can spend those units before external startup.

Interactive/review requests use the same total accounting and their own trusted
class policy. This proposal does not let a general/trial caller borrow a
protected class or choose its own reserve-consumption rules. Any subsequent
protected-class use is a separate native admission: it can invalidate a new
preview, cannot double-spend an already committed execution entry, and grants
no pressure enforcement or interruption of an existing body.

The external launcher starts only from the admitted original result, checks
its selected receiver/current native effect fence at the last supported
handoff, and reports the exact observed result. A policy/cutover change must
serialize with in-flight admission: it cannot erase or reassign a committed
launching obligation. Revocation/conflicting policy retains the original
outcome and holds new work; it never becomes a reason to replay or kill it.

Participant registration must use the existing native authority and executing
contract, not caller assertions. More than one uncoordinated admission store or
a stale/ungoverned launch path is incomplete coverage, not another eligible
capacity provider.

## 6. Capability, storage migration and rollback

Propose an additive descriptor in the running coordinator's existing `/health`
capabilities: `host_execution_admission: {versions: [1]}`. It advertises
implemented semantics, not an enabled policy, admitted cohort or capacity.
No package-version comparison, staged payload or older pool capability
substitutes for this descriptor.

Provider-first delivery must add the native storage/schema and a real
writer/activation fence before enabling a required host policy. The canonical
queue schema/migration owner assigns the next storage revision; this proposal
does not reserve a competing schema number. Frozen older writable-provider
tests must prove that a provider unaware of the required host budget cannot
admit around it. If the existing migration/rollback boundary cannot enforce
that guarantee, activation is unsupported until an accepted provider fence
exists; a new CLI check is insufficient.

With no host policy, legacy operations preserve their documented semantics,
but no protected-reserve guarantee is advertised. With a required policy,
each actual execution operation enforces admission internally or refuses
before effect. A supported old caller may continue only if the upgraded
provider/launcher can supply equivalent enforcement without guessing data.
Unsupported new callers/providers and provider rollback return a specific
pre-effect contract gap; never probe an effect, catch an error and retry a
legacy unchecked path.

Policy/configuration migration and effect authority remain separately owned.
Source merge, release, authorized installation and real cohort acceptance
are distinct; no rollout or native test is performed by this proposal.
