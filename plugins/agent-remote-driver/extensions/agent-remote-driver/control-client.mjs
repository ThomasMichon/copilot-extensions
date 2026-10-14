export class RemoteControlError extends Error {
  constructor(code, message, status = null, details = {}) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

export function createControlClient(descriptor, { fetchImpl = fetch, timeoutMs = 5_000 } = {}) {
  if (
    descriptor?.host !== "127.0.0.1" || !Number.isInteger(descriptor.port) ||
    descriptor.port < 1 || descriptor.port > 65535 ||
    typeof descriptor.token !== "string" || !descriptor.token ||
    descriptor.controlProtocol !== 2 || typeof descriptor.instanceId !== "string" || !descriptor.instanceId ||
    typeof descriptor.sessionId !== "string" || !descriptor.sessionId
  ) throw new RemoteControlError("descriptor_invalid", "A protocol-2 loopback driver descriptor is required");
  const scheme = "Bear" + "er ";
  const authorization = scheme + descriptor.token;

  async function request(path, body) {
    let response;
    let result;
    try {
      response = await fetchImpl(`http://127.0.0.1:${descriptor.port}${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers: { authorization, ...(body === undefined ? {} : { "content-type": "application/json" }) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        redirect: "error",
        signal: AbortSignal.timeout(timeoutMs),
      });
      result = await response.json();
    } catch {
      throw new RemoteControlError("transport_outcome_unknown", "Driver response unavailable; do not replay an uncertain mutation");
    }
    if (!response.ok || result?.ok !== true) {
      throw new RemoteControlError(
        typeof result?.code === "string" ? result.code : "request_failed",
        `Driver request refused (HTTP ${response.status})`,
        response.status,
        { control: result?.control, effects: result?.effects },
      );
    }
    return result;
  }

  async function health() {
    const result = await request("/health");
    const state = result.control;
    if (state?.controlProtocol !== 2 || state.instanceId !== descriptor.instanceId ||
        state.sessionId !== descriptor.sessionId || result.sessionId !== descriptor.sessionId) {
      throw new RemoteControlError("target_changed", "Driver identity changed; read a fresh descriptor");
    }
    return state;
  }

  return {
    health,
    async grant(ownerId, { rehook = false } = {}) {
      if (typeof descriptor.controlToken !== "string" || !descriptor.controlToken) {
        throw new RemoteControlError("control_credential_missing", "Descriptor has no administrative re-hook credential");
      }
      const target = await health();
      const result = await request(rehook ? "/control/re-hook" : "/control/claim", {
        target, ownerId, controlToken: descriptor.controlToken,
      });
      const lease = result.lease;
      if (lease?.instanceId !== descriptor.instanceId || lease.sessionId !== descriptor.sessionId ||
          typeof lease.generation !== "string" || !lease.generation || typeof lease.token !== "string" || !lease.token) {
        throw new RemoteControlError("grant_outcome_unknown", "Driver grant response is invalid; reconcile before retry");
      }
      return lease;
    },
    async invoke(action, lease, sequence, content, mode) {
      if (!["send", "steer", "abort", "release"].includes(action)) {
        throw new RemoteControlError("action_invalid", "Unknown driver command");
      }
      if (lease?.instanceId !== descriptor.instanceId || lease.sessionId !== descriptor.sessionId) {
        throw new RemoteControlError("claim_target_changed", "Saved claim belongs to another session or extension instance");
      }
      return request(action === "release" ? "/control/release" : `/${action}`, {
        lease, sequence,
        ...(content === undefined ? {} : { content }),
        ...(mode === undefined ? {} : { mode }),
      });
    },
  };
}
