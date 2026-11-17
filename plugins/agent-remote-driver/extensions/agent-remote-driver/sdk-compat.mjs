function groupedOptions(options) {
  if (options === undefined) return undefined;
  const { tools, hooks, onPermissionRequest, commands, ...rest } = options;
  const result = { ...rest };
  if (tools !== undefined) {
    result.tools = Array.isArray(tools) ? { definitions: tools } : tools;
  }
  if (hooks !== undefined) {
    const handlers = Object.fromEntries(
      Object.entries(hooks).filter(([key]) => /^on[A-Z]/.test(key)),
    );
    if (Object.keys(handlers).length) {
      if (hooks.handlers !== undefined) throw new TypeError("Conflicting SDK hook options");
      result.hooks = {
        ...Object.fromEntries(Object.entries(hooks).filter(([key]) => !/^on[A-Z]/.test(key))),
        handlers,
      };
    } else {
      result.hooks = hooks;
    }
  }
  if (onPermissionRequest !== undefined) {
    if (rest.permissions?.onRequest !== undefined) {
      throw new TypeError("Conflicting SDK permission options");
    }
    result.permissions = { ...rest.permissions, onRequest: onPermissionRequest };
  }
  if (commands !== undefined) {
    if (rest.interaction?.commands !== undefined) {
      throw new TypeError("Conflicting SDK command options");
    }
    result.interaction = { ...rest.interaction, commands };
  }
  return result;
}

export async function loadExtensionSdk(importModule = (specifier) => import(specifier)) {
  let namespace = "@microsoft/copilot-sdk";
  let extension;
  try {
    extension = await importModule(`${namespace}/extension`);
  } catch (error) {
    // A broken current SDK must not silently fall back to another SDK.
    if (error?.code !== "ERR_MODULE_NOT_FOUND" ||
        !/^Cannot find (?:package ['"]@microsoft\/copilot-sdk['"]|module ['"]@microsoft\/copilot-sdk\/extension['"])(?: imported from |$)/.test(error.message)) {
      throw error;
    }
    namespace = "@github/copilot-sdk";
    extension = await importModule(`${namespace}/extension`);
  }
  const sdk = await importModule(namespace);
  return {
    ...sdk,
    joinSession: (options) => extension.joinSession(
      namespace === "@microsoft/copilot-sdk" ? groupedOptions(options) : options,
    ),
  };
}
