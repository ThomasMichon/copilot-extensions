import { test } from "node:test";
import assert from "node:assert/strict";
import {
  appendFileSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

const PLUGIN = fileURLToPath(new URL("../", import.meta.url));
const ROOT = fileURLToPath(new URL("../../../", import.meta.url));

function fixture(fn) {
  const root = mkdtempSync(join(tmpdir(), "driver-dx-"));
  const payload = join(root, "p");
  let deferredCleanup = false;
  try {
    cpSync(PLUGIN, payload, {
      recursive: true,
      filter: (path) => path !== join(PLUGIN, "tests"),
    });
    const prepare = fileURLToPath(new URL("./prepare_command_evidence.py", import.meta.url));
    const command = process.platform === "win32" ? "powershell.exe" : join(ROOT, "tools/run-python");
    const args = process.platform === "win32" ? [
      "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", join(ROOT, "tools/run-python.ps1"),
      prepare, payload,
    ] : [prepare, payload];
    const prepared = spawnSync(command, args, {
      cwd: ROOT, windowsHide: true, encoding: "utf8", timeout: 30_000,
    });
    assert.equal(prepared.error, undefined);
    assert.equal(prepared.status, 0, prepared.stderr);
    const result = fn({ root, payload });
    if (result instanceof Promise) {
      deferredCleanup = true;
      return result.finally(() => rmSync(root, { recursive: true, force: true }));
    }
    return result;
  } finally {
    if (!deferredCleanup) rmSync(root, { recursive: true, force: true });
  }
}

for (const stalled of [false, true]) {
  test(`Linux ${stalled ? "deadline" : "caller loss"} retires native helpers before outer cleanup`, {
    skip: process.platform !== "linux", timeout: 15_000,
  }, async () => {
    await fixture(async ({ root, payload }) => {
      const directory = join(root, ".copilot-extensions", "logs", "diagnostics", "commands");
      mkdirSync(directory, { recursive: true, mode: 0o700 });
      const lock = join(directory, ".command-errors.lock");
      writeFileSync(lock, "", { mode: 0o600 });
      if (stalled) appendNativeFixtureScript(payload, "command-errors.sh", "\nsleep 60\n");
      const holder = spawn("flock", ["-x", "-F", lock, "bash", "--noprofile", "--norc", "-c",
        'printf "ready\\n"; IFS= read -r'], { windowsHide: true, stdio: ["pipe", "pipe", "pipe"] });
      holder.stdin.on("error", () => {});
      const holderExit = once(holder, "exit");
      let command;
      let commandExit;
      const known = new Map();
      function identity(pid) {
        try {
          const raw = readFileSync(`/proc/${pid}/stat`, "utf8");
          const fields = raw.slice(raw.lastIndexOf(")") + 2).trim().split(/\s+/);
          return {
            pid, name: raw.slice(raw.indexOf("(") + 1, raw.lastIndexOf(")")),
            state: fields[0], parent: Number(fields[1]), group: Number(fields[2]),
            session: Number(fields[3]), birth: fields[19],
          };
        } catch (error) {
          if (["ENOENT", "ESRCH"].includes(error.code)) return null;
          throw error;
        }
      }
      function capture() {
        for (const previous of [...known.values()]) {
          const current = identity(previous.pid);
          if (!current || current.birth !== previous.birth) continue;
          let children;
          try {
            children = readFileSync(`/proc/${current.pid}/task/${current.pid}/children`, "utf8")
              .trim().split(/\s+/).filter(Boolean).map(Number);
          } catch (error) {
            if (["ENOENT", "ESRCH"].includes(error.code)) continue;
            throw error;
          }
          for (const pid of children) {
            const child = identity(pid);
            if (child?.parent === current.pid && child.session === command.pid) known.set(pid, child);
            assert.ok(known.size <= 128, "fixture process observation exceeded its bound");
          }
        }
      }
      function active() {
        return [...known.values()].filter((previous) => {
          const current = identity(previous.pid);
          return current?.birth === previous.birth && !["Z", "X"].includes(current.state);
        });
      }
      try {
        const [ready] = await Promise.race([
          once(holder.stdout, "data"),
          delay(2000, undefined, { ref: false }).then(() => { throw new Error("fixture lock was not ready"); }),
        ]);
        assert.match(ready.toString(), /^ready/);
        // The fixture owns this session; it is not a detached venue daemon.
        command = spawn(process.execPath, [join(payload, "bin", "control-session.mjs"), "status"], {
          detached: true, windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
          env: { ...process.env, HOME: root, USERPROFILE: root },
        });
        command.stdout.resume();
        let stderr = "";
        command.stderr.on("data", (chunk) => { stderr += chunk; });
        commandExit = once(command, "exit");
        const original = identity(command.pid);
        assert.equal(original.session, command.pid);
        known.set(command.pid, original);
        const waiterDeadline = performance.now() + 1500;
        while (![...known.values()].some((row) => row.name === (stalled ? "sleep" : "flock"))) {
          capture();
          assert.ok(performance.now() < waiterDeadline, "the actual native lock waiter was not observed");
          await delay(5);
        }
        if (!stalled) command.kill("SIGKILL");
        await commandExit;
        if (stalled) {
          assert.equal(command.exitCode, 1);
          assert.equal(JSON.parse(stderr).diagnostics.status, "incomplete");
        }
        const retirementDeadline = performance.now() + 500;
        while (active().length && performance.now() < retirementDeadline) {
          capture();
          await delay(5);
        }
        assert.equal(active().length, 0, "native helpers survived the original caller's loss");
        assert.equal(existsSync(join(directory, "command-errors.jsonl")), false);
      } finally {
        if (command && command.exitCode === null && command.signalCode === null) command.kill("SIGKILL");
        if (commandExit) await commandExit;
        capture();
        try {
          const groups = active().map((row) => {
            const current = identity(row.pid);
            return current?.birth === row.birth && current.session === command.pid ? current.group : null;
          }).filter(Boolean);
          for (const group of new Set(groups)) {
            assert.notEqual(group, process.pid);
            try { process.kill(-group, "SIGKILL"); } catch (error) {
              if (error.code !== "ESRCH") throw error;
            }
          }
          const cleanupDeadline = performance.now() + 500;
          while (active().length && performance.now() < cleanupDeadline) await delay(5);
          assert.equal(active().length, 0, "owned fixture cleanup did not settle");
        } finally {
          holder.stdin.end("release\n");
          await holderExit;
        }
      }
    });
  });
}

function cli(payload, root, entry, args, extraEnv = {}, execArgv = []) {
  const result = spawnSync(process.execPath, [...execArgv, join(payload, "bin", entry), ...args], {
    windowsHide: true, encoding: "utf8", timeout: 12_000,
    env: { ...process.env, HOME: root, USERPROFILE: root, ...extraEnv },
  });
  assert.equal(result.error, undefined);
  return result;
}

function evidence(root) {
  const directory = join(root, ".copilot-extensions", "logs", "diagnostics", "commands");
  return readdirSync(directory).filter((name) => name.endsWith(".jsonl"))
    .flatMap((name) => readFileSync(join(directory, name), "utf8").trim().split("\n").map(JSON.parse));
}

function appendNativeFixtureScript(payload, script, content) {
  const support = join(payload, "scripts", "command-errors");
  const indexPath = join(support, "index.json");
  const index = JSON.parse(readFileSync(indexPath, "utf8"));
  const old = join(support, index.bundle);
  appendFileSync(join(old, script), content);
  const contents = {};
  const digests = {};
  for (const name of Object.keys(index.files).sort()) {
    contents[name] = readFileSync(join(old, name));
    digests[name] = createHash("sha256").update(contents[name]).digest("hex");
  }
  index.files = digests;
  index.bundle = createHash("sha256").update(JSON.stringify(digests)).digest("hex");
  const selected = join(support, index.bundle);
  mkdirSync(selected);
  for (const [name, data] of Object.entries(contents)) writeFileSync(join(selected, name), data);
  writeFileSync(indexPath, JSON.stringify(index));
}

test("the actual control CLI retains a safe cause without changing its error JSON or result", () => {
  fixture(({ root, payload }) => {
    const canary = "private-input-canary-never-retain";
    const result = cli(payload, root, "control-session.mjs", [
      "status", "--descriptor", join(root, canary),
    ]);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    const response = JSON.parse(result.stderr);
    assert.equal(response.ok, false);
    assert.equal(response.code, "ENOENT");
    assert.equal(response.diagnostics.status, "recorded");
    assert.match(response.recovery, /never replay/);
    const records = evidence(root);
    assert.equal(records.length, 1);
    assert.equal(records[0].command, "remote-driver-control-session");
    assert.equal(records[0].code, "driver-input-unavailable");
    assert.equal(records[0].exit_code, null);
    const index = JSON.parse(readFileSync(join(payload, "scripts", "command-errors", "index.json"), "utf8"));
    assert.equal(records[0].source, `agent-remote-driver-cli.${index.bundle}`);
    assert.equal(JSON.stringify(records).includes(canary), false);
    assert.equal(JSON.stringify(records).includes(join(root, canary)), false);
  });
});

test("the actual listing CLI retains registry failure rather than reporting an empty fleet", () => {
  fixture(({ root, payload }) => {
    const blocked = join(root, "blocked");
    mkdirSync(blocked);
    writeFileSync(join(blocked, "sessions"), "fixture-owned non-directory");
    const result = cli(payload, root, "list-sessions.mjs", ["--json", "--no-probe"], {
      AGENT_REMOTE_DRIVER_CONFIG_DIR: blocked,
    });
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /could not read the session registry/);
    assert.doesNotMatch(result.stderr, /diagnostic evidence unavailable/);
    const records = evidence(root);
    assert.equal(records.length, 1);
    assert.equal(records[0].command, "remote-driver-list-sessions");
    assert.equal(records[0].code, "driver-registry-unavailable");
    assert.equal(records[0].exit_code, null);
    assert.equal(JSON.stringify(records).includes(blocked), false);
  });
});

test("an incomplete native bundle preserves primary diagnostics and exposes unavailable evidence", () => {
  fixture(({ root, payload }) => {
    const support = join(payload, "scripts", "command-errors");
    const index = JSON.parse(readFileSync(join(support, "index.json"), "utf8"));
    appendFileSync(join(support, index.bundle, "command-causes.tsv"), "changed");
    const result = cli(payload, root, "control-session.mjs", ["status"]);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    const response = JSON.parse(result.stderr);
    assert.equal(response.ok, false);
    assert.equal(response.code, "Error");
    assert.equal(response.diagnostics.status, "unavailable");
    assert.equal(existsSync(join(root, ".copilot-extensions")), false);
  });
});

test("Linux inherited shell startup configuration cannot skip evidence or alter its result", {
  skip: process.platform !== "linux",
}, () => {
  fixture(({ root, payload }) => {
    const startup = join(root, "shell-startup");
    const marker = join(root, "startup-ran");
    writeFileSync(startup, `printf ran > '${marker.replaceAll("'", "'\\''")}';exit 0\n`);
    const result = cli(payload, root, "control-session.mjs", ["status"], {
      BASH_ENV: startup, ENV: startup,
      "BASH_FUNC_command_error_write%%": "() { return 0; }",
    });
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.equal(JSON.parse(result.stderr).diagnostics.status, "recorded");
    assert.equal(evidence(root).length, 1);
    assert.equal(existsSync(marker), false);
  });
});

for (const mode of ["index", "member", "replacement race"]) {
  test(`Linux ${mode} FIFO cannot block the primary command error`, {
    skip: process.platform !== "linux",
  }, () => {
    fixture(({ root, payload }) => {
      const directory = join(payload, "scripts", "command-errors");
      const indexPath = join(directory, "index.json");
      const index = JSON.parse(readFileSync(indexPath, "utf8"));
      const target = mode === "member"
        ? join(directory, index.bundle, "command-causes.tsv") : indexPath;
      const fifo = mode === "replacement race" ? join(root, "replacement") : target;
      if (fifo === target) rmSync(target);
      const prepared = spawnSync("mkfifo", [fifo], { encoding: "utf8", timeout: 1000 });
      assert.equal(prepared.status, 0, prepared.stderr);
      let execArgv = [];
      if (mode === "replacement race") {
        const preload = join(root, "replace-before-open.mjs");
        writeFileSync(preload, `
import fs from "node:fs";
import { syncBuiltinESMExports } from "node:module";
const original = fs.openSync;
let replaced = false;
fs.openSync = function(path, ...args) {
  if (!replaced && path === ${JSON.stringify(target)}) {
    replaced = true;
    fs.renameSync(${JSON.stringify(fifo)}, path);
  }
  return Reflect.apply(original, this, [path, ...args]);
};
syncBuiltinESMExports();
`);
        execArgv = ["--import", pathToFileURL(preload).href];
      }
      const start = performance.now();
      const result = cli(payload, root, "control-session.mjs", ["status"], {}, execArgv);
      assert.equal(result.status, 1);
      assert.equal(result.stdout, "");
      assert.equal(JSON.parse(result.stderr).diagnostics.status, "unavailable");
      assert.ok(performance.now() - start < 5000, "native asset admission blocked the primary failure");
      assert.equal(existsSync(join(root, ".copilot-extensions")), false);
    });
  });
}

test("per-session probe failures stay successful reporting data rather than command errors", () => {
  fixture(({ root, payload }) => {
    const registry = join(root, "registry");
    mkdirSync(join(registry, "sessions"), { recursive: true });
    writeFileSync(join(registry, "sessions", "fixture.json"), JSON.stringify({
      version: 1, sessionId: "fixture", pid: process.pid, updatedAt: new Date().toISOString(),
      host: "[invalid]", port: 1, token: "fixture-observation-value",
    }));
    const result = cli(payload, root, "list-sessions.mjs", ["--json"], {
      AGENT_REMOTE_DRIVER_CONFIG_DIR: registry,
    });
    assert.equal(result.status, 0);
    assert.equal(result.stderr, "");
    const rows = JSON.parse(result.stdout);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].reachable, false);
    assert.equal(existsSync(join(root, ".copilot-extensions")), false);
  });
});

test("successful commands add no helper handoff and a failed command adds at most one", () => {
  fixture(({ root, payload }) => {
    const preload = join(root, "count-children.mjs");
    const counter = join(root, "child-count.json");
    writeFileSync(preload, `
import children from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { writeFileSync } from "node:fs";
let count = 0;
for (const name of ["spawn", "spawnSync", "exec", "execSync", "execFile", "execFileSync", "fork"]) {
  const original = children[name];
  children[name] = function(...args) { count += 1; return Reflect.apply(original, this, args); };
}
syncBuiltinESMExports();
process.once("exit", () => writeFileSync(new URL("./child-count.json", import.meta.url), JSON.stringify({ count })));
`);
    const execArgv = ["--import", pathToFileURL(preload).href];
    const success = cli(payload, root, "list-sessions.mjs", ["--json", "--no-probe"], {
      AGENT_REMOTE_DRIVER_CONFIG_DIR: join(root, "empty-registry"),
    }, execArgv);
    assert.equal(success.status, 0, success.stderr);
    assert.deepEqual(JSON.parse(success.stdout), []);
    assert.equal(JSON.parse(readFileSync(counter)).count, 0);
    const failure = cli(payload, root, "control-session.mjs", ["status"], {}, execArgv);
    assert.equal(failure.status, 1);
    assert.equal(JSON.parse(readFileSync(counter)).count, 1);
  });
});

test("unavailable native storage preserves the primary failure without replacing its obstacle", () => {
  fixture(({ root, payload }) => {
    const obstacle = join(root, ".copilot-extensions");
    writeFileSync(obstacle, "fixture-owned storage obstacle");
    const result = cli(payload, root, "control-session.mjs", ["status"]);
    assert.equal(result.status, 1);
    assert.equal(result.stdout, "");
    assert.equal(JSON.parse(result.stderr).diagnostics.status, "unavailable");
    assert.equal(readFileSync(obstacle, "utf8"), "fixture-owned storage obstacle");
  });
});

for (const mode of ["actual writer", "timeout", "caller loss", "stalled caller loss"]) {
  test(`Windows native writer has no workload descendants and retires before outer cleanup (${mode})`, {
    skip: process.platform !== "win32",
  }, () => {
    fixture(({ root, payload }) => {
      const delayed = mode === "timeout" || mode === "stalled caller loss";
      const cancelled = mode === "caller loss" || mode === "stalled caller loss";
      const expected = cancelled ? "caller-cancelled" : delayed ? "incomplete" : "recorded";
      if (delayed) {
        const ready = join(root, "helper-stalled").replaceAll("'", "''");
        appendNativeFixtureScript(payload, "command-errors.ps1",
          `\n[IO.File]::WriteAllText('${ready}', 'ready');Start-Sleep -Seconds 8\n`);
      }
      const observer = fileURLToPath(new URL("./fixtures/observe-command-timeout.ps1", import.meta.url));
      const result = spawnSync("powershell.exe", [
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", observer,
        "-Node", process.execPath, "-Cli", join(payload, "bin", "control-session.mjs"),
        "-HomeDirectory", root, "-ExpectedStatus", expected,
        ...(cancelled ? ["-CancelCaller"] : []),
        ...(delayed && cancelled ? ["-AwaitHelperDeadline"] : []),
      ], { windowsHide: true, encoding: "utf8", timeout: 20_000 });
      assert.equal(result.error, undefined);
      assert.equal(result.status, 0, result.stderr);
      const receipt = JSON.parse(result.stdout);
      if (!cancelled) assert.equal(receipt.primaryExitCode, 1);
      assert.equal(receipt.helperStatus, expected);
      const names = [...receipt.helperNames, ...receipt.descendantNames].map((name) => name.toLowerCase());
      assert.equal(names.filter((name) => name === "powershell.exe").length, 1, JSON.stringify(receipt));
      assert.equal(names.every((name) => ["powershell.exe", "conhost.exe"].includes(name)), true, JSON.stringify(receipt));
      assert.ok(names.filter((name) => name === "conhost.exe").length <= 2, JSON.stringify(receipt));
      assert.equal(receipt.helpersStillAlive, 0);
      if (delayed && cancelled) assert.equal(receipt.helperReadyBeforeCancellation, true);
    });
  });
}
