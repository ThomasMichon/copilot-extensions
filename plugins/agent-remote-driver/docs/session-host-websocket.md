# ACP WebSocket access

The ordinary Driver application accepts `--ws-port 0` alongside its existing
TCP broker options. Its private Host state publishes `websocket` with
`contract: "acp.v1"`, the loopback `/acp` URL and a per-launch `nonce`.
`--support-json` reports `acp.v1` only when the executing artifact includes
the transport and its ordinary `websockets` distribution.

Ordinary ACP launches always authenticate the TCP listener too. If no nonce
is supplied, the Host generates one and publishes it only in its private state.
The WS adapter uses that same launch token; enabling WS never leaves an
unauthenticated parallel TCP route. Only explicit legacy execution retains
the old optional-authentication contract.

Connect with WebSocket subprotocols `acp.v1` and `bearer.<nonce>`. The server
selects only `acp.v1`, never the credential. Like Bridge's public ACP WS
surface, each text message is one UTF-8 JSON-RPC object, without a driver
envelope. A normal ACP client initializes and uses `session/new`, `session/load`,
`session/prompt`, cancellation and permission responses. The endpoint exposes
the existing process: connecting never creates another Copilot or conversation.
The URL is not Bridge's agent registry or session-routing API.

This local endpoint rejects Origin-bearing requests and unexpected authorities;
it is not a browser-facing or remotely bound service. Credentials remain in the
private state record, not argv or diagnostics. A concurrent TCP/WS controller
is refused rather than displaced. Closing WS releases control without stopping
Copilot. Explicit retirement remains the owning TCP control operation; there is
no invented ACP stop method.

WS closure is observed independently of RPC writes. A backpressured write
cannot keep its TCP controller attached after disconnect. Shutdown bounds the
drain and TCP close, then releases the controller without stopping Copilot or
retrying an uncertain inbound RPC.
Initial HELLO and replay writes use the same controller cleanup scope, so a
disconnect during startup output cannot reserve an abandoned controller.

Ordinary ACP WS has no resumable application receipt. The adapter acknowledges
Host output after handing it to WebSocket, not after the remote application
consumes it. A network failure can leave delivery uncertain; the adapter never
replays an inbound RPC or prompt. Consumers needing durable sequence/ACK
recovery must keep using the TCP contract. This implements the existing custom
`acp.v1` convention, not conformance to a pending ACP transport proposal.

The compact Python zipapp remains a dependency-free **TCP receiving export**.
It does not vendor WebSocket code or claim WS support. Use the ordinary locked
application, including its distribution metadata and notices, for WS access.
The full application and receiving export use the same Host implementation.
HTTP/SSE is not currently offered by the core; Bridge's independent remote
HTTP/2 ACP client remains unchanged.
