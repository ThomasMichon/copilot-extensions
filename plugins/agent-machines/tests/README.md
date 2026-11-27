# Machines command admission controls

Run `tools/run-tests agent-machines --test-file
tests/test_managed_command_gate.py --allow-explicit-tiers` from the repository
root. The acquisition-free packet requires prepared UV and CPython 3.10.20,
independent of the payload's declared 3.11.18 bootstrap. Missing prerequisites
are skips, not qualification. `test_machines_ready_source.py` exposes the same
packet through the lightweight `payload-invocation` suite.

The actual Bash, PowerShell Core and Windows PowerShell command surfaces
exercise raw and materialized capture support. Inert installed applications
prove exact interpreter selection for ordinary, configuration, health and
readiness commands; current, last-known-good and newest-complete resolution;
namespaced context; hostile ambient selectors; stdin, streams, exit status and
empty/quoted/Unicode arguments. Isolated health and readiness flags remain
distinct. Legacy controls also pass numeric and Boolean PowerShell variables
and require native argument strings. Generated Bash instrumentation and carriers
use Unix newlines even on Windows. The real compatibility/readiness helpers use
synthetic declarations. POSIX Bash inherited descriptors survive admission.
Missing UV, provider and capture controls refuse before importing the application
or calling the installer. Repeated samples and exact UV lookups are recorded
without changing the selected slot or payload.

`python-selection-native.yml` qualifies 26 controls per native shell on Linux,
macOS, Windows PowerShell, PowerShell Core and Windows Git Bash. It rejects skips and requires
both samples for successful entries. This packet is not default unit CI.
`test_health_job_payload.py` separately retains real health-capability refusal
and rollback checks; `test_configuration_payload_readonly.py` retains cold
configuration/health refusal without installer execution.
`test_machines_provisioning_source.py` exposes the retained Windows installer
streaming, failure/retry and warm-launch controls through the same lightweight
suite. Both Windows jobs require their three cases without skips, using the
real prepared application and admission support rather than `PYTHONPATH`.
Every native job additionally requires the real capability-refusal/rollback
control through `test_machines_health_source.py`. PowerShell fixtures use native
Windows addresses; Git Bash also exercises Windows/POSIX path forms.
The opt-in Windows scheduler fixture prepares canonical admission support and a
managed runtime with command-local UV access. Its original scheduler, host-state,
network, observation and cleanup gates remain separate; this inert packet does
not execute or qualify those effects.

These controls perform no real machine module, configuration mutation, health
resource collection, scheduling, service, network or credential operation.
They do not qualify first-use installation, installed maintenance or daemon
lifetime.

## Native POSIX bootstrap manifests

`test_bootstrap_manifest_native.py` (also exposed by the lightweight
`test_machines_manifest_source.py` adapter) exercises the actual Bash hook and
native JSON reader from source and materialized inputs. Its inert status,
publication and installer owners isolate legacy/namespaced manifest admission:
current and stale versions, strict schema/UTF-8/duplicate-key refusal,
runtime/current-marker coherence, first-use ordering and publication denial.
Every Python/UV command and the addressed runtime are poisoned; these checks
must neither start Python nor prepare an interpreter. Repeated current-path
samples are recorded without increasing the existing fifteen-second outer cap.
This is manifest-reader qualification, not an end-to-end installation-policy
or first-use provisioning pass. The PowerShell-native reader is unchanged.
