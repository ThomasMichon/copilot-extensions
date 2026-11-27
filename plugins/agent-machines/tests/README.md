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
Newline-suffixed context/runtime/source fields retain their identity, atomic
replacement between queries cannot mix manifest generations, and literal NUL
is refused before Bash capture. The adapter retains the source module's
POSIX skip and T2/process/filesystem marks.
Every Python/UV command and the addressed runtime are poisoned; these checks
must neither start Python nor prepare an interpreter. Repeated current-path
samples are recorded without increasing the existing fifteen-second outer cap.
This is manifest-reader qualification, not an end-to-end installation-policy
or first-use provisioning pass. The PowerShell-native reader is unchanged.

## Early cell-lifecycle installer ingress

`test_cell_lifecycle_bootstrap.py`, exposed by
`test_machines_cell_bootstrap_source.py`, exercises all five early lifecycle
actions through the real `init.sh`/`init.ps1` entrypoints with an inert helper.
Raw and materialized inputs retain the canonical Installer Engine request and
capture closure. Prepared CPython 3.11.18 is selected through real UV despite
poisoned ambient Python and foreign selectors; repeated calls preserve helper
arguments, stdin, both output streams, exit status, working directory and bytes.
Missing UV, provider, request, capture support or prepared interpreter must
refuse before helper entry without acquisition or ambient fallback.

The existing native jobs require all twenty applicable controls and ten
repeated work reports per supported installer shell, without skips. `init.sh`
is qualified on Linux/macOS, and `init.ps1` on both Windows PowerShell hosts;
this is not a new Windows Git Bash installer contract. These fixtures perform
no actual repair, uninstall, attribution, deactivation or retirement and do
not qualify cold setup, installed adoption or the lifecycle engine's separate
authorization/deletion protocol.
The same Windows jobs also require all five retained construction, real staged
stamp and lifecycle-refusal controls through `test_machines_cell_output_source.py`.
The lifecycle fixture invokes the complete installer with real parameter binding,
materialized bootstrap support and prepared UV rather than a Python-only
command-lookup shim or an extracted preamble with missing defaults.
The original lifecycle error, bytes and no-state-mutation assertions remain
unchanged. The isolated construction fixture supplies the installer's empty
wheel-option defaults before its extracted function definitions.

## Native explicit-setup seed

`test_installer_bootstrap.py`, exposed by
`test_machines_installer_bootstrap_source.py`, exercises raw and materialized
setup support with poisoned ambient Python and selectors. UV-missing and
UV-present preparation use inert acquisition owners; no network or host
installation is performed. Prepared-only inspection refuses missing inputs
without repair. Actual installer entrypoints select Python before source
identity for installation and stamping, with an inert legacy-policy decision
that never inspects the host account. Invalid cell context/deadline inputs do
not prepare prerequisites.
Configured UV installation, unmanaged and XDG destinations are exercised through
inert acquisition followed by a fresh prepared-only discovery invocation.
Native legacy stamps publish their actual launcher without UV lookup; POSIX
retains its existing no-flock transaction prerequisite through an explicit
prepared seed, without falling back to poisoned ambient Python.

The PowerShell watchdog control retains the actual shell-caller custody
handshake and proves that its selected worker receives only the remaining
installer deadline. Separate native controls retain implicit inactivity,
explicit elapsed-time and disabled PowerShell watchdog modes after bootstrap
deadline forwarding. Real UV constructs fixture-owned application venvs through
the canonical engine under poisoned selectors/configuration, with caller
environment restoration. This does not install application dependencies or
qualify a production update, service/task PATH, SAC-enforcing deployment or
installed adoption.

The existing native workflow requires all 38 applicable Bash controls on
Linux/macOS and all 46 controls on each Windows PowerShell host, without skips.
The Windows packet retains real self-stage success and exit-124 cleanup with
materialized bootstrap support and fixture-owned inert preparation.
Windows Git Bash is not an installer target. Large copied UV fixture binaries
are released after each case so the packet retains the existing scratch cap.

## Prepared-only legacy uninstall

`test_uninstall_bootstrap.py`, exposed through
`test_machines_uninstall_bootstrap_source.py`, exercises both raw and
materialized helpers under poisoned ambient Python and selectors. Actual
finite removal preserves durable/unknown bytes and persistent lease inodes,
and is idempotent. Separate controls require live-generation-lease refusal,
missing-UV refusal before helper entry, and rejection of an interpreter within
the removal root. No acquisition or production-root removal is performed.

The native lane requires eight no-skip cases on each POSIX Bash host and
eight on each Windows PowerShell host. Both Windows shell packets run in the
existing Windows Bash carrier job, which has headroom, rather than enlarging
the original eight-minute PowerShell jobs. This does not add a Windows Bash
installer contract. POSIX PowerShell is useful for isolated selection/refusal
controls, but is not a native shell for the finite removal protocol.
Each raw/materialized success report records actual removal and subsequent
idempotent-call durations, with six UV calls and two managed lookups across
the pair. These synthetic-state observations are not full installation or
tail-latency measurements.

Full local POSIX deletion can refuse when a current-user process is not
inspectable. That existing fail-closed observation boundary is not relaxed or
reported as a pass; real positive removal still requires actual native-lane
qualification. No root, service, credential or process repair is a fixture
prerequisite.
