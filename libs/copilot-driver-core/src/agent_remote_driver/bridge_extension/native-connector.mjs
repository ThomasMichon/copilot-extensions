import { realpathSync } from "node:fs";
import { isAbsolute } from "node:path";

export const NATIVE_CONNECTOR_ENV = "COPILOT_DRIVER_NATIVE_CONNECTOR";
export const NATIVE_CONNECTOR_CONTRACT = "driver-native-v1";

export function selectedNativeConnector({
  pluginRoot, role, env = process.env, cwd = process.cwd(),
}) {
  const raw = env[NATIVE_CONNECTOR_ENV];
  if (raw === undefined) return null;
  const invalid = () => new Error("Native connector differs from its explicit launch selection");
  let selected;
  try { selected = JSON.parse(raw); } catch { throw invalid(); }
  const fields = [
    "contract", "version", "sessionId", "cwd", "scopeId", "attempt",
    "bridgePluginRoot", "driverPluginRoot", "connectorPluginRoot",
  ];
  const roots = { bridge: "bridgePluginRoot", driver: "driverPluginRoot", connector: "connectorPluginRoot" };
  if (!selected || Object.keys(selected).length !== fields.length ||
      fields.some((name) => !Object.hasOwn(selected, name)) ||
      selected.contract !== NATIVE_CONNECTOR_CONTRACT || selected.version !== 1 ||
      typeof selected.sessionId !== "string" || selected.sessionId !== env.SESSION_ID ||
      selected.sessionId !== env.AGENT_BRIDGE_NATIVE_SESSION_ID ||
      selected.scopeId !== env.AGENT_BRIDGE_SCOPE_ID || !selected.scopeId ||
      selected.attempt !== env.AGENT_BRIDGE_PORTABLE_ATTEMPT || !selected.attempt ||
      selected.bridgePluginRoot !== env.AGENT_BRIDGE_RECEIVING_PLUGIN_ROOT ||
      selected.driverPluginRoot !== env.AGENT_BRIDGE_REMOTE_DRIVER_ROOT ||
      !Object.hasOwn(roots, role) ||
      ![cwd, pluginRoot, selected.cwd, ...Object.values(roots).map((key) => selected[key])]
        .every((value) => typeof value === "string" && isAbsolute(value))) {
    throw invalid();
  }
  try {
    if (realpathSync(cwd) !== realpathSync(selected.cwd) ||
        realpathSync(pluginRoot) !== realpathSync(selected[roots[role]])) throw invalid();
  } catch { throw invalid(); }
  return selected;
}
