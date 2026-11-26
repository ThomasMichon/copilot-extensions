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
synthetic declarations. Bash inherited descriptors survive admission.
Missing UV, provider and capture controls refuse before importing the application
or calling the installer. Repeated samples and exact UV lookups are recorded
without changing the selected slot or payload.

`python-selection-native.yml` qualifies 26 controls per native shell on Linux,
macOS, Windows PowerShell and PowerShell Core. It rejects skips and requires
both samples for successful entries. This packet is not default unit CI.
`test_health_job_payload.py` separately retains real health-capability refusal
and rollback checks; `test_configuration_payload_readonly.py` retains cold
configuration/health refusal without installer execution.

These controls perform no real machine module, configuration mutation, health
resource collection, scheduling, service, network or credential operation.
They do not qualify first-use installation, installed maintenance or daemon
lifetime.
