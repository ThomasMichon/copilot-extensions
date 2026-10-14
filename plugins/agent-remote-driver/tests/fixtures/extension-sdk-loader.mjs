export function resolve(specifier, context, nextResolve) {
  if (specifier === "./driver-server.mjs" && context.parentURL?.endsWith("/extension.mjs")) {
    return { shortCircuit: true, url: "data:text/javascript,export const createDriverServer = globalThis.probeCreateServer; export const MAX_SSE_CLIENTS = 16;" };
  }
  if (specifier === "@github/copilot-sdk") {
    return { shortCircuit: true, url: 'data:text/javascript,export const approveAll = () => ({kind:"approved"});' };
  }
  if (specifier === "@github/copilot-sdk/extension") {
    return { shortCircuit: true, url: "data:text/javascript,export async function joinSession(){return globalThis.probeSession;}" };
  }
  return nextResolve(specifier, context);
}
