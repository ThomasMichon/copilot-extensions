# Finite ADO review-evidence export

The explicit source module `agent_worktrees.pr_review_evidence_cli` extends the
existing native ADO reader for the
[reviewed acquisition slice](../../../efforts/active/pr-review-workspaces/acquisition-slice.md).
It emits `agent-pull-requests.review-evidence` version 1 for the successor's
[generation builder](../../agent-pull-requests/docs/review-workspaces.md).
Existing `pr-watch`, status and publication commands are unchanged.
Shared reader endpoint validation now rejects embedded credentials and malformed
authorities/ports before use; ordinary configured HTTPS endpoints are unaffected.

This is **read-only acquisition**, not review eligibility, source preparation,
cache/service activation, submission or rollout.

## Select the source and credential owner explicitly

Use the chosen Worktrees source package with an interpreter that already has
its declared dependencies. No installer is invoked by this module:

```powershell
$env:PYTHONPATH = Join-Path $PWD 'plugins\agent-worktrees\src'
python -m agent_worktrees.pr_review_evidence_cli capabilities
python -m agent_worktrees.pr_review_evidence_cli read `
  --repo 'project/repository' --number 42 --endpoint 'https://dev.azure.com/example' `
  --budget 60 --expected-principal '<provider-principal-id>' `
  --credential-command '<trusted absolute JSON argv from the existing credential owner>'
```

The placeholder is **not** a command to invent or an authority grant. Reuse an
already authorized credential owner. Its command must emit the existing
`fleet-manager.forge-credential` version-1 contract with a matching provider,
endpoint, repository, unexpired credential and `observe` permission; see
[native observation](pr-observation.md). No credentials go in CLI arguments,
evidence, files or diagnostics. If that callable owner is unavailable, the live
lane is unavailable; do not install, mint another identity or fall back to
ambient auth.

`--budget` is a required finite positive operation deadline, shared across all
reads and identity checks. The example's 60 seconds is illustrative, not a
provider/bridge timeout policy. Capabilities are reported by the exact executing
module, independently of package version. An older installation without this
module is explicitly unavailable; normal clients are not automatically migrated.

## Acquisition and account scope

The operation renews typed observation credentials for every request/page
through the existing owner. Each new opaque credential is checked against the
provider's fixed connection-data identity endpoint using that **same** material,
before it reads PR data. Repeated material reuses only an operation-local
identity result; the bounded memory map stores token digests and principal IDs,
not credential bytes. Expiry and forge binding are still checked per request.
A different actual principal, or mismatch with `--expected-principal`, aborts;
the exporter does not silently mix accounts or reuse another cache namespace.
This additional identity IO is included in the caller's budget and measured
duration, not hidden behind a separate issuer or background service.

PR metadata supplies canonical project/repository IDs and must match the
requested PR number. Scope drift during the read aborts. Reviewer and completion
fields come from the observed PR object. Policies are native **evaluations**,
never configuration definitions interpreted as evaluation results.

Iterations, common-baseline iteration changes, all-status discussions and
evaluations use the existing bounded HTTP reader. Iteration changes have their
own `changeEntries`/`nextSkip`/`nextTop` protocol; missing, malformed, repeated or
nonadvancing continuation leaves retained records explicitly incomplete.
Unavailable policies are not an empty complete set. Credential loss aborts the
whole operation rather than becoming a best-effort partial success.

Fresh metadata and iteration reads bracket acquisition. Newly observed head/
iteration evidence is retained while changes keep their original binding;
the generation builder reports mismatched changes incomplete instead of
relabeling old source. A failed final iteration read retains known earlier
records but cannot establish a current range. Unknown initial ranges omit the
change section with an explicit acquisition omission reason. Observation
intervals are local timestamps, not server-clock or atomic-snapshot proof.

## Output and limits

Stdout is one compact JSON value. Exit `0` means acquired, possibly incomplete;
`1` is an explicit sanitized input/provider/credential failure; `2` is strict CLI
syntax failure. Partial records and per-section errors survive. The output
includes measured duration, observation intervals, identity-check count, omitted
sections, `snapshot_atomic: false` and `effects: none`. The normalizer owns
generation consistency and analysis invalidations, not this transport adapter.

The existing byte/record/page limits and shared deadline remain enforced; total
evidence must fit the version-1 8 MiB input budget. Exhaustion is not empty success.
No token, header, raw credential exception or caller path is printed on failure.
Caller-private PR content still requires private handling.

This provider-first expansion adds no ordinary installed-client invocation,
daemon or persistent state. Later slices own service-backed generations, exact
source attachment and durable explicit submission/reconciliation.
