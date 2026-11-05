import { createControlClient, RemoteControlError } from "./control-client.mjs";

export async function createStartupSession(descriptor, { ownerId, ...options } = {}) {
  const client = createControlClient(descriptor, options);
  const lease = await client.grantStartup(ownerId);
  let sequence = 0;
  let held = false;
  let pending = Promise.resolve();

  function invoke(action, content, mode) {
    const operation = pending.then(async () => {
      if (held) throw new RemoteControlError("startup_control_held", "Original control outcome requires reconciliation");
      try {
        const response = await client.invoke(action, lease, ++sequence, content, mode);
        return response.result;
      } catch (error) {
        held = true;
        throw error;
      }
    });
    pending = operation.catch(() => {});
    return operation;
  }

  return {
    sessionId: descriptor.sessionId,
    send: ({ prompt, mode }) => invoke("send", prompt, mode),
    abort: () => invoke("abort"),
  };
}
