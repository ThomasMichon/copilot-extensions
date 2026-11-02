# Explicit configured-cohort transport

The [pool-publication owner](pool-publication.md) projects the registrar's whole
configured supervisor observation into the existing coordinator catalog.
`PoolCohortPreparation` accepts the actual local `TaskQueue` or a concrete
`DispatchClient`; it does not derive membership from enrolled or reachable
publishers. This is an explicit integration boundary, not a production
configuration publisher or ordinary-lane activation.

## Running-peer selection and routes

The existing `/health` descriptor's `pool_catalog_transport` adds
`configured_cohort_projection_versions: [1]`. Clients require that version,
metadata protocol/version 1 and configured control authority against the
running peer before each new operation. Package versions and the separate
`configured_cohort_fencing_versions` do not establish projection support.
Missing, invalid or unsupported support refuses this feature without rejecting
supported legacy registration or metadata operations. A provider rollback
between negotiation and the route returns an explicit upgrade-required result,
never a legacy-write fallback.

All three routes share the catalog's existing control dependency, including
inherited API authentication and task-scoped-authority refusal:

| Route | Operation |
|-------|-----------|
| `GET /pool-catalog/cohort` | Read the entire retained observation and coordinator cohort revision; scoped query parameters are refused |
| `POST /pool-catalog/cohort/project` | Project one original whole observation under its original coordinator CAS |
| `POST /pool-catalog/cohort/recover` | Read exact accepted history for that original observation/CAS; no mutation |

Both POST bodies contain exactly `version: 1`, integer `expected_revision`
and `observation`. The observation contains `state`, `source_revision`,
configured `revision`, complete `members` and `reason`; every member contains
logical `machine`, intended `execution_machine` and `env`. Unknown fields and
coerced revisions are refused. The existing queue validator checks state,
digest, identity and duplicate-scope consistency before any write, not after
publishing a valid subset. Confirmed-empty, absent/legacy-unconfirmed and
digest-bearing unavailable observations remain distinct.

Responses contain metadata `protocol`, integer `version: 1` and `cohort`,
whose fields are `revision` and `observation`. Recovery alone may return an
explicit null cohort when no exact accepted result exists. A missing field is
not absence. Required fields, integer revisions and the entire observation
are validated; additive response fields remain compatible.
Invalid request shape yields `422`; semantic or stale-CAS refusal yields
structured catalog `409`. Authentication keeps its inherited `401`, catalog
`403` and unconfigured-control `503` meanings.

## Original owner and unknown outcomes

Preparation brackets fresh registrar reads with coordinator observations and
retains the original source path, existing bound coordinator URL, observation
and CAS. It writes nothing. A pending request prevents preparation from
discovering or substituting newer inputs. The URL names the selected client
target, not a new endpoint registry or proof of a particular remote process.

Projection first attempts history-only recovery. A fresh write uses the
existing registrar pointer-writer lock. After the client's running-peer
negotiation, the final boundary rechecks the original source and both owner
targets immediately before POST and again after the response. A changed
target, changed source, malformed reply, transport failure or stale CAS leaves
the same request pending. The existing client network timeout bounds this
explicit synchronous operation; no daemon executor, timer or retry loop is
introduced.

### Coordinator-owned source composition

Ordinary `server.build_app` now supplies its actual `RegistrarSources` to the
coordinator. The same projection route requires a fresh observation matching
that coordinator-local owner, including the whole pointer-file byte revision.
An unrelated supervisor's host-local pointer file cannot replace the shared
projection merely because its member list is equivalent. The running descriptor
adds `configured_cohort_projection_owner: "coordinator-registrar"`; an explicit
`create_app` embedding without a source retains `"explicit-caller"` and its
original transport behavior. Supplying a source requires the embedding's actual
active-route callback. No new caller field, credential, registry or RPC is added.

Fresh source-owned projection requires the active route and an open existing
drain gate. It remains counted through the write and final source recheck so a
cutover can wait for it. Source/queue target drift and mismatched observations
refuse through the existing structured `409` response. Accepted original history
is checked first: a replay or history read can still account for an accepted
write after source loss, demotion or drain, without renewing or rewriting it.

The provider checks its local source immediately before and after the write.
These are optimistic observations, **not an atomic filesystem fence**. It does
not acquire another pointer lock across POST: an existing same-host
`PoolCohortPreparation` client can already hold that exact lock. A managed local
publisher can reuse the existing pointer transaction; cross-host and unmanaged
file-writer atomicity remain an integration obligation, not a claimed guarantee.
A source change after commit yields a refusal while retaining the accepted
original result for recovery.

Construction, health and catalog reads do not project even an absent or empty
cohort. This source-owner binding neither starts an automatic publisher nor
changes ordinary spawning, initial prompts, metadata freshness or allocation.
The eventual automatic composition must use this coordinator-local source for
the whole cohort and the existing per-scope supervisor for effective definitions.
It must not make every host-local master an independent whole-cohort writer.

A matching result is checked against the original observation/CAS, then
confirmed in the original coordinator's accepted history before custody is
cleared. Lost accepted responses can therefore recover after the source or
current projection changes, without another projection, clock renewal,
enrollment or allocation. An accepted historical result does not attest that
the current source is unchanged. An unaccepted original request cannot rebase
onto a newer coordinator revision.

The pointer lock excludes cooperating registrar writers only. It cannot
atomically fence unmanaged file writes across a remote SQLite transaction.
A post-write refusal retains the committed effect for recovery; it does not
roll that projection back. The metadata contract continues to advertise
`configured_scope_membership: false`, `lock_holder_verification: false` and
`atomic_placement: false`. Automatic publication, old-writer exclusion,
ordinary initial-prompt/allocator migration, membership activation and native
qualification remain separate gates of the
[assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md).

## Focused source validation

`tests/test_pool_cohort_transport.py` connects the real client and ASGI routes
to isolated registrar files and SQLite. It covers authority, strict bodies,
whole-state distinctions, the actual pointer lock, original-CAS refusal,
lost/malformed accepted results, post-negotiation and post-write source/target
changes, exact-history recovery and declared/route-level provider rollback.
The existing local preparation, projection, catalog transport and metadata
sender controls retain their original semantics. These are contained source
controls, not installed or native qualification.
`tests/test_coordinator_pool_source.py` also exercises ordinary server composition,
same-host client lock ownership, different host-local source bytes, active/drain
admission, in-flight cutover accounting, source/target changes and accepted
history recovery through the real routes.

See also: [pool publication](pool-publication.md) and
[explicit claim/start](pool-claim-start.md).
