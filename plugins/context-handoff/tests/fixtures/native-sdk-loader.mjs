export async function resolve(specifier, context, nextResolve) {
  const namespace = process.env.FIXTURE_SDK_NAMESPACE || "@microsoft/copilot-sdk";
  if (namespace === "@github/copilot-sdk" && specifier.startsWith("@microsoft/copilot-sdk")) {
    const error = new Error("Cannot find package '@microsoft/copilot-sdk' imported from synthetic fixture");
    error.code = "ERR_MODULE_NOT_FOUND";
    throw error;
  }
  if (specifier === namespace || specifier === `${namespace}/extension`) {
    return { url: "native-test:sdk", shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url !== "native-test:sdk") return nextLoad(url, context);
  return {
    format: "module", shortCircuit: true,
    source: `
      import assert from "node:assert/strict";
      export const approveAll = () => ({kind:"approve"});
      export async function joinSession(config) {
        const modern = process.env.FIXTURE_SDK_NAMESPACE !== "@github/copilot-sdk";
        assert.equal(Array.isArray(config.tools), !modern);
        assert.equal(typeof (modern ? config.permissions.onRequest : config.onPermissionRequest), "function");
        assert.ok(Array.isArray(modern ? config.interaction.commands : config.commands));
        if (modern) {
          assert.ok(Array.isArray(config.tools.definitions));
          assert.equal(config.commands, undefined);
          assert.equal(config.onPermissionRequest, undefined);
        } else {
          assert.equal(config.permissions, undefined);
          assert.equal(config.interaction, undefined);
        }
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
