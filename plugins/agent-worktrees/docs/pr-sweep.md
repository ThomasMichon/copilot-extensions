# One-shot repository PR sweep

`pr-sweep <owner/repository>` reads a registered repository's complete provider
open-PR census and joins it to worktree observations. It is an on-demand report,
not a watcher, merge decision or claim-settlement command.

Use the exact agent-worktrees command from the session command catalog. Local
mode needs no SSH or fleet service:

```powershell
& '<catalog argv[0]>' pr-sweep example/widgets --json
```

The GitHub inventory uses its native paginated provider transport and the
repository's own credential binding. An unavailable scoped credential or a
failed/malformed later page makes the provider inventory unknown, never zero.
Account lookup is host-qualified, including registered Enterprise repositories.
An explicit repository token or that host's existing CLI credential must pass
the selected account's identity check before the repository census is read.
Other providers currently report unsupported inventory capability explicitly.
The existing GitHub bulk-completion operation remains unsupported; this read
capability does not enable `pr-merge --all`.

## Inspect and select cells before execution

```powershell
& '<catalog argv[0]>' pr-sweep example/widgets --fleet --list-cells --json
```

This reads configuration only: no provider census, SSH probe or worktree
observation. It lists the exact machine/environment selectors, including
configured unavailable cells. Configuration and `ssh_ready` describe a route,
not current reachability or permission to use it.

`--fleet` uses existing machine topology. `--include MACHINE/ENV` and
`--exclude MACHINE/ENV` repeat; exact excludes win and are applied before cell
execution. Unknown selectors fail before any provider or cell read. Preserve
the operator's existing holds with these selectors; never substitute another
environment, route or owner because a selected cell is unavailable.

Remote calls additionally need the selected cell's **actual absolute catalog
command**, supplied in a caller-owned JSON object:

```json
{
  "worker-a/windows": {
    "command": "C:\\commands\\agent-worktrees.ps1",
    "project": "remote-host-a"
  },
  "worker-b/linux": {
    "command": "/commands/agent-worktrees",
    "project": "remote-host-b"
  }
}
```

These are synthetic paths. Obtain real values from the admitted target's command
catalog, not from PATH, a guessed installation or an installed-payload scan.
`project` is the command owner's registered project on that remote cell,
not the caller's project name. `--commands` is invocation input, not a second
topology registry or a credential store. Missing commands remain visible as
unsupported cells. Alias-only machine entries retain their declared execution
platform; an unknown environment remains unavailable rather than guessed.
Declared profiles without an SSH alias remain visible as unavailable cells.
Selected-machine aliases resolve through the existing topology authority;
another machine or environment is still rejected.
Legacy response hostnames are accepted only when the topology unambiguously
resolves them to that selected machine/environment.

```powershell
& '<catalog argv[0]>' pr-sweep example/widgets --fleet `
  --include worker-a/windows --exclude worker-b/linux `
  --commands .\cell-commands.json --author author-a --json
```

Each selected remote cell uses one existing configured SSH connection and one
batched `claims find pr --repo ... --state all --sweep-observation --json`
read, with first-use provisioning disabled. The established non-launching
claims namespace rejects the new option on older clients; the caller does not
send an unknown launching verb or retry through another route. This internal
observation option refuses mutating claim actions/options. Ordinary local
`claims find pr` behavior is unchanged.

`--timeout` bounds each caller-owned remote SSH process and its local
descendants; it is not a production deadline change or proof that a failed
remote read completed. Failure remains unknown and is never replayed
automatically. Local observation is synchronous. No installation, login,
service, recurring worker, claim release or global account switch is performed.

## Interpret the result

Version-1 JSON uses schema `copilot-extensions.repository-pr-sweep`.

- `provider` records complete/failed status, authority, observation time and
  the all-author census. `prs: null` means unavailable, not no PRs.
- `cells` records selected, excluded, unavailable, unsupported or failed
  coverage independently of the provider result. `--list-cells` instead
  returns operation `list-cells`, with no PR inventory.
- `prs[].claimants` contains observed unsettled transitive PR claims, with
  original project/root, full IDs and paths. `prs[].candidates` contains only
  tracked metadata; it is not promoted into held-claim or live-owner proof.
- `associations` retains matching non-open claims and candidates separately.
  Not being in an open census does not distinguish merged from closed.
- `summary` reports count, oldest and unclaimed-by-observed-graph count.
  `--author` filters metrics/presentation, not the retained all-author census.
  Nullable author/head/title metadata stays explicit. If an author is unknown,
  the author-filtered total and oldest PR are unknown rather than falsely
  definitive; the known-match lower bound and complete all-author count remain
  available.
- `complete` describes observation coverage only. Excluded/unavailable cells,
  unreadable records and unresolved subtrees keep it false. It says nothing
  about review, required CI, native acceptance, process liveness or merge rights.

Root source freshness uses the read cohort's start: rewalking records cached
within the same invocation never renews their timestamps. Provider, source-read,
root and received times remain separate; the report is not an atomic snapshot
across a forge and machines. No timestamp or disappeared row clears a claim.
Historical record aliases use the receiving project's existing record-scoped
locality proof, without rewriting stored machine labels. Descendant traversal
stays within that invocation's admitted machine/environment snapshot.
Mixed historical/current labels resolve only against those admitted records.
Malformed tracked candidates add root-scoped errors without hiding valid peers.
Historical associations and monthly retention belong to the caller's own report,
not this stateless one-shot operation.

Exit `0` means the requested census and all described cell observations are
complete, `1` preserves a partial result with errors/coverage, `2` means invalid
configuration/arguments, and `130` means cancellation.

Human-readable fields escape terminal controls and bidirectional formatting;
JSON retains the original data using escaped JSON strings. An invalidly encoded
remote response fails only that cell, preserving the remaining coverage reads.
