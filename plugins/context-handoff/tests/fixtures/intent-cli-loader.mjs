const cli = new URL("../../extensions/context-handoff/handoff-cli.mjs", import.meta.url).href;
const core = new URL("../../extensions/context-handoff/handoff-core.mjs", import.meta.url).href;

export async function resolve(specifier, context, nextResolve) {
  if (context.parentURL === cli && specifier === "./handoff-core.mjs") {
    return { url: "intent-test:core", shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url !== "intent-test:core") return nextLoad(url, context);
  return {
    format: "module", shortCircuit: true,
    source: `
      export * from ${JSON.stringify(core)};
      const bound = process.env.FIXTURE_BOUND_CWD;
      export function runCli(_bin, args, options) {
        if (options.allowProvision !== false) throw new Error("provisioning forbidden");
        if (args[0] === "get") {
          if (args[1] !== "worktree-dir" || args[3] !== "fixture-bound-session")
            throw new Error("binding query mismatch");
          return bound;
        }
        if (args[0] === "effort-focus") return JSON.stringify({
          active_effort: {active:true,state:"open",path:"effort.md"}
        });
        if (args[0] === "state-root") return JSON.stringify({
          state_root:bound,requires_external:false,bound:true
        });
        throw new Error("unexpected management command");
      }
      export async function waitForWorktreeSyncToSettle(cwd) {
        if (cwd !== bound) throw new Error("wrong sync scope");
        return {settled:true,waited:false};
      }
      export function consumeFileHandoff(cwd) {
        if (cwd !== bound) throw new Error("wrong consume scope");
        return {ok:true,id:"fixture-handoff",payload:"Continue the assigned objective."};
      }
    `,
  };
}
