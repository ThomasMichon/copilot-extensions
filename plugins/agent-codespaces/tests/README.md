# Codespaces command-admission controls

The [suite testing guide](../../../TESTING.md) owns the runner and isolation
procedure. `test_managed_command_gate.py` runs actual Bash and PowerShell gates
with prepared UV and independent 3.11.18 helper/3.10.20 application interpreters.
Eighteen raw/materialized controls per native shell retain current/LKG/newest
and namespaced selection, readiness and invocation metadata, Legacy argument
forwarding, stdin, both streams and exit status.

Missing UV/provider/capture refuses before application entry or provisioning.
The refusal controls use the existing explicit helper selector to isolate
application admission. Successful paths run twice with poisoned ambient
Python/selectors and shadow commands, unchanged slot/payload bytes and exact
native UV work: one application lookup per Bash call; PowerShell additionally
performs the existing helper version check and helper lookup.

The path-gated Ubuntu/macOS/Windows jobs require every applicable case and report
without skips within eight-minute deadlines. Inert context decisions and modules
do not access Codespaces, backends, credentials or services; these are not
installed-host adoption or full command-latency measurements.
