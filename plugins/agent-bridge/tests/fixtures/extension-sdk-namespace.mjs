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
  const source = readFileSync(entry, "utf8");
  await link(source, pathToFileURL(entry).href);
  const start = source.indexOf("async function importExtensionSdk(");
  assert.notEqual(start, -1, `${entry} must preserve mixed-loader compatibility`);
  const end = source.indexOf("\n}", start) + 2;
  assert.ok(end > start);
  const helper = source.slice(start, end);
  for (const available of ["microsoft", "github", "both", "dependency-failure"]) {
    const calls = [];
    const dependencyFailure = Object.assign(new Error(
      "Cannot find package 'missing-sdk-dependency' imported from sdk/index.js",
    ), { code: "ERR_MODULE_NOT_FOUND" });
    const module = new vm.SourceTextModule(`${helper}\nexport { importExtensionSdk };`, {
      identifier: pathToFileURL(entry).href,
      importModuleDynamically: async (specifier) => {
        calls.push(specifier);
        const modern = specifier.startsWith("@microsoft/");
        if (modern && available === "dependency-failure") throw dependencyFailure;
        if ((modern && available === "github") || (!modern && available === "microsoft")) {
          const namespace = modern ? "microsoft" : "github";
          throw Object.assign(new Error(
            `Cannot find package '@${namespace}/copilot-sdk' imported from fixture.mjs`,
          ), { code: "ERR_MODULE_NOT_FOUND" });
        }
        const exports = specifier.endsWith("/extension")
          ? { joinSession: forbidden } : { approveAll: forbidden };
        const result = synthetic(exports);
        await result.link(() => assert.fail("synthetic SDK must have no dependencies"));
        await result.evaluate();
        return result;
      },
    });
    await module.link(() => assert.fail("isolated helper must have no static imports"));
    await module.evaluate();
    for (const subpath of ["", "/extension"]) {
      calls.length = 0;
      if (available === "dependency-failure") {
        await assert.rejects(module.namespace.importExtensionSdk(subpath),
          (error) => error === dependencyFailure);
        assert.deepEqual(calls, [`@microsoft/copilot-sdk${subpath}`]);
      } else {
        const exports = await module.namespace.importExtensionSdk(subpath);
        assert.equal(typeof exports[subpath ? "joinSession" : "approveAll"], "function");
        assert.deepEqual(calls, available === "github"
          ? [`@microsoft/copilot-sdk${subpath}`, `@github/copilot-sdk${subpath}`]
          : [`@microsoft/copilot-sdk${subpath}`]);
      }
    }
  }
}
console.log(`Linked ${entries.length} extension entrypoints; verified Microsoft/GitHub SDK compatibility`);
