import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { constants, closeSync, fstatSync, lstatSync, openSync, readSync } from "node:fs";
import { homedir } from "node:os";
import { isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../../", import.meta.url));
const DIRECTORY = join(ROOT, "scripts", "command-errors");
const NAMES = [
  "command-causes.tsv", "command-errors.ps1", "command-errors.sh",
  "private-paths.ps1", "private-paths.sh",
];
const HASH = /^[a-f0-9]{64}$/;
const TIMEOUT_MS = 5_000;

function ordinary(path) {
  const suffix = relative(ROOT, path);
  if (isAbsolute(suffix) || suffix.split(sep).includes("..")) throw new Error("Unowned native assets");
  let current = ROOT;
  for (const part of ["", ...suffix.split(sep)]) {
    current = part ? join(current, part) : current;
    const info = lstatSync(current);
    if (info.isSymbolicLink() || (current === path ? !info.isFile() : !info.isDirectory())) {
      throw new Error("Redirected native assets");
    }
  }
}

function boundedFile(path, limit) {
  ordinary(path);
  const descriptor = openSync(path,
    constants.O_RDONLY | (constants.O_NOFOLLOW || 0) | (constants.O_NONBLOCK || 0));
  try {
    const info = fstatSync(descriptor);
    if (!info.isFile() || info.size > limit) throw new Error("Unsupported native resource");
    const buffer = Buffer.alloc(limit + 1);
    let length = 0;
    while (length < buffer.length) {
      const count = readSync(descriptor, buffer, length, buffer.length - length, null);
      if (count === 0) break;
      length += count;
    }
    if (length > limit) throw new Error("Oversized native resource");
    return buffer.subarray(0, length);
  } finally {
    closeSync(descriptor);
  }
}

function support(cause) {
  const index = JSON.parse(boundedFile(join(DIRECTORY, "index.json"), 16_384));
  if (
    index?.schema !== "copilot-extensions.native-command-errors" || index.version !== 1 ||
    index.consumer !== "agent-remote-driver" || typeof index.bundle !== "string" ||
    !HASH.test(index.bundle) || !index.files || Array.isArray(index.files) ||
    Object.keys(index.files).sort().join("\n") !== NAMES.join("\n")
  ) throw new Error("Unsupported native resource descriptor");
  const digests = {};
  const files = {};
  for (const name of NAMES) {
    if (typeof index.files[name] !== "string" || !HASH.test(index.files[name])) {
      throw new Error("Invalid native resource digest");
    }
    const path = join(DIRECTORY, index.bundle, name);
    const content = boundedFile(path, 262_144);
    const digest = createHash("sha256").update(content).digest("hex");
    if (digest !== index.files[name]) throw new Error("Incomplete native resource bundle");
    digests[name] = digest;
    files[name] = { path, content };
  }
  if (createHash("sha256").update(JSON.stringify(digests)).digest("hex") !== index.bundle) {
    throw new Error("Invalid native resource selection");
  }
  if (!files["command-causes.tsv"].content.toString("ascii").split("\n").some(
    (line) => line.split("\t", 1)[0] === cause,
  )) throw new Error("Unsupported native cause catalog");
  return { files, bundle: index.bundle };
}

function quoted(value) {
  return "'" + value.replaceAll("'", "''") + "'";
}

export function controlFailureCause(error) {
  const known = {
    descriptor_invalid: "driver-descriptor-invalid",
    transport_outcome_unknown: "driver-transport-unknown",
    target_changed: "driver-target-changed",
    claim_target_changed: "driver-target-changed",
    control_credential_missing: "driver-credential-missing",
    grant_outcome_unknown: "driver-grant-unknown",
    generation_stale: "driver-ownership-changed",
    driver_revoked: "driver-ownership-revoked",
    native_control_failed: "driver-native-outcome-unknown",
    action_invalid: "driver-invalid-arguments",
    request_failed: "driver-request-refused",
    ENOENT: "driver-input-unavailable",
    ENOTDIR: "driver-input-unavailable",
    EACCES: "driver-input-unavailable",
    EPERM: "driver-input-unavailable",
    EIO: "driver-input-unavailable",
  };
  if (typeof error?.code === "string" && Object.hasOwn(known, error.code)) return known[error.code];
  if (error instanceof SyntaxError) return "driver-input-invalid";
  if (Number.isInteger(error?.status) && error.status >= 100 && error.status <= 599) {
    return "driver-request-refused";
  }
  if (error?.message === "Claim state is locked; do not remove the lock while its owner may be running") {
    return "driver-state-locked";
  }
  if ([
    "Previous outcome is unknown; deliberate re-hook is required",
    "No usable claim state; deliberate re-hook is required",
    "Sequence exhausted; deliberate re-hook is required",
  ].includes(error?.message)) return "driver-state-pending";
  if ([
    "Unknown driver command", "Invalid command options", "--descriptor is required",
    "A private --state file is required for ownership and mutations",
    "A nonempty --content-file is required", "Invalid send mode",
  ].includes(error?.message)) return "driver-invalid-arguments";
  return "driver-command-failed";
}

export function recordCommandFailure(command, cause) {
  // This bounded synchronous path belongs only to a failed standalone CLI.
  const deadline = Date.now() + TIMEOUT_MS;
  try {
    if (!["remote-driver-control-session", "remote-driver-list-sessions"].includes(command)) {
      return { status: "unavailable" };
    }
    const { files, bundle } = support(cause);
    const source = `agent-remote-driver-cli.${bundle}`;
    const directory = resolve(homedir(), ".copilot-extensions", "logs", "diagnostics", "commands");
    let executable;
    let args;
    let environment;
    if (process.platform === "win32") {
      const previous = "$previous=[Console]::Error;$capture=[IO.StringWriter]::new();[Console]::SetError($capture);";
      const invocation = ". " + quoted(files["command-errors.ps1"].path) + ";Write-CommandErrorEvidence" +
        " -Command " + quoted(command) + " -Source " + quoted(source) +
        " -Cause " + quoted(cause) + " -Directory " + quoted(directory);
      // ConsoleHost can retain stderr through shutdown; keep its managed capture alive.
      const script = "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';" + previous +
        "try{$output=&{" + invocation + "} *>&1;" +
        "if($output -or $capture.GetStringBuilder().Length){exit 75}}" +
        "catch{exit 75}finally{[Console]::SetError($previous)};exit 0";
      executable = join(process.env.SystemRoot || "C:\\Windows", "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
      args = ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64")];
    } else if (process.platform === "linux") {
      environment = { ...process.env };
      delete environment.BASH_ENV;
      delete environment.ENV;
      const work = Math.min(TIMEOUT_MS, deadline - Date.now()) - 250;
      if (work <= 0) return { status: "incomplete" };
      // The writer uses subshells/GNU tools: retain a private group on timeout and caller loss.
      executable = "setpriv";
      args = ["--pdeathsig=TERM", "--", "timeout", "--signal=TERM", "--kill-after=0.25s",
        (work / 1000).toFixed(3) + "s", "bash", "--noprofile", "--norc", "-p", "-c",
        'output=$({ . "$1" && command_error_write "$2" "$3" "$4" null "$5"; } 2>&1) || exit 75; ' +
        '[ -z "$output" ] || exit 75',
        "native-evidence", files["command-errors.sh"].path, command, source, cause, directory];
    } else {
      return { status: "unavailable" };
    }
    const remaining = Math.min(TIMEOUT_MS, deadline - Date.now());
    if (remaining <= 0) return { status: "incomplete" };
    const result = spawnSync(executable, args, {
      // libuv retains non-detached Windows children in its caller-owned kill-on-close Job.
      detached: false, windowsHide: true, stdio: "ignore", timeout: remaining, env: environment,
    });
    if (result.error?.code === "ETIMEDOUT" ||
        process.platform === "linux" && [124, 137].includes(result.status)) return { status: "incomplete" };
    return { status: !result.error && result.status === 0 ? "recorded" : "unavailable" };
  } catch {
    return { status: "unavailable" };
  }
}
