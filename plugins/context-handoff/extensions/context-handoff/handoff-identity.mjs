// Optional identity observations are evidence, never consume or head authority.
import {
  closeSync, constants, fstatSync, lstatSync, openSync, readSync, realpathSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const MAX_BINDING_BYTES = 16 * 1024;
const segment = (value) => typeof value === "string"
  && /^[A-Za-z0-9._-]{1,160}$/.test(value) && value !== "." && value !== "..";
const timestamp = (value) => typeof value === "string" && value.length <= 64
  && Number.isFinite(Date.parse(value));

function activationObservation(value) {
  if (value?.version !== 1 || !Number.isSafeInteger(value.ordinal) || value.ordinal <= 0
      || !timestamp(value.startedAt) || !timestamp(value.startRecordedAt)
      || typeof value.sourceIdentity !== "string"
      || !/^[A-Za-z0-9._:-]{1,128}$/.test(value.sourceIdentity)) return null;
  return {
    version: 1, ordinal: value.ordinal, startedAt: value.startedAt,
    startRecordedAt: value.startRecordedAt, sourceIdentity: value.sourceIdentity,
  };
}

export function captureHandoffIdentity(sessionId, worktreeId) {
  if (!segment(sessionId) || !segment(worktreeId)) return null;
  let fd;
  try {
    const root = realpathSync(join(homedir(), ".copilot", "session-state"));
    const dir = join(root, sessionId);
    if (!lstatSync(dir).isDirectory() || realpathSync(dir) !== dir) return null;
    const path = join(dir, "worktree-binding.json");
    if (!lstatSync(path).isFile()) return null;
    fd = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW || 0)
      | (constants.O_NONBLOCK || 0));
    const info = fstatSync(fd);
    if (!info.isFile() || info.size > MAX_BINDING_BYTES) return null;
    const bytes = Buffer.alloc(MAX_BINDING_BYTES + 1);
    let length = 0;
    while (length < bytes.length) {
      const count = readSync(fd, bytes, length, bytes.length - length, null);
      if (count === 0) break;
      length += count;
    }
    if (length > MAX_BINDING_BYTES) return null;
    const binding = JSON.parse(new TextDecoder("utf-8", { fatal: true })
      .decode(bytes.subarray(0, length)));
    if (binding?.sessionId !== sessionId || binding?.worktreeId !== worktreeId
        || !segment(binding.project) || !segment(binding.creationNonce)) return null;
    return {
      version: 1, project: binding.project, worktreeId,
      creationNonce: binding.creationNonce, sessionId,
      activation: activationObservation(binding.sessionActivation),
    };
  } catch {
    return null;
  } finally {
    if (fd !== undefined) {
      try { closeSync(fd); } catch { /* optional observation */ }
    }
  }
}
