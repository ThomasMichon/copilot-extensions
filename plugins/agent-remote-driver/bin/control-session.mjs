import { readFileSync, writeFileSync, mkdirSync, openSync, closeSync, unlinkSync, lstatSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createControlClient } from "../extensions/agent-remote-driver/control-client.mjs";
import { writeDescriptorAtomic } from "../extensions/agent-remote-driver/discovery.mjs";

export async function controlSession({ descriptorPath, statePath, action, ownerId, content, mode }) {
  if (!["status", "claim", "re-hook", "send", "steer", "abort", "release"].includes(action)) {
    throw new Error("Unknown driver command");
  }
  const descriptorFile = resolve(descriptorPath);
  const descriptor = JSON.parse(readFileSync(descriptorFile, "utf8"));
  const client = createControlClient(descriptor);
  if (action === "status") return { ok: true, control: await client.health() };
  if (!statePath) throw new Error("A private --state file is required for ownership and mutations");
  const stateFile = resolve(statePath);
  const lockFile = `${stateFile}.lock`;
  if (stateFile === descriptorFile || lockFile === descriptorFile) {
    throw new Error("The claim state must not overwrite its discovery descriptor");
  }
  if (["send", "steer"].includes(action) && (typeof content !== "string" || !content)) {
    throw new Error("A nonempty --content-file is required");
  }
  if (mode !== undefined && !["enqueue", "immediate"].includes(mode)) throw new Error("Invalid send mode");
  mkdirSync(dirname(stateFile), { recursive: true, mode: 0o700 });
  let lock;
  try {
    lock = openSync(lockFile, "wx", 0o600);
  } catch (error) {
    if (error.code === "EEXIST") throw new Error("Claim state is locked; do not remove the lock while its owner may be running");
    throw error;
  }
  try {
    writeFileSync(lock, JSON.stringify({ pid: process.pid, createdAt: new Date().toISOString() }));
    let saved = null;
    try {
      if (!lstatSync(stateFile).isFile() || lstatSync(stateFile).isSymbolicLink()) {
        throw new Error("Claim state must be a regular, non-symlink file");
      }
      saved = JSON.parse(readFileSync(stateFile, "utf8"));
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    if (action === "claim" || action === "re-hook") {
      if (saved?.pending && action === "claim") {
        throw new Error("Previous outcome is unknown; deliberate re-hook is required");
      }
      writeDescriptorAtomic(stateFile, { version: 1, pending: true, operation: action });
      const lease = await client.grant(ownerId, { rehook: action === "re-hook" });
      writeDescriptorAtomic(stateFile, { version: 1, pending: false, lease, sequence: 0 });
      return { ok: true, control: await client.health(), stateSaved: true };
    }
    if (!saved?.lease || saved.pending || !Number.isSafeInteger(saved.sequence) || saved.sequence < 0) {
      throw new Error("No usable claim state; deliberate re-hook is required");
    }
    const sequence = saved.sequence + 1;
    if (!Number.isSafeInteger(sequence)) throw new Error("Sequence exhausted; deliberate re-hook is required");
    writeDescriptorAtomic(stateFile, { ...saved, sequence, pending: true, operation: action });
    const result = await client.invoke(action, saved.lease, sequence, content, mode);
    writeDescriptorAtomic(stateFile, {
      version: 1, pending: false, sequence,
      ...(action === "release" ? {} : { lease: saved.lease }),
    });
    return { ok: true, control: result.control, effects: result.effects, stateSaved: true };
  } finally {
    closeSync(lock);
    unlinkSync(lockFile);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const [action, ...args] = process.argv.slice(2);
    const options = {};
    for (let i = 0; i < args.length; i += 2) {
      if (!["--descriptor", "--state", "--owner", "--content-file", "--mode"].includes(args[i]) || !args[i + 1] ||
          Object.hasOwn(options, args[i])) throw new Error("Invalid command options");
      options[args[i]] = args[i + 1];
    }
    if (!options["--descriptor"]) throw new Error("--descriptor is required");
    const result = await controlSession({
      action, descriptorPath: options["--descriptor"], statePath: options["--state"],
      ownerId: options["--owner"], mode: options["--mode"],
      content: options["--content-file"] ? readFileSync(options["--content-file"], "utf8") : undefined,
    });
    console.log(JSON.stringify(result));
  } catch (error) {
    console.error(`Driver control failed: ${error.code || error.name || "error"}. Check the descriptor, private state and current ownership; never replay an uncertain mutation.`);
    process.exitCode = 1;
  }
}
