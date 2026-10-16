# Candidate release-lock inputs

This directory currently contains **inert dependency inputs**, not a runtime
bundle, installer, approved lock or published artifact. It advances the release
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

Generation reads the existing canonical project inventory and standalone
consumer registry. It excludes vendored duplicates and follows first-party
runtime dependencies and explicitly selected extras to their third-party
declarations. It preserves target markers and version constraints; it neither
evaluates them against the generating machine nor chooses dependency versions.
The virtual project retains the existing shared minimum-age cutoff. Selected
local-source runtime metadata must match its canonical counterpart throughout
the selected dependency graph, including nested mappings and cycles. A
divergent copy is not silently replaced by a different closure.

| Group | Candidate roots |
|-------|-----------------|
| `core` | Runtime plugins and registered standalone distributions, except Index host/engine roots |
| `index-service` | The standalone Index service's `native` extra, including its declared store/server closure |
| `index-engine` | The existing separate embedding-engine project |

Python 3.11 is the **tooling** prerequisite, not an imposed runtime floor.
The generated project covers the declared group ranges, with each group's
first-party Python constraints retained in UV's dependency-group metadata.
The existing Index service and engine Python 3.10 target remains covered.

The candidate core includes Dispatch's MCP and Containers' provider-exec extras
outside Windows ARM64. Other optional capabilities remain opt-in. Unknown
first-party extras, duplicate normalized distribution names, dynamic runtime
dependencies, direct references and unsupported source/extra-marker forms
refuse generation rather than producing an incomplete closure.

The already-wired install-contract test suite collects the input regression
class and checks the generated file against the real repository. No workflow
change or new publication/admission path is required.

**Not implemented here:** resolution, lock approval/selection, exports, feed
compatibility proof, artifact publication, shared runtime construction or
deployment. Those remain with the existing
[artifact effort](../efforts/active/governed-python-artifact-promotion/README.md)
and its proposed exact-profile contract. Candidate inputs are not evidence of
an approved, available or executable dependency closure.
