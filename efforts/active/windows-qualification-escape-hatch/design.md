# Focused Windows request and result flow

This is the agent-recommended realization of the
[Windows qualification escape-hatch effort](README.md), not an implemented
service contract or an authorization to change another execution lane.

## Admission and execution

Use one `workflow_dispatch` workflow controlled by its protected default-branch
revision. Check the original actor and the triggering rerun actor against the
repository owner. Do not hardcode a personal account or imply that GitHub has
a native per-workflow Maintain-only dispatch ACL.

The repository-owner-only policy is deliberately narrower than collaborator
admission. Unknown identities, organization ownership without an explicitly
supported owner principal, non-control refs and invalid requests fail visibly.
Authorization precedes candidate execution. The Windows job has only the
permissions needed to read public source and publish its own run artifacts,
not repository-write credentials or deployment secrets.

Run one disposable hosted Windows worker per admitted request. There are no
push/PR/schedule triggers, per-request matrix fan-out, self-hosted registration,
new daemon, or global pending-slot concurrency setting that silently replaces
another request. Ordinary GitHub capacity limits remain separate.

## One transport ref, exact source

Already-published candidate commits need no staging. For local-only commits,
use one reserved append-only ref, preferring a dedicated non-branch namespace
where GitHub supports it. If the supported provider requires a branch, use one
fixed service branch, never one branch per request or per caller.

A transport envelope retains both the previous transport tip and the exact
candidate commit. Its tree is the candidate tree; it does not merge code or
rewrite the candidate. Creating the envelope does not switch, amend, reset or
modify the caller's branch, index or working tree. The requested candidate SHA,
not the envelope or moving transport tip, is the test identity.

Update the shared ref only by fast-forward. A competing update gets a bounded
read/rebuild/retry, not a force push or a repository-wide lock. A new request
cannot make an earlier requested commit unreachable. Ref identity and
transport format are checked before appending; an unrelated existing ref is
not adopted or deleted.

Only this one transport ref is managed. Its history intentionally retains
submitted public source; there is no automatic history rewrite or unrelated
branch/tag sweep. Normal source contributions still land through their own PRs.
The transport ref is never merged into a contribution or default branch.

Use the existing repository-scoped GitHub identity and Git credential owner.
Do not switch the global account, copy credentials into requests, or create a
repository/fork to move test input.

## Typed request and focused selection

The request contains a version, opaque public request ID, exact source SHA,
optional transport envelope identity, and a typed selection. It contains no
machine paths, personal configuration, credential data or arbitrary shell.
Publish the controller's request/result contract support before callers use
it. A caller reads the selected protected controller's support declaration;
an absent or incompatible provider refuses before source staging or dispatch.
Do not infer support from the caller's checkout or a package version.

Default plugin requests require test files and may add the canonical runner's
`-k` expression. Tool-test requests are similarly file-scoped and contained.
Broad suites require a distinct explicit choice; they are never the result of
an omitted selector. Native/explicit-tier selections retain their existing
eligibility and prerequisite rules.

Named indivisible packets, initially the existing Bridge HTTP-52 capture and
its independent verification, call the canonical producer rather than
reconstructing tests or stitching partial results. The source-owned profile
retains its group, item, process, memory and aggregate limits. In particular,
managed test-environment preparation remains inside an existing aggregate
clock. An outer workflow timeout reserves room for checkout and reporting;
it does not enlarge the packet's acceptance deadline.

The first implementation should reuse the existing Python/toolchain setup and
dependency-seasoning owners. Do not add a new package manager, raw command
executor, portfolio registry or background worker.

## Local client and unknown effects

Provide a source-owned CLI for submit, status/wait and result retrieval. Check
the candidate/repository/identity/selection before publication. Persist the
existing request and any attempted transport identity before its remote effect.

A submission returns or resolves one Actions run identity. Correlation uses
the original request and source, not whichever recent successful job happens
to exist. An ambiguous dispatch does not create another request or repeat a
possibly accepted run. Status/wait resumes that same request; actual transport
and dispatch uncertainty is reported rather than converted to success.

No client silently applies a returned patch, publishes source, merges a PR or
retire another caller's worktree.

## Truthful results and useful failure evidence

Return the requested and actual source SHA, request and run identity, trusted
workflow revision, resolved selection, actual test identities/outcomes/skips,
worker OS/Python/toolchain and phase timings. Retain bounded named progress
incrementally so a deadline does not erase the last started operation.

Publish a small result manifest, available JUnit, logs and any canonical
generated evidence as run artifacts. Download and validate the expected
request/source/selection/artifact scope before using those results locally.
Results are data, not a privileged local executable or permission to apply
arbitrary files.

An empty collection, missing required phase, skipped prerequisite, unsupported
native feature, timeout, malformed artifact or source mismatch cannot become
an unconditional qualification pass. A green workflow does not replace the
specific packet's acceptance checks, required PR CI or code review.

The hosted Windows image proves the selected source and consumer on that
image. It does not establish managed-desktop visibility, productive session
migration, installed fleet adoption, or every native Windows image.

## Delivery order

Land the reviewed plan first. Then deliver one actual focused source-submit /
Windows execution / artifact-return flow with its tests and usage guidance.
Qualify ref reuse, failures and the normal default-branch publication path
before declaring the capability ready. Only then use it to discharge the
Driver effort's still-open qualification gate.
