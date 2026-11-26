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
distinct. The real health compatibility and readiness helpers execute against
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
The Bash jobs additionally require the real capability-refusal/rollback control
through `test_machines_health_source.py`, including Windows Git Bash path forms.
The opt-in Windows scheduler fixture prepares canonical admission support and a
managed runtime with command-local UV access. Its original scheduler, host-state,
network, observation and cleanup gates remain separate; this inert packet does
not execute or qualify those effects.

These controls perform no real machine module, configuration mutation, health
resource collection, scheduling, service, network or credential operation.
They do not qualify first-use installation, installed maintenance or daemon
lifetime.
