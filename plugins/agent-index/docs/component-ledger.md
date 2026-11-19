# Native component ordering evidence

`store.component_ledger` is an opt-in Python dependency for ordered component
application. It changes no default reader or writer and enables no receiver,
executor, checkpoint acknowledgement or live adoption.

`initialize_component_ledger(config, store)` explicitly bootstraps the native
`component_ordering_evidence` table only when the configured participating
content/model tables are empty. Existing bindings are verified, never reset;
a missing table, orphan table, unknown binding, incompatible schema, changed
table identity/version or unprepared index refuses access. Existing populated
stores require a separately designed adoption/recovery path, not this bootstrap.

The table lives in `config.lance_dir`, alongside the source-layout receipt and
data included by the existing native-directory backup. Arrow schema metadata
retains a random table identity across native writes, index preparation and
coherent directory copies. A constant-size protocol-1 `component_ordering`
receipt binding records that identity and the ledger owner's witnessed native
version. Generic participating writers update the domain's version map but
**not** this binding: dropping the table or losing tombstone rows cannot become
valid merely because another writer subsequently commits a clean receipt.

## Point reads and subordinate writes

`ComponentLedger(config, store).get(source, path)` reads at most two candidates
through a prepared B-tree on the exact point's digest. It rejects duplicate or
unexpected records. Each immutable `ComponentEvidence` retains `ComponentContent`
(provider version, rendering semantics, fingerprint and deletion flag), opaque
nonempty acquisition `scope` and `policy` identities, and the complete
[raw file projection](file-projection.md). These scope/policy identities must
cover the caller's actual account, endpoint, query and selected policy; this
helper neither derives them nor permits rebinding existing history.

`ledger.put(evidence, expected=prior_record, max_rows=point_budget)` requires an
already-owned source-domain writer. It re-reads the exact prior record, compares
versions with the shared component comparator, and recaptures the current raw
point before retaining evidence. New versions retain the union of current and
previously known chunk IDs; omitting old IDs cannot hide vector remnants.
Equal-version conflicts refuse; matching duplicates and older versions return
`False` without changing the ledger only while the retained raw point still
matches native data. A newer record returns `True`. A deletion record requires
empty canonical rows and empty registered-model points for all retained IDs.
Unknown vector orphans remain outside this evidence, not silently certified.

The ledger uses the existing scalar-index owner and its physical-tail/segment
budgets. Point capture and index preflight must succeed before the ledger
mutation. Its single-row native transition and index update then advance only
its own binding. A failed native effect or binding publication remains subject
to the source domain's dirty/uncertain recovery rules. Read/preparation failures
before entering a writer do not dirty the domain; exceptions escaping an owned
writer retain that owner's existing failure semantics.

`max_rows` bounds point candidates/retained IDs as described by raw projection,
not aggregate payload bytes, model count or index metadata I/O. Ledger history
is not pruned automatically. `ledger.consolidate(max_rows=physical_row_budget)`
explicitly preflights the whole current physical table before entering its
writer, then reuses scalar consolidation and rebinds the resulting native
version under that same writer. Use this operation at the segment ceiling;
generic scalar maintenance cannot advance the ledger's independent binding.

## Application and restore boundaries

These are ordering observations, **not an applied-content certificate**. An
application must still validate its exact durable pending parent and prior
native point **before derived writes**, bind acquired input/rendering and model
coverage to the intended fingerprint, prepare embeddings outside exclusion,
revalidate inside it, and compose content/vector publication, claims,
checkpoints, crash recovery and component read isolation. This helper cannot
retroactively prove those steps or make default readers component-isolated.

A coherent, quiescent synthetic copy of the complete native directory preserves
the evidence at another path without an inode/path identity requirement.
Missing-ledger and dirty copies refuse in native controls. This is not a new
backup implementation or qualification of live backup concurrency, mixed
checkpoint/state restores, missing unknown vectors or an obsolete complete
backup presented as current state. Those remain application/recovery work.

See the [architecture overview](architecture.md) and
[prepared scalar-index ownership](chunk-id-index.md).
