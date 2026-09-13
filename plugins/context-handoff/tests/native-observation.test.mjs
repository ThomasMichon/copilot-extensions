import assert from "node:assert/strict";
import { test } from "node:test";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import vm from "node:vm";
import { observationBrief, prepareObservationHandoff, ObservationHandoffError } from "../extensions/context-handoff/native-observation.mjs";

const jobCode = `
const fs = require("node:fs");
process.on("message", path => {
  fs.writeFileSync(path, JSON.stringify({ pid: process.pid, launch_count: 1, value: 42 }));
  process.disconnect();
});
process.send("ready");`;
const observerCode = `
const fs = require("node:fs"), path = process.argv[1];
const watcher = fs.watch(require("node:path").dirname(path), check);
function check() {
  if (!fs.existsSync(path)) return;
  const result = JSON.parse(fs.readFileSync(path));
  watcher.close();
  process.send(result);
  process.disconnect();
}
process.send("ready");
check();`;

for (const tool of ["continue_handoff", "retry_handoff_cutover"]) {
  test(`${tool} returns the actual observer rejection through the public tool boundary`, async () => {
    const extension = readFileSync(new URL("../extensions/context-handoff/extension.mjs", import.meta.url), "utf8");
    const start = extension.indexOf("handler: async", extension.indexOf(`name: "${tool}"`));
    const end = extension.indexOf("\n      },\n    },", start);
    const message = "Attached shells prevent native session.idle; exact observer is still attached.";
    let failure = new ObservationHandoffError(message);
    const context = vm.createContext({
      ObservationHandoffError, process: { env: {} }, session: { sessionId: "source" },
      state: { pendingHandoff: { seed: "fixed-seed", nativeGoalCheckpoint: "fixed-checkpoint" } },
      nativeCutoverPath: null,
      requestNativeCutover: async () => { throw failure; },
    });
    vm.runInContext(`globalThis.invoke = (${extension.slice(start + "handler: ".length, end)}\n});`, context);
    const result = await context.invoke({ seed: "fixed-seed" });
    assert.equal(result.resultType, "rejected");
    assert.equal(result.textResultForLlm, message);
    assert.equal(context.nativeCutoverPath, null);
    failure = new Error("unrelated native failure");
    await assert.rejects(context.invoke({ seed: "fixed-seed" }), /unrelated native failure/);
  });
}

async function child(t, code, args = []) {
  const process = spawn(globalThis.process.execPath, ["-e", code, ...args], {
    detached: true, stdio: ["ignore", "ignore", "inherit", "ipc"],
  });
  const exited = once(process, "exit");
  t.after(async () => {
    if (process.exitCode === null && process.signalCode === null) process.kill("SIGTERM");
    await exited;
  });
  assert.deepEqual(await once(process, "message"), ["ready", undefined]);
  return { process, exited };
}

for (const finishesInGap of [true, false]) {
  test(`parked observer preserves one original job; terminal during gap=${finishesInGap}`, { timeout: 10000 }, async t => {
    const dir = mkdtempSync(join(tmpdir(), "handoff-observer-"));
    t.after(() => rmSync(dir, { recursive: true, force: true }));
    const path = join(dir, "checkpoint.json");
    const terminal = join(dir, "result.json");
    const job = await child(t, jobCode);
    const old = await child(t, observerCode, [terminal]);
    const task = {
      id: "source-observer", type: "shell", attachmentMode: "attached",
      status: "running", pid: old.process.pid, command: "read-only terminal observer",
      startedAt: "2026-01-01T00:00:00.000Z",
    };
    const tasks = [task];
    const session = { sessionId: "source", rpc: { tasks: { list: async () => ({ tasks }) } } };
    const original = { promptText: "same objective", nativeGoal: {
      sourceSessionId: "source", successorSessionId: "fixed-receiver",
      intent: "stopped", sourceObjectiveId: null,
    } };
    const observers = [{ shell_id: task.id, job: {
      host: "local", id: "owned-non-gpu-fixture",
      identity: `pid=${job.process.pid};start=${readFileSync(`/proc/${job.process.pid}/stat`, "utf8").split(") ")[1].split(" ")[19]}`,
      artifact_path: dir, terminal_path: terminal, reattach: "watch this existing terminal source, then read it",
    } }];
    await assert.rejects(prepareObservationHandoff(session, path, original, observers), /stop_bash.*source-observer/);
    const saved = JSON.parse(readFileSync(path, "utf8"));
    assert.deepEqual(saved.nativeGoal, original.nativeGoal);
    assert.equal(saved.observations[0].observer.pid, old.process.pid);
    assert.deepEqual(saved.observations[0].job, observers[0].job);
    assert.equal(old.process.exitCode, null, "preparation never kills an observer");
    assert.equal(job.process.exitCode, null, "preparation never stops the job");
    old.process.kill("SIGTERM"); // Equivalent owned-observer stop; never signal the job.
    await old.exited;
    task.status = "cancelled";
    const parked = await prepareObservationHandoff(session, path, saved);
    assert.equal(parked.observations[0].source_observer_parked, true);
    assert.deepEqual(parked.nativeGoal, original.nativeGoal);
    const brief = observationBrief(parked);
    assert.match(brief, /terminal source FIRST/);
    assert.match(brief, /NOT resumed observation/);
    let result;
    if (finishesInGap) {
      job.process.send(terminal);
      await job.exited;
      result = JSON.parse(readFileSync(terminal, "utf8"));
    } else {
      const next = await child(t, observerCode, [terminal]);
      assert.notEqual(next.process.pid, old.process.pid);
      const terminalEvent = once(next.process, "message");
      job.process.send(terminal);
      [result] = await terminalEvent;
      await Promise.all([job.exited, next.exited]);
    }
    assert.deepEqual(result, { pid: job.process.pid, launch_count: 1, value: 42 });
    assert.equal(JSON.parse(readFileSync(path)).observations[0].job.terminal_path, terminal);
  });
}

test("undeclared work, invalid job metadata and replaced observer remain untouched", async t => {
  const dir = mkdtempSync(join(tmpdir(), "handoff-observer-negative-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, "checkpoint.json");
  const task = {
    type: "shell", attachmentMode: "attached", status: "running",
    id: "observer", pid: 123, command: "observe", startedAt: "original",
  };
  const session = { sessionId: "source", rpc: { tasks: { list: async () => ({ tasks: [task] }) } } };
  const record = { promptText: "same work" };
  await assert.rejects(prepareObservationHandoff(session, path, record), /Undeclared attached shells/);
  await assert.rejects(prepareObservationHandoff(session, path, record,
    [{ shell_id: "observer", job: {} }]), /requires job.host/);
  await assert.rejects(prepareObservationHandoff(session, path, {
    ...record, observations: [{ source_session_id: "source", observer: { ...task, pid: 456 } }],
  }), /identity changed/);
});
