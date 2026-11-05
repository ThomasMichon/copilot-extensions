# Candidate release-lock inputs

`tools/bundle_lock.py` produces **inert dependency inputs**, not a runtime
bundle, installer, approved lock or published artifact. Generated virtual
projects are working outputs, not checked-in universal qualification. The tool advances the release
lock described by the
[Runtime Assembly vision](../visions/plugin-services/runtime-assembly/README.md)
without changing any installation's selected source, profile or runtime.

From the repository root, with Python 3.11+ and the existing `packaging` tooling
dependency:

```text
python tools/bundle_lock.py generate
python tools/bundle_lock.py check
python tools/check-bundle-inputs.py
```

Default generation reads the existing canonical project inventory and standalone
consumer registry. It excludes vendored duplicates and follows first-party
runtime dependencies and explicitly selected extras to their third-party
declarations. It preserves target markers and version constraints; it neither
evaluates them against the generating machine nor chooses dependency versions.
The virtual project retains the existing shared minimum-age cutoff. Selected
local-source runtime metadata must match its canonical counterpart throughout
the selected dependency graph, including nested mappings and cycles. A
divergent copy is not silently replaced by a different closure.

## Explicit group and target projection

The programmatic `collect_inputs` and `render` APIs accept `groups=GROUPS` and
`target_environment=None`. Selecting groups requires roots only for those
groups; canonical inventory validation and the shared root selector remain in
use. With no target context, conditions remain symbolic as before.

An explicit target must contain exactly the standard `packaging.markers.Environment`
fields. Missing facts are never filled from the generating host. Applicable
runtime Python ranges, including prerelease interpreters, are checked before
emitting inputs. Proven-inactive edges are omitted before source lookup;
applicable markers/extras remain in the output, and rendered target facts are
attributed under `[tool.bundle-inputs.target-environment]`.

The optional `root_names` argument selects exact canonical runtime distribution
names through the shared inventory, not arbitrary library roots. A subset records
its selected names under `[tool.bundle-inputs].root-names`; it is not the complete
portfolio core selection. Explicit environment groups must contain every selected
root. Omitting both selectors preserves the ordinary three-environment projection.

The same traversal also follows a project's flat standard
`[dependency-groups].first-party-core` requirements as first-party build inputs.
Those entries must name canonical first-party projects; their runtime requirements,
selected extras, markers and Python ranges contribute to the ordinary third-party
projection. Other dependency groups, including development tools, are not runtime
roots. This projection does not itself embed code, change wheel metadata or admit
an installed distribution set.

For example, from a Python process whose own target facts are intended:

```python
import sys
from pathlib import Path
from packaging.markers import default_environment

sys.path.insert(0, str(Path.cwd() / "tools"))
from bundle_lock import render

candidate = render(
    Path.cwd(), groups=("core",), target_environment=default_environment(),
)
```

This returns an inert string; it does not resolve, write a lock, approve a
source or authorize native compilation. External source mappings are audited
even when no direct selected edge names them. Unknown/transitive-only mappings
and resolver settings that cannot be preserved refuse; a mapping is not
silently treated as a registry dependency. Omission of an external mapping
requires explicit inactive markers across its declarations.

The CLI remains the universal three-group interface above. The real universal
graph currently refuses the qualified local Manager `dbus-next` wheel source;
generation/check neither create nor change output when refusal occurs.
An explicit Windows-core projection can omit that Linux-only edge, but active
Linux and untargeted cases still refuse. This is not qualification or removal
evidence for the existing workaround.

| Group | Candidate roots |
|-------|-----------------|
| `core` | Runtime plugins and registered standalone distributions, except Index host/engine roots |
| `index-service` | The standalone Index service's `native` extra, including its declared store/server closure |
| `index-engine` | The existing separate embedding-engine project |

Python 3.11 is the **tooling** prerequisite, not an imposed runtime floor.
The generated project covers the declared group ranges, with each group's
first-party Python constraints retained in UV's dependency-group metadata.
The existing Index service and engine Python 3.10 target remains covered.
Conditional first-party Python constraints are accepted only when each
specifier already occurs in the group's unconditional constraints. Other
conditional ranges refuse generation: flattening them would incorrectly
narrow the entire group's supported targets.

The candidate core includes Dispatch's MCP and Containers' provider-exec extras
outside Windows ARM64. Other optional capabilities remain opt-in. Unknown
first-party extras, duplicate normalized distribution names, missing project
metadata, dynamic runtime dependencies or Python ranges, direct references
and unsupported source/extra-marker forms
refuse generation rather than producing an incomplete closure.

The existing install-contract test module collects the input regression class,
including real-tree scoped projection and universal-source refusal controls.
The earlier proposed checked-in universal project and positive real-tree
qualification check are withheld until that source can be represented. This
does not relax an existing runtime or publication guard. No workflow change or
new publication/admission path is required.

**Not implemented here:** resolution, lock approval/selection, exports, feed
compatibility proof, artifact publication, shared runtime construction or
deployment. Those remain with the existing
[artifact effort](../efforts/active/governed-python-artifact-promotion/README.md)
and its proposed exact-profile contract. Candidate inputs are not evidence of
an approved, available or executable dependency closure.
