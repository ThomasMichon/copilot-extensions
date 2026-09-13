import { writeJsonAtomic } from "./handoff-core.mjs";

export class ObservationHandoffError extends Error {}

export const observerSchema = {
  type: "array",
  description: "Only explicitly owned, source-private read-only observers of independently running jobs. Record the original job and durable terminal source before stopping an observer; never stop the job.",
  items: {
    type: "object",
    required: ["shell_id", "job"],
    properties: {
      shell_id: { type: "string", description: "Current source observer's native shell/task ID, not the job's shell ID." },
      job: {
        type: "object",
        required: ["host", "id", "identity", "artifact_path", "terminal_path", "reattach"],
        properties: {
          host: { type: "string" },
          id: { type: "string" },
          identity: { type: "string", description: "Original scheduler ID or PID plus start identity. Preserve authorization and remaining budget in the brief." },
          artifact_path: { type: "string" },
          terminal_path: { type: "string", description: "Durable terminal result/status source, written by the job independently of this observer." },
          reattach: { type: "string", description: "How to observe the same job if nonterminal; data for the successor, never automatically executed." },
        },
      },
    },
  },
};

const active = task => task.type === "shell" && task.attachmentMode === "attached"
  && ["running", "idle"].includes(task.status);

export async function prepareObservationHandoff(session, path, record, observers) {
  const { tasks } = await session.rpc.tasks.list();
  if (observers !== undefined) {
    if (!Array.isArray(observers)) throw new ObservationHandoffError("observers must be an array.");
    if (record.observations) {
      const saved = record.observations.map(item => ({ shell_id: item.observer.id, job: item.job }));
      if (JSON.stringify(saved) !== JSON.stringify(observers)) {
        throw new ObservationHandoffError("Observer handoff already saved; preserve the same job/observer metadata on retry.");
      }
    } else if (observers.length) {
      const seen = new Set();
      const observations = observers.map(({ shell_id, job }) => {
        if (!shell_id || seen.has(shell_id)) throw new ObservationHandoffError("Observer shell IDs must be present and unique.");
        seen.add(shell_id);
        for (const key of observerSchema.items.properties.job.required) {
          if (typeof job?.[key] !== "string" || !job[key].trim()) {
            throw new ObservationHandoffError(`Observer ${shell_id} requires job.${key}. Nothing was parked.`);
          }
        }
        const task = tasks.find(item => item.id === shell_id);
        if (!task || task.type !== "shell" || task.attachmentMode !== "attached"
          || !Number.isInteger(task.pid)) {
          throw new ObservationHandoffError(`Observer ${shell_id} is not an identifiable attached shell in this source session.`);
        }
        return {
          source_session_id: session.sessionId, job,
          observer: { id: task.id, pid: task.pid, command: task.command, started_at: task.startedAt },
        };
      });
      record = { ...record, observations };
      writeJsonAtomic(path, record);
    }
  }
  for (const item of record.observations || []) {
    if (item.source_session_id !== session.sessionId) throw new ObservationHandoffError("Observer belongs to a different source session.");
    const task = tasks.find(candidate => candidate.id === item.observer.id);
    if (task && (task.pid !== item.observer.pid || task.command !== item.observer.command
      || task.startedAt !== item.observer.started_at)) {
      throw new ObservationHandoffError(`Observer ${item.observer.id} identity changed; do not stop the replacement.`);
    }
  }
  const pending = tasks.filter(active);
  if (pending.length) {
    const declared = new Set((record.observations || []).map(item => item.observer.id));
    const owned = pending.filter(task => declared.has(task.id)).map(task => task.id);
    const other = pending.filter(task => !declared.has(task.id)).map(task => task.id);
    throw new ObservationHandoffError([
      "Attached shells prevent native session.idle; cutover was not armed.",
      ...(owned.length ? [
        `Job/observer metadata is durable. Verify these are separate read-only observers, then use stop_bash for ONLY their exact source shell IDs: ${owned.join(", ")}.`,
        "Never stop the original job or its launcher. Retry the SAME saved handoff after parking.",
      ] : []),
      ...(other.length ? [
        `Undeclared attached shells: ${other.join(", ")}. Wait for actual work, or explicitly record an owned observer and its independent job. No process was stopped.`,
      ] : []),
    ].join("\n"));
  }
  if (record.observations?.length) {
    record = {
      ...record,
      observations: record.observations.map(item => ({ ...item, source_observer_parked: true })),
    };
    writeJsonAtomic(path, record);
  }
  return record;
}

export function observationBrief(record) {
  if (!record.observations?.length) return record.promptText;
  return `${record.promptText}\n\n### Existing job observation handoff\n\n` +
    "After native admission, inspect each original job's durable terminal source FIRST. " +
    "A job may finish while no observer is attached. Validate terminal artifacts if present; " +
    "otherwise attach a NEW event-driven observer to the SAME job and record this session's " +
    "new observer identity. Old shell IDs below are historical parking evidence, NOT resumed " +
    "observation. Do not relaunch jobs, execute these metadata strings automatically, change " +
    "authorization/budget, enable autopilot or create a goal. Missing/failed terminal sources " +
    "are explicit errors, not successful completion.\n\n" +
    `\`\`\`json\n${JSON.stringify(record.observations, null, 2)}\n\`\`\`\n`;
}
