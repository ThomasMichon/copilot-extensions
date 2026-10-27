# Origin-owned worktree allocation provider

This JSON provider separates durable creator responsibility, receiving creation
and optional exact-claim acknowledgement. It uses the existing worktree resource
ledger and workspace-creation journal, not a second allocator or service.
It is the provider foundation for the
[origin-owned allocation pattern](../../../docs/patterns/workspace-allocation-and-session-acquisition.md#origin-owned-remote-allocation).
The ordinary `create --machine/--repo` coordinator, restart-driven asynchronous
linker and Bridge/Dispatch consumer migration are separate work; these provider
commands do not implement or advertise those consumers.

## Source ABI and negotiation

```text
agent-worktrees worktree-allocation capabilities --json
agent-worktrees worktree-allocation <operation> --owner-ref machine/project/worktree --json
agent-worktrees --project destination worktree-allocation-receive capabilities --json
agent-worktrees --project destination worktree-allocation-receive <operation> --json
```

Non-capability operations consume exactly one bounded JSON object on stdin.
Unknown arguments, extra keys, duplicate JSON keys and unsupported schemas are
refused. Capability discovery is independent of package versions; callers must
query the actual selected runtime before depending on this ABI. The response
declares schema `copilot-extensions.worktree-allocation`, version `1`,
`metadata_version: 3`, `lifetime: owned-worktree`, receiving receipt version `1`,
`claim_linkage: non-blocking`, and `agent_launch: false`.

The origin resolves an exact qualified local creator through its registered
project configuration. A session alias, anchor, foreign creator or caller-supplied
ledger path is not a substitute. The receiver resolves its own actual execution
space and project; the origin's local configuration never supplies target paths.

## Origin responsibility

| Operation | Input | Effect |
|---|---|---|
| `reserve` | `request_id`, `target` | Persist protected pending responsibility before effects |
| `inspect` | `request_id` or `token` | Read the original local claim, without remote I/O |
| `bind` | `token` | Observe the original receiver and retain its exact child identity |
| `confirm` | `token` | Request optional acknowledgement of that bound identity |
| `close` | `token` | Observe and retain actual receiving child finality |
| `cancel` | `token` | Seal the original pre-identity receiving operation, then close responsibility |

`target` contains `machine`, `project`, and optional bounded `options` and
`authority`. A request ID is stable per creator: replay retains the same token
and target facts; changed facts conflict. Supported receiving options are `kind`,
`interface`, `origin`, `owner`, `name`, `bound_agent`, `caller_worktree`,
`dispatch_attempt` and `no_pair`. Existing creation policy remains authoritative.

Protected metadata version `3` has lifetime `owned-worktree`, namespace
`worktree-allocation`, and phases `pending`, `bound`, `confirmed`, `closed`.
A pending reference is a canonical opaque `worktree-allocation:w1.` envelope.
After binding, the reference is the existing qualified native child reference,
so normal ownership graphs retain the actual child. The exact creation nonce,
receiving absolute path and branch remain in protected producer metadata.

Remote observations happen outside the origin record lock. Before committing,
the original owner and complete protected metadata snapshot are checked again.
Terminal or frozen owners do not reopen; handoff reservations and ref collisions
refuse mutation. Transfer is not supported by this provider.

## Receiving operation

The named receiver accepts `create`, `inspect`, `confirm` and `cancel`. Its input
contains exactly:

```json
{
  "schema": "copilot-extensions.worktree-allocation",
  "version": 1,
  "origin": "origin/project/parent",
  "request_id": "original-request",
  "token": "opaque-token-from-the-origin-reservation",
  "target": {
    "machine": "target",
    "project": "destination",
    "options": {"no_pair": true}
  },
  "identity": null
}
```

An acknowledged request supplies `identity` with the exact returned `ref`,
`creation_nonce`, `path` and `branch`. The receiver's original operation key
depends on creator and request ID, **not** token: changing a token conflicts with
the same operation rather than allocating another child.

The core reserves the original ID, path, branch and nonce in the existing journal
before Git creation. Its first saved child record includes the foreign creator
and a receipt matching those original facts. The receipt starts in `created`;
`linked` records optional origin acknowledgement. A valid local, original-journal
receipt permits ordinary receiving lifecycle operations in either phase. Neither
phase starts an agent, creates a prompt, or bypasses normal hosting, head, holder,
session or task admission.

Replies include exact request facts, `identity`, `status` and
`allocation_finished`. `allocated` means receiving creation finished, not that
the origin's claim link finished. `confirmed` adds optional acknowledgement.
`pending` and `unknown` preserve uncertainty. A zero CLI exit means the query
ran, **not** that allocation finished; callers must inspect both result fields.
Refusals contain an explicit error and exit nonzero.

## Recovery and conclusion

The receiver serializes each original operation with a strict process-shared
operation lock, separate from the child's short record lock. A concurrent live
creator is never overtaken by an inspector: the latter reports pending.

After an interrupted first save, recovery admits the same record, nonce and
receiving namespace and checks the retained source/pairing configuration.
It resumes the original deterministic companion completion without creating a
replacement primary child. A ready-tail checkpoint permits completion after a
crash immediately before the final journal commit without repeating pairing.
A valid already-stamped pair can retry plugin composition through the existing
pair validator. An unproven partial companion remains blocking, not an invitation
to allocate another pair.

If an older generic writer omits the new top-level receiving receipt, the
receiver can reconstruct it only from the exact retained original local journal,
creator, nonce, ID, path and branch. Generic saves preserve that recovered receipt;
the journal also retains optional acknowledgement. A **present but malformed**
receipt is not repaired or ignored. Changed identity or ambiguous evidence cannot
grant foreign-parent mutation authority.

Actual saved `finalized` or `archived` child state is retained as a conclusion
tombstone in the original creation journal. It can outlive removal of the child
directory and record. Cancellation instead atomically seals a request **before**
identity reservation, fencing a late creator before Git effects. Neither a
missing directory, missing record, transport failure nor conversation retirement
is a receiving lifecycle conclusion.

The current transport invokes only the named receiver over the existing selected
SSH alias/shell with finite caller-owned process custody. It requires no reverse
origin connection and accepts no arbitrary remote executable or caller-written
conclusion. A future transport must preserve the same provider operation.

## Related

- [Disposable producer API](resource-producer.md): distinct lifetime and absence semantics.
- [Retained relationship API](resource-relationship.md): relationship retirement, not child finality.
- [Origin-owned allocation pattern](../../../docs/patterns/workspace-allocation-and-session-acquisition.md#origin-owned-remote-allocation).
