# Resource obligations — CodeSpace at-rest cleanliness

Part of the `resource-obligation-settlement` effort (fabric-wide overview:
[`../../../docs/architecture.md`](../../../docs/architecture.md#resource-obligations--accountability);
umbrella dotfiles#1081). This documents agent-codespaces' role: deciding when a
**borrowed CodeSpace** is safe to settle so a borrowing worktree may finalize.

## at-rest ≠ deleted

A CodeSpace reaches **at-rest** when its *work is safe* — merged or off-box —
**not** when it is deleted. An at-rest CodeSpace may keep running; its claim can
be *released* (freeing it for the next borrower) without the box being destroyed.
`at-rest` is a property of the resource; `released` is a property of the claim.

## The cleanliness predicate (`cleanliness.py`)

Pure decision logic + a shell probe builder + a defensive parser (mirrors
`fence.py`):

- **`GitCleanliness`** — the workspace's git safety signals: `known` (could the
  probe evaluate the repos at all — `False` is the conservative "cannot prove
  safe"), `dirty`, `ahead`, `unpushed_branches`.
- **`is_git_clean(gc)`** — a *known* verdict with no uncommitted changes, no
  local-only HEAD commits, and no branch carrying local-only commits.
- **`at_rest(gc, in_flight)`** — `is_git_clean` **and** no host-side dispatch
  still driving the box. A conservative AND: either signal unmet keeps the
  obligation `active`.

The caller settles the obligation (`agent-worktrees claims settle <cs-ref>` +
the lease `--disposition` mirror, see *Wiring* below) only on a *definitive*
at-rest verdict. Anything the probe cannot determine reads as **not** at-rest, so
an un-probeable CodeSpace stays an active (blocking) obligation rather than being
settled blind.

## The read-only probe (`probe_command` / `parse_probe`)

`probe_command()` builds a **read-only** bash command run inside the CodeSpace
over the existing SSH channel; it prints `OBLIGATION_PROBE=1` plus aggregated
`DIRTY` / `AHEAD` / `UNPUSHED_BRANCHES` `KEY=VALUE` markers. `parse_probe`
requires the `OBLIGATION_PROBE=1` marker to trust the result (missing marker →
`known=False`). `probe_cleanliness(manager, name)` runs it degrade-safe (any exec
failure → `known=False`).

### Behaviors hardened by the real-CodeSpace spike (2026-08-08)

Spike-testing against a live dev6 CodeSpace surfaced three defects in the shell
probe (the pure predicate logic was already correct). The current probe:

1. **Interpolates the workspace glob unquoted** (with `shopt -s nullglob`). The
   glob must **not** be `shlex.quote`d — a single-quoted `'/workspaces/*'` makes
   the shell treat `*` literally, so it matches nothing and the probe reports
   `known=False` on *every* real CodeSpace (a silent, total failure). The glob is
   a trusted, code-supplied value, never user input.
2. **Scans every repo under the glob** (aggregating: any dirty/unpushed ⇒ not
   at-rest), not just the first. A borrowed CodeSpace holds both the scaffold
   repo (e.g. `odsp-web-codespaces`) and the actual work repo (e.g. `odsp-web`);
   stopping at the first would hide unpushed work in the other.
3. **Detects unpushed work with `git rev-list --count HEAD --not --remotes`**
   (commits reachable from HEAD that exist on **no remote**), plus per-branch
   `<branch> --not --remotes`. This is well-defined even when the branch has **no
   upstream** — a common CodeSpace state where `@{u}..HEAD` errors to `0` and
   would falsely read clean.

The marker contract and `parse_probe` are unchanged; only the shell construction
was corrected.

## Wiring (shipped)

- **Kernel:** `cleanliness.py` predicate + probe.
- **Probe hardening (spike-driven):** the three fixes above.
- **Journal on borrow:** on CodeSpace borrow, `coordination.journal_obligation`
  shells `agent-worktrees claims add codespace <name> --owner-ref <holder_ref>`
  onto the borrowing worktree (resolved by the qualified holder-ref, **not** the
  daemon's cwd project).
- **Settle + mirror on disconnect:** on ssh disconnect
  (`_settle_codespace_on_disconnect`), `probe_cleanliness` runs and, on a
  definitive `at_rest`, `coordination.settle_obligation` flips the borrowing
  worktree's **local** ledger claim to `at-rest`, and
  `coordination.mirror_disposition` writes the same disposition onto the
  **CodeSpace's exclusion lease** — `lease renew codespace <name> --token <token>
  --disposition at-rest` (the L2 fencing `token` is read from the local L1 store
  via `lease.lease_token_for`). Best-effort: a no-token / degraded mirror is a
  no-op and never perturbs the disconnect.

### Why the lease mirror matters (cross-machine + missed-settle)

The local `claims settle` only clears the obligation on the **borrowing
worktree's own machine**. The lease `--disposition` mirror makes the settled
verdict **cross-machine visible on the shared exclusion lease**, which is what
lets agent-worktrees' never-wedge reclaim sweep (`sweep.lease_disposition_of` →
`obligations.from_context`) settle a **stale** `codespace` obligation that the
normal disconnect hook never cleared:

- an owner on a **different machine** (the local settle can't reach its ledger),
- a **missed settle** — a crash after a clean disconnect, or a **bridge-driven**
  CodeSpace dispatched without an `agent-codespaces ssh` session (so the
  journal/settle hooks never fired locally).

In all these the shared lease is the single source of truth: whoever settles
writes the disposition once, and any machine's sweep reads it to reclaim its own
stale claim. A settled disposition (`at-rest`/`released`) proves the obligation
dischargeable; an absent/`active` mirror is spare (never reclaimed on a guess).
`release_claim` tombstones the lease (an absent lease reads as spare —
conservative), so release relies on the local immediate-release cascade at
finalize. (Effort `resource-obligation-settlement`, dotfiles#1081 — complete.)
