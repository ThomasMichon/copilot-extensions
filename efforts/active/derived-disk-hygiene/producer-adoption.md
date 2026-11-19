# Owned producer adoption

Parent and completion authority: [Derived Disk Hygiene](README.md).
Coordination: #10968; the existing test pilot is [scratch-pilot.md](scratch-pilot.md).

## Goal and scope

Extend the existing allocation and process-settlement contract to selected
repository task producers. A completed invocation must leave either a verified
consumed allocation or a discoverable, bounded disposition. An uncertain child
process result must leave the allocation and its responsibility open.

Source implementation is delivered in #13444 after the plan review in #13436.
The bounded qualification and remaining ownership are recorded below.
This does not complete the parent effort, enable a default, install a runtime
or authorize cleanup of existing data.

## Selected consumers

| Producer | Selected real consumer contract |
|---|---|
| Test | Preserve the opt-in `run-plugin-tests.py --owned-scratch-owner` flow and its existing regression packet. |
| Spike | Execute a bounded, one-off analysis command in claimed scratch and consume its result through standard output. |
| Build | Execute a build-and-check command with its temporary intermediates inside the allocation. Explicit durable build output keeps its existing caller/publication owner. |
| Archive extraction | Extract an explicitly selected input into the allocation, validate and consume its contents before disposition. Preserve the extractor's input/path validation. |
| Report | Produce and consume a transient report inside the allocation. Durable reports use an independently admitted destination; terminal output is not proof of user delivery. |
| Source worker | Create and consume a transient qualification image inside the allocation. Ongoing or editable source work remains in a registered source worktree. |

These are explicit, selected integrations, not universal interception of tool
writes. The implementation and completion record must name the actual commands
and revisions exercised for each row. A synthetic file labelled "build" is not
qualification of the build consumer.

## Architecture

Use the existing Worktrees artifact owner and outbound ledger. Reuse its
capability negotiation, configuration/root admission, identity binding,
interruption behavior, retained disposition and independent cleanup. Do not
add a second allocation inventory or accept caller-supplied ledger paths.

Add one opt-in repository command for bounded disposable producers, provisionally
`tools/run-owned-producer.py`. It runs a literal argument vector, not a shell
expression, through the existing process containment owner. A command receives
its selected allocation as its working directory and through an explicit
scratch argument/environment contract. Child temporary roots stay inside that
allocation. Inherited installation, session and credential state follows the
existing test containment policy rather than reaching the operator's services.

Factor only the shared allocation/disposition portion of the test pilot as
needed. Keep its old entry point and ordinary runner behavior compatible.
The new consumer must not change the test pilot's interpretation of success,
timeout, failure, teardown or unknown descendant cleanup.

Only the integrated containment boundary may settle work after descendants and
cleanup workers join. Do not expose a `work_settled` assertion flag or treat a
zero parent exit, released lock, process scan or elapsed time as settlement.
Missing Worktrees support or coordination authority refuses before child
execution; no bootstrap or unclaimed-scratch fallback is permitted.

Successful disposable output is consumed before exact removal. Failed known
cleanup keeps only the configured bounded producer-owned diagnostic summary,
not arbitrary subprocess output, extracted inputs or recursive log guesses.
Unknown process cleanup retains the complete allocation and open obligation.
Partial physical cleanup remains an explicit retained or unresolved result.

The wrapper owns only its disposable allocation. It does not make arbitrary
external output paths safe, transfer a user file, evict a reusable cache or
preserve execution history. Explicit external outputs need their existing
owner and verified disposition. History and durable results must not be put
in the allocation merely to make finalization pass.

## Contract and compatibility

- Invocation is opt-in. Existing commands without it retain their current
  behavior. Required unit CI must not start a live Worktrees runtime.
- Negotiate the actual loaded artifact capabilities, not a package version.
  An unavailable owner, malformed policy or unsafe root fails visibly.
- Preserve literal command arguments and exit results; define scratch
  substitution explicitly and reject malformed or ambiguous invocation.
- Commands must use foreground child work under the containment owner.
  No detached services, OS tasks, daemon escape or host-state override is added.
- Use no-window Windows creation and the existing POSIX process boundary.
  Preserve identity-qualified cleanup and containment resource limits.
- Finalization accepts a valid retained disposition. A later cleanup is
  separate and revalidates the exact allocation; no automatic sweep is added.

## Vision and pattern reconciliation

This closes the artifact ownership and guided-reclamation intent in the parent
effort and the
[checkout-retirement boundary](../../../visions/plugins/agent-worktrees/worktree-data/README.md#checkout-retirement-is-not-data-reclamation).
It does not change those lifetimes or the domain-owned history contract.
The existing artifact and process owners remain the authorities; this is a
consumer expansion, not a new storage service. Honor independent plugin
availability and the existing service-contract evolution and Windows launch
patterns. No runtime installer or dependency change is proposed.

## Implementation and validation

- [x] Preserve the test pilot through a focused shared-context regression packet.
- [x] Implement the opt-in literal-command consumer with capability/root refusal,
      bounded diagnostics, normalized exit handling and joined cleanup.
- [x] Prove the selected spike, build, extraction, report and qualification-image
      flows through real process entry points; record their exact commands.
- [x] Add focused unit coverage for argument handling and effect ordering.
      Keep actual process, filesystem and native qualification in the explicit
      integration portfolio, not required unit CI.
- [x] Prove native Windows and available POSIX success, command failure, timeout,
      unknown cleanup, partial removal and unsupported-owner cases. Include
      paths with spaces and the applicable link/permission/path-length negatives.
- [x] Run a first-touch fixture with no prior allocation, then a refusal case
      with an incompatible owner. Do not repair or adopt foreign resources.
- [x] Verify ledger disposition and exact remaining bytes after each native
      result. Preserve a reusable-cache sentinel and unrelated sibling identity.
- [x] Update the canonical artifact/test documentation and entry points; run
      focused tests, source guards and the required publication checks.
- [x] Obtain current-head review and required CI, merge the implementation,
      then publish the bounded completion record. Report release and installed
      adoption separately; preserve every remaining parent item and owner.

## Source delivery and qualification

#13444 merged as `8c222798452e02456d96180bdf8b62ff7fa8f5a6`.
The final reviewed head is `ff14f14b42fa04b81942aa5c81e1f85d92240c0f`.
Current-head approval, required CI, resolved review threads and an empty
closing-reference list passed before managed merge. #10968 remains open.
This completion record must clear its normal review and merge before the
bounded source task closes.

| Selected flow | Real consumer exercised |
|---|---|
| Test | Existing `test_artifact_runner.py` shared-context packet |
| Spike | Standard-library `ast` CLI and a standalone analysis against a real owner ledger |
| Build | `zipapp` CLI, followed by execution and verification of the built Python archive |
| Archive extraction | `zipfile` CLI, followed by consumption of the extracted contents |
| Report | `cProfile` output, read and rendered through `pstats` before disposition |
| Source worker | Git archive, extraction and consumption of a transient qualification image |

The initial focused packet passed 41 tests on Windows x64 and 40 tests on
Linux, with one Windows-only junction skip. The two test files corrected
for strict typing then passed 26 tests on each platform.
The unchanged allocator's qualified root, link, permission, identity and
partial-reclaim controls remain the provider boundary from #12508; the new
consumer does not override them. This is reuse of that provider qualification,
not a claim that every negative was newly executed through every command kind.
Unknown-cleanup fault injection preserves the full payload and open
responsibility. Actual command failure and timeout retain only bounded
diagnostics; successful consumers close the exact allocation and preserve
the independent cache sentinel. Failure and timeout controls read the actual
child-written payload inside the target scope before the owner can prune it.
Setup exhausting the deadline before launch cannot satisfy those controls.
The observer always runs the owner's exit, even if the read fails, and checks
the bytes only after settlement. Exceptional body exits delegate unchanged.
A missing-signal regression proves failed observation leaves no bound custody;
the unknown-cleanup control checks that the original error and open custody survive.

The explicit two-allocation control is
`test_owned_producer.py::test_target_disposition_preserves_an_unrelated_sibling_allocation`.
It holds a sibling allocation live while the target succeeds, fails, times out
or reports injected unknown cleanup. The sibling's device/inode identity,
owner-marker bytes, payload and open ledger state must remain unchanged across
the target's disposition. Unknown target cleanup also preserves the enclosing
scope instead of treating it as settled. The other cases separately close
only the consumed sibling after that identity check.
The initial four-case control passed on Windows x64 in 17.73 seconds and Linux
in 6.11 seconds. Exact-exception and complete-ledger-identity corrections then
passed in 17.11 and 5.44 seconds. These earlier timings do not qualify the
subsequent child-start and path-length controls.

The explicit native path-length control is
`test_owned_producer.py::test_native_overlong_path_refuses_without_losing_custody`.
It checks native refusal of an overlong component in the selected root,
refusal before child execution and no allocation, with a nearby valid
component as a positive control. Its separate child case writes and observes
a valid long component, encounters the native overlong-component refusal,
and verifies bounded retained diagnostics with settled custody. Windows APIs
can report errors 123/206; Python's file API can instead expose `EINVAL`
without a Windows error attribute.
On Windows, both cases first probe the fixture's long-path prerequisite. An
unavailable venue is reported as a skip, not a component-length acceptance
result. The same consumer controls run on POSIX and require native
`ENAMETOOLONG` at both root admission and child execution.
This tests component-length refusal, not every possible Windows total-path
limit or filesystem policy. The 12 existing producer cases, now including
positive child-start checks, passed on Windows x64. The first path-control
attempt failed because it expected only error 206; after the native error
normalization above, both path cases passed in 6.47 seconds. The Linux file
passed 12 cases with two Windows-only skips in 22.17 seconds. The final
six-file whole-file strict matrix passes both declared targets.
The final cleanup-safe observer and prerequisite-aware file passed all
15 cases on Windows x64 in 49.45 seconds. Linux passed 13 cases with two
Windows-only skips in 22.42 seconds. Strict checking passes the final six-file
cohort for both declared targets. No runtime or required unit-source admission
changed.

The producer-level partial-removal control is
`test_owned_producer.py::test_partial_native_removal_reports_retained_settled_custody`.
It starts and consumes a real child, then injects refusal only at the exact
allocation's final directory removal. The real cleanup owner removes the
consumed payload, retains its breadcrumb and reports partial removal. The
test checks the retained ledger disposition, settled process custody and
provider readback instead of calling retained bytes a complete removal.
The expanded file passes all 16 cases on Windows x64 in 55.63 seconds and
Linux in 25.75 seconds. A subsequent direct-`os` import correction passes the
affected partial-removal case on both platforms, in 4.85 and 1.25 seconds,
and the final six-file strict matrix passes both declared targets.

The parser rejects long-option abbreviations. Three actor-free regressions
and actual Windows CLI calls cover `--own`, `--ki` and `--time`, each refused
with exit 2 before allocation. The final full supported unit/coverage packet
passed 5,565 cases in 129.122 seconds, including eight new actor-free cases.
Required CI keeps its existing effect restrictions and five-minute budget;
native process cases remain in the explicit portfolio.

Whole-file strict checking passed all six changed Python files for both
declared Python 3.10 targets. The 43 admitted projects passed 86 target checks.
Ruff F/E9, install contract, headless launch, changed-module size,
documentation consistency, source-hash admission and whitespace checks passed.
The focused reproducible entry points are:

```text
python tools/run-plugin-tests.py agent-worktrees --retirement-ready --test-file tests/test_artifact_runner_contract.py --test-file tests/test_artifact_runner.py --test-file tests/test_owned_producer.py --allow-explicit-tiers
python tools/run-plugin-tests.py agent-worktrees --retirement-ready --test-file tests/test_owned_producer.py -k target_disposition_preserves_an_unrelated_sibling_allocation --allow-explicit-tiers
python tools/typecheck.py --new-code --base <pinned-comparison-commit>
python tools/typecheck.py --enforced
python tools/run-promotion-unit-tests.py --source-sha <full-source-commit> --output-dir <new-owned-evidence-directory>
```

The exact five consumer argument vectors and their synthetic inputs are
preserved at the reviewed implementation revision:
[`_CONSUMERS` and the invoking control](https://github.com/ThomasMichon/copilot-extensions/blob/ff14f14b42fa04b81942aa5c81e1f85d92240c0f/plugins/agent-worktrees/tests/test_owned_producer.py#L48-L170).
That control defines the input Python application and ZIP member, each child
command and the assertions that consume its result. Its Git input comes from
the [isolated owner seed](https://github.com/ThomasMichon/copilot-extensions/blob/ff14f14b42fa04b81942aa5c81e1f85d92240c0f/plugins/agent-worktrees/tests/test_resource_producer_finalize.py#L22-L72)
and [anchor/origin fixture](https://github.com/ThomasMichon/copilot-extensions/blob/ff14f14b42fa04b81942aa5c81e1f85d92240c0f/plugins/agent-worktrees/tests/test_transitive_finalize_integration.py#L71-L115).

The separate standalone real-ledger analysis used this program and argument
vector. Substitute an already-compatible interpreter, an owner under the
caller's control and an absolute admitted source file; the selected interpreter
must satisfy the artifact guide's prerequisites. The program writes only inside
its allocated working directory:

```python
from pathlib import Path
import subprocess
import sys

program = """import ast,json,pathlib,sys
source=pathlib.Path(sys.argv[1]); tree=ast.parse(source.read_text())
report=pathlib.Path('analysis.json')
report.write_text(json.dumps({'functions':sum(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) for n in ast.walk(tree))}))
result=json.loads(report.read_text()); assert result['functions']>0
print(json.dumps({'consumed_ast_analysis':result},sort_keys=True))
"""
command = [
    sys.executable, str(Path("tools/run-owned-producer.py").resolve()),
    "--owner", "<machine/project/worktree>", "--kind", "spike", "--timeout", "20",
    "--", sys.executable, "-c", program, "<absolute-source-file>",
]
subprocess.run(
    command, check=True, timeout=90,
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
)
```

The recorded invocation additionally read the exact owner ledger before and
afterward and confirmed one new closed, non-unsettled allocation. A successful
command exit alone is not independent proof of physical absence when the owner
reports a partial retained disposition.

The selected interpreter must already satisfy each command's documented
prerequisites. Windows x64 and Linux were exercised; native ARM64 and macOS
execution are not claimed. Windows-host checker preparation remained
unqualified under its configured feed's dependency-age cutoff; the declared
Linux/Win32 analysis targets passed on Linux without a cutoff exception.
The devcontainer CLI was unavailable, so the declared contained Linux
fallback was used. Full fresh-machine provisioning is not applicable because
no installer or bootstrap changed; first-touch allocation remains a real
native fixture. No external service deployment was exercised.

The new entry point is repository source tooling, not an installed runtime
verb. This record asserts source delivery only. Release, installed guidance,
real legacy-history cutover and retained resource disposition keep their
existing owners and independent acceptance gates. All other parent tracks
and validation items remain unchanged.

## Boundaries

No automatic enabling, installed rollout, real history rescue, shared-cache
retirement, unknown-file deletion, new service, parallel claims registry or
whole-machine scan is included. This source plan does not supersede other
Derived Disk Hygiene tracks or assign their remaining work.
