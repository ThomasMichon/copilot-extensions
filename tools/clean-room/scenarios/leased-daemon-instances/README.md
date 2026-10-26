# Leased daemon instance proof

Credential-free Tier-P/F1 acceptance for the shared source and native cleanup
contract. Run in a fresh disposable Linux Docker container with `--init`,
network disabled, the candidate checkout read-only, and no host Docker socket
or privileged mode. The probe refuses ordinary host execution because its
production control deliberately has no test expiry.

The scenario needs the shared `clean-room-lib.sh` through `CR_LIB` and these
inputs:

| Input | Meaning |
|---|---|
| `CR_DAEMON_SOURCE_ROOT` | Candidate checkout; defaults to `CR_HARNESS_MOUNT` or `/harness`. |
| `CR_DAEMON_NATIVE_HELPER_ROOT` | Prebuilt bundle containing `daemon_instances.sh` and `native/linux-ARCH/daemon-instance`. An installed wheel's package directory is also valid. |
| `CR_DAEMON_SOURCE_BUILD=1` | Explicit alternative to the prebuilt bundle: compile the candidate helper through `_build_native.py` inside the test's build-toolchain image. |

Select one native input mode. Source construction needs its declared compiler
before the scenario starts; normal daemon startup and installer cleanup never
compile a helper or silently acquire a toolchain.

Inside the prepared container:

```bash
export CR_DAEMON_SOURCE_ROOT=/candidate
export CR_DAEMON_NATIVE_HELPER_ROOT=/installed/agent_procutil
bash /scenario/scenario.sh
```

Assertions cover exact unattended defaults, explicit interactive policy,
cross-root discovery, renewable leases, hard expiry, abrupt owner loss,
production protection, real installer reclamation, repeated cleanup and
retention of unrelated proof files. Pass/fail and classified jams use the
standard `cr-report.json` and `cr-logs` format.

This source-consumer scenario complements, not replaces, the actual built-wheel
test in `tools/test_native_daemon_artifacts.py`. That test verifies installed
helper lookup and a separate runtime with Python and the compiler physically
absent. Windows Job/visibility and Python-free PowerShell cases remain in the
shared library's focused native test portfolio.
