import { execFile } from "node:child_process";
import { closeSync, constants, fstatSync, openSync, readSync } from "node:fs";
import { isAbsolute, join, normalize } from "node:path";
import { promisify } from "node:util";

const execute = promisify(execFile);

function readBounded(path, limit) {
  const descriptor = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  try {
    const info = fstatSync(descriptor);
    if (!info.isFile() || info.size > limit) throw new Error("invalid-binding");
    const bytes = Buffer.alloc(limit + 1);
    const count = readSync(descriptor, bytes, 0, bytes.length, null);
    if (count > limit) throw new Error("invalid-binding");
    return bytes.subarray(0, count).toString("utf8");
  } finally {
    closeSync(descriptor);
  }
}

export async function ensureInstalledService({
  bindingPath = new URL("../../../../service-binding.json", import.meta.url),
  run = execute,
  platform = process.platform,
  onFailure,
} = {}) {
  let binding;
  try {
    binding = JSON.parse(readBounded(bindingPath, 16384));
  } catch (error) {
    return { state: error.code === "ENOENT" ? "not-configured" : "unavailable", reason: "binding" };
  }
  try {
    const { root, generation, argv } = binding;
    if (
      binding.schema !== "copilot-driver.service.startup" || binding.version !== 1
      || typeof root !== "string" || !isAbsolute(root)
      || typeof generation !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._+-]{0,255}$/.test(generation)
      || !Array.isArray(argv) || argv.some(item => typeof item !== "string" || /[\0\r\n]/.test(item))
    ) throw new Error("invalid-binding");
    const slot = join(root, "versions", generation);
    const expected = [
      "-I", "-S", "-B", join(slot, ".runtime-bootstrap", "runtime_launcher.py"),
      ...(platform === "win32" ? ["--gui-runtime"] : []),
      "--runtime-identity",
    ];
    if (
      argv.length !== expected.length + 6 || !isAbsolute(argv[0])
      || expected.some((item, index) => normalize(argv[index + 1]) !== normalize(item))
      || !/^[0-9a-f]{32}:[0-9a-f]{64}$/.test(argv[expected.length + 1])
      || argv.slice(-4).join("\n") !== "-m\nagent_remote_driver_app\nservice\nensure"
      || readBounded(join(root, "current-version"), 512).trim() !== generation
    ) throw new Error("invalid-binding");
    const result = await run(argv[0], argv.slice(1), {
      cwd: root, env: { ...process.env, AGENT_REMOTE_DRIVER_HOME: root },
      windowsHide: true, timeout: 12000, maxBuffer: 65536, encoding: "utf8",
    });
    const observed = JSON.parse(result.stdout);
    if (
      observed.contract !== "copilot-driver.service.commands" || observed.version !== 1
      || observed.state !== "running" || observed.generation !== generation
      || typeof observed.instance_id !== "string" || !observed.instance_id
      || !Number.isSafeInteger(observed.pid) || observed.pid <= 0
    ) throw new Error("unconfirmed-readiness");
    return { state: "ensured" };
  } catch (error) {
    try { onFailure?.(error); } catch { /* diagnostic observers cannot change ownership */ }
    return { state: "unavailable", reason: "ensure" };
  }
}
