# Prepare a merged-source runtime payload

`prepare_runtime_source.py` materializes one runtime plugin or standalone Manager from an exact
merged Git commit into an installer-owned location outside its source
checkout. It prepares first-party code; it does not install a runtime, update
Copilot, select a marketplace, or change service supervision.

Native consumers on Windows and POSIX are Agent Bridge, Codespaces,
Containers, MCP, SSH, Machines, Pull Requests, Budget Guidance, Worktrees,
Dispatch, Index, Logger and Vault. Manager payload preparation uses its existing
standalone project identity; Manager installation/recovery qualification,
updater integration and the local plugin-deployment gate remain separate slices.

## Inputs and invocation

Run with Python 3.11+ and Git from a checkout containing this tool and its
canonical libraries, or use Manager's shipped
`scripts/runtime-source-tools/prepare_runtime_source.py` with its adjacent
installer-owned runtime helpers:

```text
python tools/prepare_runtime_source.py --source <clean-checkout> --commit <full-commit> --plugin agent-bridge --output <outside-checkout>
```

| Argument | Meaning |
|----------|---------|
| `--source` | Exact standalone checkout root; linked worktrees are refused. |
| `--commit` | Full selected Git commit, reachable from the fetched source branch. |
| `--branch` | `dev` by default; `main` is also accepted as the fetched branch. |
| `--plugin` | One runtime plugin identifier under `plugins/`, or `worktree-manager`. |
| `--output` | Preparation root outside the source checkout. |

The caller owns source acquisition and authentication. The tool never fetches
or changes refs. Modified, untracked and ignored working files are refused;
inherited Git repository/configuration selectors cannot redirect the probes.
The selected commit is archived by object identity, not copied from mutable
working files. Source admission is checked again before publication.
Configured source acquisition and preparation explicitly disable Git's ambient
automatic line-ending conversion, so their clean-checkout checks observe the
same literal source bytes on Windows and POSIX. Repository-owned attributes
remain applicable.

Python callers may supply `prepare(..., deadline=<absolute monotonic time>)`
to share their transaction's deadline with Git probes/archive creation,
construction-lease waits and archive/initial payload-copy chunks. The CLI and
ordinary callers retain their existing defaults. Callers still own whole-task
process containment and source acquisition; this parameter grants no source
authorization.

`runtime_source_build.py` is a repository-only composition over this preparation,
the actual configured-runtime-source owner, the retained native dependency
compiler, governed wheel acquisition and the existing numbered constructor.
It includes declared build requirements in the same native lock, inspects them
before backend execution, and validates its held native feed configuration
before compilation can make package requests. It constructs a separate immutable tools environment,
then uses the existing build frontend offline with owned, deadline-bound
children. Built runtime metadata is checked against static source declarations;
the final source-mode profile excludes build-only dependencies and binds the
actual wheel bytes. Its profile-qualified generation is distinct from the
prepared source identity. No artifact index or source-approval file is required.
The returned context must remain held through installed health; `verify()`
rechecks its actual owner/source policy and byte custody. This validation
composition does not enable production callers, activate a runtime or provide
persistent offline lock discovery.

## Prepared payload and identity

The canonical materializers expand file references, recursive first-party
library references, installer-engine assets and packaged launch assets.
Package versions and pending changefiles are unchanged. A payload with
unresolved materialization or preexisting deployment metadata is refused.
Already-materialized packaged assets are retained only when their bytes match
the selected canonical source exactly; conflicting copies are never overwritten.

Manager's `packaged-assets.json` ships the canonical preparer and its complete
materializer import closure inside Manager's own released payload. Preparation
runs those already-installed tools, never Python code from the selected
checkout. Incoming toolkit files are copied and hashed as data for a subsequent
installation. No sibling plugin or checkout is needed by the shipped toolkit.
Manager's name and package version come from `pyproject.toml`; it does not gain a
synthetic plugin manifest.

The prepared directory contains `runtime-source.json`, recording:

- plugin and original package version;
- full selected commit and Git tree;
- canonical materialized first-party assembly hash;
- a separate source-qualified runtime generation.

The generation retains the package version and adds the full source commit
and an assembly discriminator. It is not the claim-owned editable
`dev-<generation>` mode. The assembly hash covers payload bytes, excluding
the receipt itself and derived build/cache metadata; a runtime package named
`build` inside `src` remains an input.

This is local source/assembly evidence, not release attestation or installation
ownership. Existing dependency policy, installation provenance, construction,
health, activation and retention remain the installer's responsibility.

Publication is serialized through the canonical construction lease. Exact
repeated preparation reuses the existing directory without changing its
receipt. A conflicting or altered prepared generation is refused, never
repaired in place. Temporary archives and materialization work are scoped to
the current preparation and cleaned up on exit.

## Output and installer boundary

Success emits one JSON object with `payload`, `identity` and `reused`.
Failure writes the concrete refusal to stderr and exits nonzero.

The prepared payload's own installer is the installation entry point. Native
consumers validate the plugin/package identity and assembly bytes before using
the source-qualified slot, and revalidate that selection at construction and
publication boundaries. Their deploy manifests preserve `source.version` as
the original package version and add `runtime_version` plus `runtime_source`.
Source snapshots preserve the complete prepared assembly and are reused only
after their receipt matches; they do not reopen the original checkout.
Containers retains its separate dependency profile in the selected generation
(see the [install contract](../docs/install-contract.md)).
When running from an attributable source slot, Bridge advertises the
qualified build through its existing operational version field while the
installed distribution metadata remains at the original package version.
Different source hashes are selections, not lexicographically ordered release
versions. Source-qualified recovery requires an exact current or last-known-good
selection; serving reconciliation uses the existing guarded cutover.

The preparation CLI is currently an explicit validation/recovery surface,
not a replacement for the ordinary unified updater. It must not be treated
as proof that a runtime was activated or a service is serving.

## Validation

```text
python tools/run-plugin-tests.py runtime-source
python tools/run_tests_in_devcontainer.py runtime-source
```

The bounded suite covers admission, vendor materialization, immutable reuse,
concurrent preparation, changed inputs, archive paths and the real native
installer identity/manifest functions. Native Windows additionally observes
two preparation/reuse cycles from a GUI-subsystem parent for visible-console
and foreground changes.
The isolated shipped-toolkit regression prepares and reuses raw and already-
materialized Manager inputs, rejects wrong project identity and conflicting
assets, and deliberately poisons fetched tool bodies to detect accidental
execution.

The credential-free `merged-dev-runtime` clean-room scenario exercises the
actual installer from a merged source commit, then checks the installed
command and vendor imports with the source checkout unavailable. For Bridge it
also checks live authenticated health, routed process identity and the running
marker against the selected source generation. For the other adopters it
repeats the real installer and requires unchanged completion bytes and exact
selection. Dispatch and Vault use no-service modes, Dispatch disables its
supervisor, and Index uses client-only configuration. Those installation
witnesses do not qualify resident-service cutover or Index engine use.

## See also

- [Install contract](../docs/install-contract.md#source--where-the-installer-runs-from-no-flag)
- [Versioned runtime](../libs/versioned-runtime/README.md)
- [Clean-room runner](clean-room/README.md)
- [Source-managed runtime effort](../efforts/active/source-managed-runtime-installs/README.md)
