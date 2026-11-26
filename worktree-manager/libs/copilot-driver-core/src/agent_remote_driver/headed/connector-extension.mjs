import { fileURLToPath } from "node:url";
import { activateBridgeExtension } from "./core/extension.mjs";
import { selectedNativeConnector } from "./core/native-connector.mjs";
import { selectedLoadout } from "./core/remote-driver.mjs";
import { loadExtensionSdk } from "./sdk-compat.mjs";
import { headedBinding, connectHeadedSession, headedDeliverySession } from "./connection.mjs";

const pluginRoot = fileURLToPath(new URL("../..", import.meta.url));
const selected = selectedNativeConnector({ pluginRoot, role: "connector" });
if (!selected) throw new Error("The Driver connector requires explicit launch selection");
const binding = headedBinding(process.env.COPILOT_DRIVER_HEADED_BINDING);
delete process.env.COPILOT_DRIVER_HEADED_BINDING;
if (binding.sessionId !== selected.sessionId) throw new Error("Native connector session changed");
const { joinSession } = await loadExtensionSdk();
let connection;
try {
  await activateBridgeExtension({
    pluginRoot, nativeConnector: selected,
    joinSession: async (options) => {
      const native = await joinSession(options);
      connection = await connectHeadedSession(native, binding, {
        validateSendContext: () => selectedLoadout(native, {
          pluginRoot: selected.driverPluginRoot, bridgePluginRoot: selected.bridgePluginRoot,
          expectedSessionId: selected.sessionId, cwd: selected.cwd,
          extensionPluginRoot: pluginRoot, nativeConnector: selected, extensionPid: process.pid,
        }),
      });
      return native;
    },
    createDeliverySession: (native) => headedDeliverySession(native, connection.adapter),
  });
} catch (error) {
  connection?.close();
  throw error;
}
