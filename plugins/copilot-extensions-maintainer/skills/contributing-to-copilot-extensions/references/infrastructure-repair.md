# Infrastructure-maintainer source-repair extension

Use only when the operator explicitly adopts this extension and assigns
copilot-extensions installation/runtime maintenance as a core responsibility.
It extends `agent-roles:maintain-agent-infrastructure`; it is not the base role's
default and does not apply to incidental tooling failures during another task.

## Adoption and retained limits

Record adoption in the existing canonical charter, with the original operator
source, target environments, approved repair strategies, source repositories,
publication destinations, experiment/resource limits, rollout authority and
holds. A role title, package enablement, repository access or this document
grants none of those authorities.

Within an adopted source-repair grant, ordinary diagnosis, controlled local
experiments, durable generic source fixes, regression tests, review fixes,
rebases and required issue/PR delivery do not need a new generic permission
prompt. Preserve separately required deployment, credential, elevation,
destructive-operation, agent-custody and review/CI gates. Adoption never grants
another repository's maintenance or permission to patch installed package logic.

## Failed installation loop

1. Reconcile the original updater request and its pending effects through its
   owner. Preserve selected generations, protected consumers and user state;
   a timeout does not prove that nothing started.
2. Read the actual installation mode, launcher, configuration, selected and
   serving generations, native result and relevant logs. Locate the first
   failing boundary. Separate an incoming startup failure from an incumbent
   lock/refusal, readiness-query failure or independent retirement failure.
3. Use a changed, bounded experiment that distinguishes the remaining causes.
   Start with approved recovery strategies. Test source candidates in an
   isolated managed worktree or fixture, never by editing a deployed slot.
   Keep original timing/resource gates; a longer diagnostic deadline or a
   faster refusal does not pass a successful-operation requirement.
4. Implement the durable generic repair through the normal contribution
   procedure. Coordinate on a public-safe issue; keep private host evidence
   downstream. Add focused regressions and exercise the real consumer contract.
   Use fresh-environment/native qualification when that boundary requires it.
5. Complete normal review, required CI, merge and release. Use the authorized
   normal rollout owner after promotion; then retry the original failing
   installation path with changed inputs and prove its actual consumers.
   Return to the charter's remaining acceptance items after each child result.

Follow `visions/installer/README.md`'s authoritative replacement target:
incoming readiness and all current selections establish installation success;
central outgoing retirement is separate. Do not invent an old-runtime,
migration or cache-preservation veto. Necessary user-state compatibility and
actual incoming readiness remain real checks.

## Blockers and completion

An unproved theory is not an operator hold. Failure stops cohort advancement,
not authorized investigation. Do not stop at an error report while another
permitted experiment or repair can resolve it.

When a real boundary remains, retain the exact blocker, evidence, owner,
authorized report destination, missing decision and next useful action.
Continue independent assigned work without bypassing the boundary.
An issue, source patch, passing test, merge, release or unrelated healthy update
does not complete the repair. Complete it only after the failed operation and
consumer checks pass, or an explicit accepted transfer retains that gate.
The standing maintenance role still ends only on retirement or accepted transfer.
