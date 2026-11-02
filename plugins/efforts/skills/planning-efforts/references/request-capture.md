# Validate the request capture

[Planning Efforts](../SKILL.md) owns this required creation/rewrite procedure.

Every effort README write that captures or updates operator intent must be
re-read against the operator's actual words before treating it as complete.
Validate Request, Guiding Intent and Plan; do not paraphrase away a constraint,
soften a firm decision or silently add scope.

- **Validate against the source.** Compare the current round's literal input.
  Flag omitted constraints, softened decisions or unrequested additions back to
  the operator in the same turn.
- **Demarcate agent recommendations.** Mark additions from agent analysis with
  an inline `_(agent-recommended)_` tag or a dedicated subsection. Do not blend
  proposed enhancements with what the operator actually authorized.
- **Accumulate, then summarize.** A short exchange can remain verbatim in
  Request. When several rounds would dominate the README, label the settled
  gist as a summary and move the full exchange to a sidecar.
- **Preserve the sidecar.** Use `<effort-folder>/inception-transcript.md` for
  the complete inception exchange or major direction change and link it from
  Request/Context. Keep the README a navigable map without losing the original
  evidence.
- **Public safety is the one verbatim exception.** Redact real private/internal
  identifiers in public artifacts, not the surrounding intent. Keep precise
  private context in its appropriate private home, without a public link to it.
  The [public-effort rule](efforts.md#keep-internal-specifics-out-of-a-public-facing-effort)
  explains abstraction and one-way references.

Original approval evidence stays distinct from the effort's interpretation.
Use the [Scope Approvals seam](efforts.md#scope-approvals) for upfront grants,
one-time answers and later changes; a paraphrase, plan review or recorded
execution outcome cannot create permission.
