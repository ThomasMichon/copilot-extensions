# Machine Alias Compatibility and Locality - Request Record

This is the public-safe operator intent record. Concrete private identifiers
and deployment traces are redacted to generic placeholders; the surrounding
technical requirements are retained. It is not a claim that the original work
started after an effort review.

## Original compatibility request

> The switch to treating [the host's WSL environment] as its "own" machine,
> e.g. `example-host-wsl`, is causing failure of worktree creation and other
> things. We still need to recognize that `example-host-wsl` is equivalent to
> machine:"Example-Host",environment:"WSL" for various operations. Basically,
> when a machine has `-wsl` as a suffix, decompose it back to the hostname and
> environment tuple. The Worktree Manager should still group "example-host"
> and "example-host-wsl" together, and worktree creation and resumption still
> needs to work with the machine for a worktree being "example-host" and it
> having a separately-tracked "environment" parameter.

> "Example-Host" still is a single, physical machine, with two "logical
> machines": "example-host" and "example-host-wsl", which are differentiated
> by Environment.

## Execution and completion decisions

> Keep driving this through to release and deployment

> We'll need to drive prerequsite PRs through to resolution

> Your goal is to ensure that every worktree on Windows or WSL on this
> machine can properly resolve

> Foxus on driving the fix to deployment so we can launch and resume
> worktrees again on this machine

The operator authorized taking over the existing identity repair and finishing
its already-existing guarded publication-recovery prerequisite, rather than
opening competing replacement repairs. Exact effectful recovery previews still
received separate operator confirmation.

## Historical aliases in every slot

> We need to be mindful with this change that we pretty much need to handle
> any of a machine's aliases in any slot, from before our regression and also
> during. We're going to have
> `Example-Host`
> `example-host`
> `example-host-win`
> `example-host-wsl`
> `Example-Host (WSL)`
> etc.
> So the review is warning you that we need to be thorough about inserting
> machine-map handling pretty much everywhere a machine name is likely
> to appear.

## Preferred name, local source and effort capture

> [On another adopting machine], when a machine's hostname differs from its
> preferred name (like "Example-Host" and 'example-host'), the Worktree Manager
> loads and highlights the "preferred name" (e.g. "example-host") but then
> show no worktrees, and an X for status. This suggests it's trying to resolve
> the current machine via SSH loop-back and then failing, because it doesn't
> recognize the tab entry as the current machine itself. This is probably
> related to our work here. Check whether we made an effort for this work so
> far, and make on retroactively if not.

The coordinator found references in existing campaigns but no dedicated effort
or binding for this compatibility/locality deployment objective. The operator
approved the slug `machine-alias-compatibility-and-locality`.

## Deployment authorization reaffirmed

> Keep driving until all worktrees across Windows and WSL resolve; you have
> my full approval for all deployments on Windows and WSL and other
> [adopting] machines

This authorizes the deployment work, not bypassing the target's contribution
flow or changing unrelated ownership and safety requirements.

## Separate deferred request

Blank attribution-only update comments are tracked separately by #6815.
The operator explicitly retained worktree deployment as the first priority.
That separate request does not expand this effort's completion gate.

## See also

- [Canonical effort](README.md)
