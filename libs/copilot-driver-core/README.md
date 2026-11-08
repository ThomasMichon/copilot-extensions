# Copilot Driver core

`agent-copilot-driver-core` owns the `agent_remote_driver` Python namespace:
the canonical Session Host, TCP protocol, ACP WebSocket adapter, retained
execution and compact receiving export. It does not import Bridge, Worktrees
or Manager. Install it as an ordinary first-party distribution and run
`python -m agent_remote_driver.session_host --support-json`; plugin enablement
is not required.

The [Driver application and extension connector](../../plugins/agent-remote-driver/README.md)
provides the ordinary application front door. Bridge consumes this same
library through the existing canonical-reference and wheel-build owners,
not a sibling plugin source dependency.

See the [ACP WS boundary](../../plugins/agent-remote-driver/docs/session-host-websocket.md).
The compact dependency-free receiving archive supports TCP only. Tests stay
with the application and Bridge consumers; the canonical receiving-policy
units remain in the required actor-free packet.
