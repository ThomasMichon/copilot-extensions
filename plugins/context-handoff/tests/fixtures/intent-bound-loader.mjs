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
      const kind = process.env.FIXTURE_CONSUME;
      const checkScope = (cwd) => {
        if (cwd !== process.env.FIXTURE_BOUND_CWD) throw new Error("wrong consumption scope");
        globalThis.consumeCwds = [...(globalThis.consumeCwds || []), cwd];
      };
      export function agentDispatchAvailable() { return kind === "task"; }
      export function agentWorktreesGet(_key, cwd) { checkScope(cwd); return cwd; }
      export function findHandoffTask(cwd) { checkScope(cwd); return {id:"synthetic-task"}; }
      export function findTaskDeliveryCheckpoint(cwd) {
        checkScope(cwd);
        return kind === "checkpoint" ? {handoffToken:"synthetic-checkpoint"} : null;
      }
      export function findHandoffFile(cwd) {
        checkScope(cwd);
        return kind === "file" ? {record:{id:"synthetic-file"},path:"synthetic-file.md"} : null;
      }
      export function consumeDispatchHandoffTask(cwd) {
        checkScope(cwd);
        return {ok:true,payload:"Synthetic bound continuation.",checkpointState:{}};
      }
      export function consumeFileHandoff(cwd) {
        checkScope(cwd);
        return {ok:true,id:"synthetic-file",payload:"Synthetic bound continuation."};
      }
      export function markDeliveryPromptInjected() {}
    `,
  };
}
