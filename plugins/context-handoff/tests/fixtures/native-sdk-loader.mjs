export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@github/copilot-sdk" ||
      specifier === "@github/copilot-sdk/extension") {
    return { url: "native-test:sdk", shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url !== "native-test:sdk") return nextLoad(url, context);
  return {
    format: "module", shortCircuit: true,
    source: `
      export const approveAll = () => ({kind:"approve"});
      export async function joinSession(config) {
        const handlers = new Map(), subscribers = new Map(), prompts = [], logs = [], sends = [];
        const session = {
          sessionId: process.env.FIXTURE_BOUND_CWD && !process.env.FIXTURE_DELAYED_ID
            ? "fixture-bound-session" : null,
          config, handlers, prompts, logs, sends,
          rpc: {tools:{getCurrentMetadata:async () => ({tools:null})}},
          on(name, fn) {
            const callbacks = subscribers.get(name) || new Set();
            callbacks.add(fn);
            subscribers.set(name, callbacks);
            handlers.set(name, async event => {
              for (const callback of callbacks) await callback(event);
            });
            return () => callbacks.delete(fn);
          },
          log(message) { logs.push(message); },
          async send(message) {
            sends.push(message);
            prompts.push(typeof message === "string" ? message : message.prompt);
          },
        };
        globalThis.nativeTestSession = session;
        return session;
      }
    `,
  };
}
