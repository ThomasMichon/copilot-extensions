"""Explicit version for the agent-bridge **HTTP wire contract** (dotfiles #632).

Plugin payloads update independently, so a client and the daemon it calls are
routinely at **different versions**. This module gives the HTTP surface an
explicit, advertised protocol version — distinct from the package
``__version__`` (a build stamp, not a contract) and from the ACP /
session-host ``PROTOCOL_VERSION``\\s (different transports) — so a client can
**gate a version-introduced capability on the daemon's advertised support**
instead of blind-sending a request an older daemon would ignore or reject. This
is the forward-compat half of the *version-skew-tolerant-contracts* invariant
(``visions/plugin-services`` → *interoperate-across-version-skew*).

Rules for evolving the contract:

- **Bump ``HTTP_PROTOCOL_VERSION``** when the HTTP surface gains a capability a
  client may need to *detect* (a new endpoint, a new request field with server
  behavior, a new response field a client depends on). Additive, tolerant-reader
  changes (a new optional field an old client simply ignores) do **not** require
  a bump — but bumping lets a newer client *know* the capability is present.
- **Raise ``HTTP_PROTOCOL_MIN_SUPPORTED``** only on a genuinely **breaking**
  change, and only after a deprecation window — it declares the oldest client
  contract this daemon still serves. It should move rarely.

The daemon advertises both on ``/health``; ``BridgeClient`` reads them (see
``daemon_protocol`` / ``daemon_supports``).
"""

from __future__ import annotations

# Current HTTP wire-contract version this build speaks.
HTTP_PROTOCOL_VERSION = 13

# First version that exposes the harness-owned relay interruption capability.
RELAY_INTERRUPT_PROTOCOL_VERSION = 2

# First version that exposes the harness-only failed ACP handshake start fault.
FAILED_ACP_HANDSHAKE_PROTOCOL_VERSION = 3
FAILED_ACP_HANDSHAKE_FAULT = "failed-acp-handshake"

# First version that exposes target-scoped container recreation for parity.
CONTAINER_RECREATE_PROTOCOL_VERSION = 4
CONTAINER_RECREATE_FAULT = "container-recreate"

# First version whose machine list/detail responses expose static topology
# descriptions and capability breadcrumbs.
MACHINE_METADATA_PROTOCOL_VERSION = 5

# First version that exposes bounded delegated-result snapshots and opaque
# cursor-neutral result positions.
RESULT_SNAPSHOT_PROTOCOL_VERSION = 6

# First version that projects represented interactive sessions through the
# bounded result snapshot shape.
REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION = 7

# First version that reports provider-target refresh failures consistently
# across direct session and worktree resume surfaces.
PROVIDER_TARGET_REFRESH_PROTOCOL_VERSION = 8

# First version that projects completed ACP turns as at rest on read surfaces.
AT_REST_PROJECTION_PROTOCOL_VERSION = 9

# First version that exposes cursor-neutral attention evaluation and waits.
ATTENTION_WAIT_PROTOCOL_VERSION = 10

# First version that exposes authenticated remote Bridge carrier operations.
REMOTE_OPERATIONS_PROTOCOL_VERSION = 11

# First version that atomically ends a session only while it remains idle.
CONDITIONAL_IDLE_END_PROTOCOL_VERSION = 12

# First version that exposes one aggregate SSE connection for a caller's set of
# remote carrier subscriptions.
REMOTE_EVENT_MULTIPLEX_PROTOCOL_VERSION = 13

# Oldest client HTTP-contract version this daemon still serves (the low end of
# the supported range). Only ever raised after a deprecation window.
HTTP_PROTOCOL_MIN_SUPPORTED = 1

# Sentinel for a daemon whose ``/health`` predates protocol advertisement (any
# build older than this feature): it reports no version, so a reader treats it as
# ``UNVERSIONED`` and gates every versioned capability **off** rather than
# assuming support.
UNVERSIONED = 0
