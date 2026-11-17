import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { builtinModules } from "node:module";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import vm from "node:vm";

const root = process.argv[2];
const entries = [];
for (const plugin of readdirSync(join(root, "plugins"), { withFileTypes: true })) {
  if (!plugin.isDirectory()) continue;
  const extensions = join(root, "plugins", plugin.name, "extensions");
  let names;
  try {
    names = readdirSync(extensions, { recursive: true });
  } catch (error) {
    if (error.code === "ENOENT") continue;
    throw error;
  }
  for (const name of names) {
    if (/^extension\.(mjs|cjs|js)$/.test(name.split(/[\\/]/).at(-1))) {
      entries.push(join(extensions, name));
    }
  }
}
entries.push(join(root, "libs/copilot-driver-core/src/agent_remote_driver/headed/extension.mjs"));
assert.ok(entries.length >= 6, "discover all plugin extensions and the shared headed entrypoint");

const sdkImports = new Set();
const synthetic = (exports) => new vm.SyntheticModule(Object.keys(exports), function () {
  for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
});
const forbidden = () => assert.fail("link-only fixture must not execute an extension");
const sdk = {
  "@microsoft/copilot-sdk": { approveAll: forbidden },
  "@microsoft/copilot-sdk/extension": { joinSession: forbidden },
};
async function link(source, identifier) {
  const modules = new Map();
  const load = (url) => {
    if (!modules.has(url)) {
      modules.set(url, new vm.SourceTextModule(readFileSync(fileURLToPath(url), "utf8"), {
        identifier: url,
      }));
    }
    return modules.get(url);
  };
  const module = new vm.SourceTextModule(source, { identifier });
  await module.link(async (specifier, parent) => {
    if (Object.hasOwn(sdk, specifier)) {
      sdkImports.add(specifier);
      return synthetic(sdk[specifier]);
    }
    if (builtinModules.includes(specifier) || specifier.startsWith("node:")) {
      return synthetic(await import(specifier));
    }
    if (specifier.startsWith(".")) return load(new URL(specifier, parent.identifier).href);
    throw Object.assign(new Error(`Unresolved extension dependency: ${specifier}`), {
      code: "ERR_MODULE_NOT_FOUND",
    });
  });
  // Deliberately never evaluate: linking proves resolution without SDK calls,
  // filesystem writes, session registration, subprocesses or network activity.
  assert.equal(module.status, "linked");
}

for (const specifier of ["@github/copilot-sdk", "@github/copilot-sdk/extension"]) {
  await assert.rejects(link(`import ${JSON.stringify(specifier)};`, import.meta.url), {
    code: "ERR_MODULE_NOT_FOUND",
  });
}
for (const entry of entries) {
  await link(readFileSync(entry, "utf8"), pathToFileURL(entry).href);
}
assert.deepEqual([...sdkImports].sort(), Object.keys(sdk).sort());
console.log(`Linked ${entries.length} extension entrypoints with Microsoft-only SDK resolution`);
