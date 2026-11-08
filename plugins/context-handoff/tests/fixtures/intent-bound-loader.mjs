import * as sdk from "./native-sdk-loader.mjs";
const extension = new URL("../../extensions/context-handoff/extension.mjs", import.meta.url).href;
const core = new URL("../../extensions/context-handoff/handoff-core.mjs", import.meta.url).href;

export async function resolve(specifier, context, nextResolve) {
  if (context.parentURL === extension && specifier === "./handoff-core.mjs") {
    return { url: "intent-bound:core", shortCircuit: true };
  }
  return sdk.resolve(specifier, context, nextResolve);
}
export async function load(url, context, nextLoad) {
  if (url !== "intent-bound:core") return sdk.load(url, context, nextLoad);
  return {
    format: "module", shortCircuit: true,
    source: `
      export * from ${JSON.stringify(core)};
      export function runCli(_bin, args, options) {
        if (options.allowProvision !== false) throw new Error("provisioning forbidden");
        if (args[0] === "get") return process.env.FIXTURE_BOUND_CWD;
        globalThis.intentGoalReads = (globalThis.intentGoalReads || 0) + 1;
        if (globalThis.emergencyStarted) throw new Error("optional discovery before store");
        if (args[0] === "effort-focus") return JSON.stringify({
          active_effort: {active:true,state:"open",path:"effort.md"}
        });
        if (args[0] === "state-root") return JSON.stringify({
          state_root:process.env.FIXTURE_BOUND_CWD,requires_external:false,bound:true
        });
        throw new Error("unexpected discovery");
      }
      export async function triggerHandoff(options) {
        globalThis.emergencyStored = options;
        return {ok:false,error:"fixture stops after capture"};
      }
    `,
  };
}
