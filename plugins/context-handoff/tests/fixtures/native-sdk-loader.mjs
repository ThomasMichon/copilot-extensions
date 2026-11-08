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
        const handlers = new Map(), prompts = [], logs = [];
        const session = {
          sessionId: null, config, handlers, prompts, logs,
          rpc: {tools:{getCurrentMetadata:async () => ({tools:null})}},
          on(name, fn) { handlers.set(name, fn); },
          log(message) { logs.push(message); },
          async send(message) { prompts.push(message); },
        };
        globalThis.nativeTestSession = session;
        return session;
      }
    `,
  };
}
