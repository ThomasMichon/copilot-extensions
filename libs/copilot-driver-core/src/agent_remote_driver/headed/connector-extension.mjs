import { fileURLToPath } from "node:url";
import { activateNativeConnector, selectedNativeConnector } from "./core/native-connector.mjs";

const pluginRoot = fileURLToPath(new URL("../..", import.meta.url));
const selected = selectedNativeConnector({ pluginRoot, role: "connector" });
if (!selected) throw new Error("The Driver connector requires explicit launch selection");
await activateNativeConnector({
  selected, pluginRoot, headedResources: new URL("./", import.meta.url),
});
