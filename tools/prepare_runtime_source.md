# Prepare a merged-source runtime payload

`prepare_runtime_source.py` materializes one runtime plugin from an exact
merged Git commit into an installer-owned location outside its source
checkout. It prepares first-party code; it does not install a runtime, update
Copilot, select a marketplace, or change service supervision.

The initial native consumer is Agent Bridge's Windows and POSIX installer.
Other runtime plugins receive the shared identity helper but do not yet
consume source-qualified payloads. Manager source selection and the local
plugin-deployment gate are separate effort slices.

## Inputs and invocation

Run with Python 3.11+ and Git from a checkout containing this tool and its
canonical libraries:

```text
python tools/prepare_runtime_source.py --source <clean-checkout> --commit <full-commit> --plugin agent-bridge --output <outside-checkout>
```

| Argument | Meaning |
|----------|---------|
| `--source` | Exact standalone checkout root; linked worktrees are refused. |
| `--commit` | Full selected Git commit, reachable from the fetched source branch. |
| `--branch` | `dev` by default; `main` is also accepted as the fetched branch. |
| `--plugin` | One runtime plugin identifier under `plugins/`. |
| `--output` | Preparation root outside the source checkout. |

The caller owns source acquisition and authentication. The tool never fetches
or changes refs. Modified, untracked and ignored working files are refused;
inherited Git repository/configuration selectors cannot redirect the probes.
The selected commit is archived by object identity, not copied from mutable
working files. Source admission is checked again before publication.

## Prepared payload and identity

The canonical materializers expand file references, recursive first-party
library references, installer-engine assets and packaged launch assets.
Package versions and pending changefiles are unchanged. A payload with
unresolved materialization or preexisting deployment metadata is refused.

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

The prepared payload's own installer is the installation entry point. Agent
Bridge validates its plugin/package identity and assembly bytes before using
the source-qualified slot, and revalidates that selection before manifest
publication. Its deploy manifest preserves `source.version` as the original
package version and adds `runtime_version` plus `runtime_source`.
When running from an attributable source slot, Bridge advertises the
qualified build through its existing operational version field while the
installed distribution metadata remains at the original package version.
Different source hashes are selections, not lexicographically ordered release
versions; serving reconciliation uses the existing guarded cutover.

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

The credential-free `merged-dev-runtime` clean-room scenario exercises the
actual installer from a merged source commit, then checks the installed
command and vendor imports with the source checkout unavailable. It also
checks live authenticated health, routed process identity and the running
marker against the selected source generation.

## See also

- [Install contract](../docs/install-contract.md#source--where-the-installer-runs-from-no-flag)
- [Versioned runtime](../libs/versioned-runtime/README.md)
- [Clean-room runner](clean-room/README.md)
- [Source-managed runtime effort](../efforts/active/source-managed-runtime-installs/README.md)
