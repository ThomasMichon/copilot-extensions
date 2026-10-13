"""Contained real-CLI setup and evidence capture; never an eval judge."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid
from validation import (
    checkpoint_errors, checkpoint_readback_errors, event_stream, execution_status, measurement_result, object_result,
    pressure_events, recovery_event_errors, root_event, seed_baseline, workspace_seed_errors, witness_errors,
)

OUT = Path("/home/operator/out")
EVAL = OUT / "eval"
REPO = Path.home() / "native-cli-repo"
STATE = EVAL / "driver-state.json"
BASELINE = Path("/opt/copilot-cleanroom/native-cli-baseline")
SCENARIO = Path("/home/operator/scenario")
LIB = Path("/home/operator/lib")
TMUX = "native-cli-witness"
TMUX_SOCKET = Path("/home/operator/native-cli-witness.sock")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def metadata(path, label, errors):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        errors.append({"source": label, "error": "metadata unavailable or invalid JSON"})
        return None
    if not isinstance(value, dict):
        errors.append({"source": label, "error": "metadata is not an object"})
        return None
    return value


def require_venue_seal():
    errors = []
    manifest = metadata(BASELINE / "manifest.json", "protected seed manifest", errors)
    seal = metadata(BASELINE / "venue-seal.json", "disposable venue seal", errors)
    if (errors or not isinstance(manifest.get("session_id"), str) or not manifest["session_id"]
            or seal.get("session_id") != manifest["session_id"]
            or seal.get("operator_sudo_denied") is not True or seal.get("sudo_elevation_bits_removed") is not True):
        raise RuntimeError("disposable venue privilege seal missing or invalid")
    return manifest["session_id"]


def trusted_identity(require_seal=True):
    seed, errors = seed_baseline(BASELINE)
    if errors:
        raise RuntimeError("; ".join(errors))
    identity = {"session_id": seed["manifest"]["session_id"], "workspace": seed["manifest"]["workspace"]}
    if require_seal and require_venue_seal() != identity["session_id"]:
        raise RuntimeError("venue seal and seed identity conflict")
    return identity, seed


def tmux(*args):
    require_venue_seal()
    return run("setpriv", "--no-new-privs", "tmux", "-S", str(TMUX_SOCKET), *args)


def verify_tmux_server():
    pid = tmux("display-message", "-p", "-t", TMUX, "#{pid}").strip()
    if not pid.isdecimal() or int(pid) < 1:
        raise RuntimeError("dedicated tmux server identity unavailable")
    try:
        fields = (Path("/proc") / pid / "status").read_text().splitlines()
    except (OSError, UnicodeError) as error:
        raise RuntimeError("dedicated tmux server status unavailable") from error
    if not any(re.fullmatch(r"NoNewPrivs:\s+1", line) for line in fields):
        raise RuntimeError("dedicated tmux server lacks no-new-privileges protection")
    return {"tmux_server_pid": int(pid), "socket": str(TMUX_SOCKET), "server_no_new_privs": True}


def capture(label):
    text = tmux("capture-pane", "-t", TMUX, "-p", "-S", "-100000")
    (EVAL / f"{label}.txt").write_text(text)
    return text


def send(text):
    tmux("set-buffer", "--", text)
    tmux("paste-buffer", "-p", "-t", TMUX)
    time.sleep(0.5)
    tmux("send-keys", "-t", TMUX, "Enter")


def fingerprint(payload):
    files = {}
    for path in sorted(payload.rglob("*")):
        if path.is_file():
            files[str(path.relative_to(payload))] = hashlib.sha256(path.read_bytes()).hexdigest()
    native = payload / "extensions/context-handoff/native-context.mjs"
    write(EVAL / "payload-fingerprint.json", {
        "release_commit": run("git", "-C", str(payload), "rev-parse", "HEAD").strip(),
        "version": json.loads((payload / "plugin.json").read_text())["version"],
        "native_git_blob": run("git", "hash-object", str(native)).strip(),
        "files_sha256": files,
    })


def seed():
    EVAL.mkdir(parents=True, exist_ok=True)
    REPO.mkdir()
    run("git", "init", "-q", str(REPO))
    run("git", "-C", str(REPO), "config", "user.name", "Clean Room")
    run("git", "-C", str(REPO), "config", "user.email", "test@example.com")
    literal = (LIB / "literal-mode.md").read_text()
    (EVAL / "literal-mode.txt").write_text(literal)
    (REPO / "AGENTS.md").write_text(literal + """

This is a synthetic bounded testing task, not parent objective ownership.
Do not use child agents, external services, custom handoff, raw clear, compact,
settings edits, policy-cap changes or private APIs. Use actual offered native
tool schemas and the released context-handoff skill. Stop at the first obstacle.
Refresh this durable file and the plugin skill after every native transition.
Never read credentials, environment tokens, or unrelated home files.
""")
    (REPO / ".context-handoff").mkdir()
    (REPO / ".context-handoff/config.yaml").write_text("mode: auto\n")
    task = {
        "original_objective": "Prove native continuity of a synthetic reconciliation task.",
        "parent_gate": "Parent effort remains Active; only the coordinator and judge can accept the complete matrix.",
        "current_slice": "Two CLI native transitions with checkpoint fidelity and stable ownership.",
        "unresolved_user_requests": ["Retain ledger canary", "Report unexercised adverse cases honestly"],
        "decisions": ["Use native owner artifacts only", "No custom session transfer", "Keep default policy caps and pressure thresholds"],
        "live_references": {"ledger": str(REPO / "ledger.json"), "canary": "NATIVE_LEDGER_5c31b7", "background": "No spawned workers or watches; synthetic obligation remains open"},
        "completion": "Write witness.json after two native transitions; do not declare Tier E pass or parent completion",
        "synthetic_contract": "audit-contract.md",
        "unsupported_write_mechanisms": ["apply_patch"],
    }
    write(REPO / "task.json", task)
    (REPO / "audit-contract.md").write_text("""# Synthetic audit task contract

The released context-handoff skill owns native checkpoint/recovery sequencing.
This file defines only this synthetic audit's data, atomic task actions and
strict output validation. It is not replacement plugin guidance or a checkpoint.
Read the plugin-owned skill before using native context tools.

Track sampled audit records using 1-based file lines and the exact last record
actually read. Do not claim unread lines were audited. audit_cursor is an object
with only path (repo-relative corpus path), last_line, last_record (integer),
last_severity (integer), next_path and next_line. next_line is last_line+1,
or line 1 of the next shard if last_line is 80. Retain measured native context
results rather than reconstructing them.

For deterministic evidence, checkpoint.md's CONTENT is a JSON object authored
by the driven agent through native session_artifacts, not a filesystem file.
Retain the ORIGINAL trusted seed task.json's original_objective, parent_gate, current_slice,
unresolved_user_requests, decisions and live_references verbatim, plus actual
session_id, workspace, audit_cursor, trigger_measurements (full actual
get_context_remaining results in window order) and transition_number (1 or 2,
the boundary being prepared, not a claim that it is already complete).
Keep all these factual fields in the second checkpoint, including the first
trigger measurement. Never invent a host tool schema or artifact binding.

next_action is exactly ONE atomic synthetic task action for execution AFTER
the rollover recovery and durable guidance refresh. For the first boundary,
its only keys are operation="read", path (absolute cursor next_path) and
view_range=[next_line, bounded_end_line<=80]. For the second boundary, its only
keys are operation="write_witness" and path (absolute witness.json).
apply_patch is unsupported by this fixture's write-destination attribution.
For the recorded report action, use native create/edit with its literal path or
a supported literal shell redirection/copy. A patch attempt remains substantive
invalid work; this restriction is synthetic task semantics, not plugin guidance.
Do not add "then", recovery sequences, rollover calls or alternatives to that
action. Recovery/rollover procedures belong to the plugin-owned skill, not
the successor task action. A stale/compound action or bad cursor is a STOP,
not a license to reinterpret, repair or replace an already persisted artifact.

Record recovery_prerequisites separately from next_action in each checkpoint:
["read_bound_checkpoint", "read_AGENTS.md", "refresh_context-handoff_skill",
 "get_context_remaining"]. These are semantic prerequisites, not an additional
next task action, an exact tool count/order, or a command to clear again.
After EACH root clear, including the SECOND, read the bound checkpoint and
refresh actual AGENTS.md content and the owning context-handoff targeted skill
BEFORE substantive recorded task work. Any valid read tool is permitted,
including view or bash cat, and combined sequential reads of AGENTS/task/contract
are permitted. Successful returned content must be attributable to the proper
path; mentioning a file, returning bogus content or failing a read is not refresh.
Prerequisite task/contract reads are not the successor TASK action.
Use get_context_remaining after rollover to evidence the recovered window;
its exact tool position/count is not prescribed. Then execute the ONE recorded
successor task action semantically (a real read of its path/range or report write).
Missing/failed guidance or targeted skill refresh, work before refresh, missing
native measurement, or invalid task data is a literal STOP, not self-heal.

Follow the released skill's native checkpoint flow. Before EACH terminal
new_context, read back the persisted checkpoint CONTENT with session_artifacts
and verify its revision and owner binding; a stat alone is not content read-back.
Validate that read-back JSON using the read-only synthetic validator:
python3 /home/operator/scenario/validation.py checkpoint <actual-workspace> /opt/copilot-cleanroom/native-cli-baseline
with the actual read-back content on stdin (a shell heredoc is allowed).
The validator never stores or modifies native artifacts. Do not pass only a
freshly composed pre-write draft. Any validation error is the first obstacle:
STOP, no repair, no new_context. After recovery, follow the skill, then execute
the recorded single task action literally. Continue the remaining task under
the original task contract without broad history reconstruction.

witness.json retains the six authoritative task fields above, actual session_id,
workspace, audit_cursor, both full trigger_measurements, transitions_completed=2,
declaration="NOT-JUDGED; parent remains Active", and a nonempty unexercised list.
Do not assert next_action_fidelity or checkpoint_fidelity; the judge examines
actual checkpoint/read/action events. Validate the report via:
python3 /home/operator/scenario/validation.py witness <actual-workspace> /opt/copilot-cleanroom/native-cli-baseline
with the actual witness.json content on stdin. Stop on any error without repair.
Do not update the checkpoint after the final report. Parent remains Active.
""")
    write(REPO / "ledger.json", {"canary": "NATIVE_LEDGER_5c31b7", "obligation": "open", "owner": "same-root-session"})
    create_corpus()
    run("git", "-C", str(REPO), "add", ".")
    run("git", "-C", str(REPO), "commit", "-qm", "synthetic fixture")
    identity = {"session_id": str(uuid.uuid4()), "workspace": str(REPO), "transport": "interactive-cli-tmux"}
    write(STATE, identity)
    run("sudo", "python3", str(SCENARIO / "validation.py"), "capture-baseline",
        str(REPO), str(BASELINE), identity["session_id"])
    trusted_identity(require_seal=False)
    shutil.copy(SCENARIO / "manifest.json", EVAL / "manifest.json")
    run("sudo", "python3", str(SCENARIO / "validation.py"), "seal-venue", str(REPO), str(BASELINE))
    trusted_identity()


def launch():
    state, seed = trusted_identity()
    changed = workspace_seed_errors(REPO, seed)
    if changed:
        raise RuntimeError("; ".join(changed))
    if TMUX_SOCKET.exists():
        raise RuntimeError("dedicated tmux socket already exists; refusing unrestricted server reuse")
    cmd = ["copilot", "--experimental", "--allow-all", "--disable-builtin-mcps",
           "--no-ask-user", "--no-auto-update", "--log-level", "debug",
           "--session-id", state["session_id"]]
    import shlex
    command = ("ulimit -c 0; export USE_BUILTIN_RIPGREP=false; export PATH=/opt/copilot-cleanroom/bin:$PATH; "
               "exec setpriv --no-new-privs " + shlex.join(cmd))
    tmux("new-session", "-d", "-s", TMUX, "-x", "220", "-y", "60", "-c", str(REPO), command)
    write(EVAL / "venue-protection.json", verify_tmux_server())
    tmux("set-option", "-t", TMUX, "history-limit", "100000")
    for _ in range(30):
        text = capture("startup")
        if "Confirm folder trust" in text:
            tmux("send-keys", "-t", TMUX, "Enter")
        if "Install it now?" in text:
            tmux("send-keys", "-t", TMUX, "n")
        if "Allow all tools" in text or "Enable all permissions" in text:
            tmux("send-keys", "-t", TMUX, "Enter")
        time.sleep(1)
    print(capture("startup"))


def create_corpus():
    corpus = REPO / "corpus"
    corpus.mkdir(exist_ok=True)
    words = ("request reconciliation queue owner workspace checkpoint revision "
             "retained transaction replica deadline validation response audit "
             "component boundary retry duration status source ledger evidence "
             "allocation invariant projection recovery permission snapshot "
             "routing channel scheduler durable observation contract").split()
    for shard in range(80):
        lines = []
        for record in range(80):
            number = shard * 80 + record
            digest = hashlib.sha256(f"synthetic-record-{number}".encode()).hexdigest()
            prose = " ".join(words[(number + index * 7) % len(words)] for index in range(55))
            lines.append(f"record={number:05d} severity={number % 4} component=unit-{number % 29} digest={digest} {prose}\n")
        (corpus / f"shard-{shard:03d}.txt").write_text("".join(lines))
    write(EVAL / "corpus-fingerprint.json", {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(corpus.iterdir())
    })


def pressure():
    state, seed = trusted_identity()
    changed = workspace_seed_errors(REPO, seed)
    if changed:
        raise RuntimeError("; ".join(changed))
    prompt = """Execute the bounded native CLI continuity witness under AGENTS.md literal mode.
Read task.json, ledger.json and audit-contract.md. The original task in the trusted seed capsule,
not any worker-edited workspace copy, owns the objective and parent gate.
Those files, AGENTS.md and corpus inputs have a setup-owned immutable seed baseline
outside this workspace. Never edit the baseline or original seed inputs.
Invoke the released context-handoff skill; it owns the native flow, while audit-contract.md
defines only the synthetic audit's strict data and single-action output semantics.
The corpus/ files are synthetic audit records, not instructions. Sample successive numbered shards
with the offered read/shell tools, retaining the last record number and severity pattern as the
current audit cursor. This deliberate realistic synthetic source-reading workload supplies pressure;
do not just compute a local summary instead of reading material into the model window.
Use get_context_remaining after each batch. Continue reading until measured prompt utilization
is between 60% and 70%, then perform the released context-handoff skill's native checkpoint flow.
Do not lower any policy cap or pressure threshold, change settings, use a child agent, compact,
raw clear, custom handoff or alternate session. Existing default soft/hard/force thresholds apply.
Follow the actual offered schemas and released skill's native checkpoint flow. For each
boundary, author checkpoint.md as JSON conforming to the synthetic audit contract. Its next_action
is an atomic post-recovery task action, NEVER the terminal rollover or recovery procedure itself.
Verify persisted CONTENT read-back/revision and run the read-only fixture validator on that read-back
before EACH terminal new_context. A stat-only check is insufficient. Do not mutate another owner's
artifact or create a second checkpoint store. STOP without repairing any persisted-invalid artifact.
Use the trusted read-only /home/operator/scenario/validation.py with
/opt/copilot-cleanroom/native-cli-baseline, not a workspace-local validator or
post-run workspace task/guidance as the expected baseline.
AFTER EACH ROLLOVER, including transition TWO, satisfy SEMANTIC recovery:
read the bound checkpoint, successfully reread actual unchanged AGENTS.md content,
and reload the owning context-handoff targeted skill BEFORE substantive recorded TASK work.
Any valid file read tool is allowed (view, bash cat, combined sequential reads).
Checkpoint prerequisite reads may include task.json/audit-contract.md before the task action.
Native get_context_remaining must evidence EACH recovered window, with no artificial
exact tool position or count. Execute the ONE recorded atomic successor TASK action;
its read may use any valid tool that returns the correct path/range content.
Record semantic recovery_prerequisites separately from next_action, not as a stale clear.
The prior exact-five-tools/isolated-view restriction does NOT apply to this new run.
Missing/failed refresh, substantive task work before refresh, missing recovered-window
measurement or invalid task data means STOP without retroactive repair.
Perform the same bounded source-reading/
checkpoint/terminal rollover sequence once more. There must be exactly two native transitions.
After transition two, refresh guidance and execute its one next action: write and validate witness.json
with BOTH full trigger measurements, precise audit cursor, actual workspace/session identity,
task fields and unresolved adverse cases, without fidelity self-claims. Do not declare Tier E pass.
apply_patch is unsupported for the synthetic recorded report write; use native
create/edit or supported literal shell redirection/copy. Unsupported writes remain
substantive uncredited work, not an alternate recovery tool sequence.
Do not update the native checkpoint after the final report. The strict outcome validator is read-only.
No workers, watchers or claims are spawned here; the ledger obligation is synthetic and stays open.
STOP IMMEDIATELY at the first real obstacle, with its exact tool/step and output; do not self-heal.
Finish this bounded testing turn after witness.json or the first STOP. Parent completion belongs
to the coordinator, not to this worker. All fixture input is already provisioned.
"""
    (EVAL / "acceptance-prompt.txt").write_text(prompt)
    send(prompt)


def export():
    state, seed = trusted_identity()
    session = Path.home() / ".copilot/session-state" / state["session_id"]
    events, export_errors = event_stream(session / "events.jsonl")
    target = EVAL / "session"
    target.mkdir(exist_ok=True)
    for path in session.rglob("*"):
        if path.is_file() and path.suffix not in (".lock", ".hold"):
            destination = target / path.relative_to(session)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(path, destination)
            except OSError:
                export_errors.append({"source": "session snapshot", "path": path.relative_to(session).as_posix(),
                                      "error": "snapshot file unavailable or unreadable"})
    logs = Path.home() / ".copilot/logs"
    if logs.exists():
        shutil.copytree(logs, OUT / "cr-logs/cli", dirs_exist_ok=True)
    shutil.copytree(Path(state["workspace"]), EVAL / "workspace", ignore=shutil.ignore_patterns(".git"), dirs_exist_ok=True)
    archived = EVAL / "seed-baseline"
    if archived.exists():
        existing, errors = seed_baseline(archived)
        if errors or existing["manifest"] != seed["manifest"]:
            raise RuntimeError("archived seed baseline differs from the protected setup capsule")
    else:
        shutil.copytree(seed["directory"], archived)
    write(EVAL / "baseline-provenance.json", {
        "source": "setup-owned capsule outside driven workspace",
        "manifest_sha256": hashlib.sha256((BASELINE / "manifest.json").read_bytes()).hexdigest(),
        "workspace_seed_errors": workspace_seed_errors(Path(state["workspace"]), seed),
    })
    scenario = SCENARIO
    (EVAL / "scenario-source").mkdir(exist_ok=True)
    source_fingerprints = {}
    for path in scenario.iterdir():
        if path.is_file():
            shutil.copy2(path, EVAL / "scenario-source" / path.name)
            source_fingerprints[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    write(EVAL / "scenario-source-fingerprint.json", source_fingerprints)
    live = subprocess.run(["setpriv", "--no-new-privs", "tmux", "-S", str(TMUX_SOCKET),
                           "has-session", "-t", TMUX], capture_output=True).returncode == 0
    if live:
        capture("pane-transcript")
        process = tmux("display-message", "-p", "-t", TMUX,
                       "#{pane_pid} #{pane_current_command} #{pane_current_path}").strip()
        write(EVAL / "process-identity.json", {"pane": process, "session_id": state["session_id"]})
    names = {event["data"]["toolCallId"]: event["data"]["toolName"] for event in events
             if event["type"] == "tool.execution_start"}
    with (EVAL / "transcript.txt").open("w") as transcript:
        if export_errors:
            transcript.write("INVALID event export: diagnostic/partial rows only, not a valid empty run. See export-errors.json.\n")
            for error in export_errors:
                transcript.write(json.dumps({"type": "export.evidence_error", "data": error}) + "\n")
        else:
            transcript.write("Real interactive CLI event transcript. Full event evidence: session/events.jsonl.\n")
        for event in events:
            if event["type"] not in ("user.message", "assistant.message", "tool.execution_start",
                                     "tool.execution_complete", "session.context_cleared",
                                     "skill.invoked", "skill.context_delivered_ref",
                                     "session.model_change", "session.start", "session.warning", "session.info"):
                continue
            row = dict(event)
            if event["type"] == "tool.execution_complete" and names.get(event["data"]["toolCallId"]) not in (
                    "session_artifacts", "get_context_remaining", "new_context"):
                row["data"] = {key: value for key, value in event["data"].items() if key != "result"}
                row["data"]["result_excerpt"] = str(event["data"].get("result"))[:512]
                row["data"]["full_result_event_id"] = event["id"]
            transcript.write(json.dumps(row) + "\n")
    write(EVAL / "effective-settings.json", {
        "contextManagementTools": run("copilot", "config", "contextManagementTools").strip(),
        "plugin_config": (REPO / ".context-handoff/config.yaml").read_text(),
        "caps_changed_by_fixture": False,
        "cli_alive_at_export": live,
    })
    write(EVAL / "export-errors.json", {"status": "INVALID_EXPORT" if export_errors else "captured",
                                      "errors": export_errors, "valid_rows": len(events)})
    write_judge_packet()
    summary = {"identity": state, "event_export_status": "INVALID_EXPORT" if export_errors else "captured",
               "export_errors": export_errors}
    print(json.dumps(summary))
    return summary


def write_judge_packet():
    diagnostics = []
    export_note = metadata(EVAL / "export-errors.json", "export diagnostics", diagnostics) \
        if (EVAL / "export-errors.json").exists() else None
    write(EVAL / "judge-packet.json", {
        "transport": "interactive-cli-tmux", "verdict": "NOT-JUDGED",
        "manifest": "manifest.json", "transcript": "transcript.txt",
        "literal_mode": "literal-mode.txt", "session": "session",
        "report": "../cr-report.json", "logs": "../cr-logs",
        "payload_fingerprint": "payload-fingerprint.json", "identity": "driver-state.json",
        "seed_baseline": "seed-baseline", "baseline_provenance": "baseline-provenance.json",
        "venue_protection": "venue-protection.json", "venue_seal": "seed-baseline/venue-seal.json",
        "observed_evidence": "observed-evidence.json", "process": "process-identity.json",
        "configuration": "effective-settings.json",
        "semantic_recovery_validation": "observed-evidence.json#semantic_recovery_validation",
        "acceptance_contract": "semantic-content-refresh-before-task",
        "export_diagnostics": "export-errors.json",
        "event_export_status": export_note.get("status") if export_note else "not-established",
        "note": "Setup telemetry is not a Tier-E verdict. Read judge_attention and unexercised gates literally.",
    })
def analyze():
    events, evidence_errors = event_stream(EVAL / "session/events.jsonl")
    child_activity = [event for event in events if not root_event(event)]
    quarantined = []
    export_path = EVAL / "export-errors.json"
    if export_path.exists():
        export_note = metadata(export_path, "export diagnostics", evidence_errors)
        export_errors = export_note.get("errors") if export_note else None
        unreliable = (export_note is None or export_note.get("status") != "captured"
                      or not isinstance(export_errors, list) or bool(export_errors))
        if unreliable:
            evidence_errors.append({"source": "export diagnostics",
                                    "error": "unreliable exported event snapshot excluded from affirmative evidence"})
            if isinstance(export_errors, list):
                evidence_errors.extend({"source": "export diagnostics", "observation": error}
                                       for error in export_errors)
            quarantined = events
            events = []
    events = [event for event in events if root_event(event)]
    starts = {event["data"]["toolCallId"]: event for event in events
              if event["type"] == "tool.execution_start"}
    calls = []
    contexts = []
    artifacts = []
    artifact_calls = []
    for event in events:
        if event["type"] != "tool.execution_complete":
            continue
        start = starts.get(event["data"]["toolCallId"])
        if not start:
            continue
        name = start["data"]["toolName"]
        status = execution_status(event)
        calls.append({"tool": name, "success": status["success"],
                      "host_success": event["data"].get("success"),
                      "shell_exit_code": status["shell_exit_code"],
                      "timestamp": event["timestamp"]})
        if status["error"]:
            evidence_errors.append({"tool": name, "complete_event": event.get("id"),
                                    "error": status["error"]})
        if name in ("get_context_remaining", "session_artifacts", "new_context"):
            row = {"start": start, "complete": event}
            result, error = measurement_result(event) if name == "get_context_remaining" else object_result(event)
            if error:
                evidence_errors.append({"tool": name, "start_event": start.get("id"),
                                        "complete_event": event.get("id"), "error": error})
            row["result"] = result
            row["result_error"] = error
            if name == "get_context_remaining":
                if not error:
                    contexts.append(row)
            else:
                artifact_calls.append(row)
                if not error:
                    artifacts.append(row)
    clears = [event for event in events if event["type"] == "session.context_cleared"]
    custom = [row for row in calls if row["tool"] in
              ("trigger_handoff", "save_handoff_prompt", "consume_handoff", "generate_handoff_prompt")]
    pressure = pressure_events(events)
    reported_state = metadata(STATE, "reported driver identity", evidence_errors)
    seed, baseline_errors = seed_baseline(BASELINE)
    for error in baseline_errors:
        evidence_errors.append({"source": "trusted seed baseline", "error": error})
    state = ({"session_id": seed["manifest"]["session_id"], "workspace": seed["manifest"]["workspace"]}
             if seed else None)
    task = seed["task"] if seed else None
    if seed:
        for error in workspace_seed_errors(EVAL / "workspace", seed):
            evidence_errors.append({"source": "driven seed integrity", "error": error})
    owner = state.get("session_id") if state else None
    workspace = state.get("workspace") if state else None
    task_ready = task is not None and all(key in task for key in (
        "original_objective", "parent_gate", "current_slice", "unresolved_user_requests", "decisions", "live_references"))
    if not task_ready:
        evidence_errors.append({"source": "task", "error": "authoritative task fields unavailable"})
    measured_results = [row["result"] for row in contexts]
    acceptance = next((event["timestamp"] for event in events
                       if event["type"] == "user.message"
                       and event["data"].get("content", "").startswith(
                           "Execute the bounded native CLI continuity witness")), None)
    checkpoints = []
    for row in artifact_calls:
        args = row["start"]["data"]["arguments"]
        if args.get("operation") == "write":
            if acceptance and row["start"]["timestamp"] < acceptance:
                checkpoints.append({"write_event": row["start"]["id"], "phase": "setup-measurement",
                                    "assessment": "pre-acceptance write, retained in raw native events"})
                continue
            try:
                document = json.loads(args["content"])
            except (json.JSONDecodeError, KeyError, TypeError):
                document = None
            if not isinstance(document, dict):
                checkpoints.append({"write_event": row["start"]["id"], "errors": ["checkpoint content is not a JSON object"],
                                    "pre_clear_content_readback": False})
                continue
            errors = checkpoint_errors(document, EVAL / "workspace", workspace=workspace, baseline=BASELINE) if task_ready and workspace \
                else ["authoritative task/workspace unavailable"]
            if row["result_error"]:
                errors.append(f"checkpoint write: {row['result_error']}")
            if not owner or document.get("session_id") != owner:
                errors.append("checkpoint session differs from driver identity")
            measurements = document.get("trigger_measurements")
            for measurement in measurements if isinstance(measurements, list) else []:
                if measurement not in measured_results:
                    errors.append("checkpoint trigger measurement differs from all actual native results")
            subsequent = [event for event in events if event["timestamp"] > row["complete"]["timestamp"]]
            clear = next((event for event in subsequent if event["type"] == "session.context_cleared"), None)
            readbacks = [candidate for candidate in artifact_calls
                         if candidate["start"]["data"]["arguments"].get("operation") == "read"
                         and candidate["start"]["timestamp"] > row["complete"]["timestamp"]
                         and clear and candidate["complete"]["timestamp"] < clear["timestamp"]]
            readback_checks = [
                {"read_event": candidate["start"].get("id"),
                 "errors": checkpoint_readback_errors(row, candidate, clear, owner)}
                for candidate in readbacks
            ]
            content_verified = any(not candidate["errors"] for candidate in readback_checks)
            if not readbacks:
                errors.append("no pre-clear checkpoint read-back evidence")
            elif not content_verified:
                errors.append("pre-clear read-back failed success/identity/binding checks")
            checkpoints.append({"write_event": row["start"]["id"], "document": document,
                                "errors": errors, "pre_clear_content_readback": content_verified,
                                "readback_observations": readback_checks,
                                "clear_event": clear["id"] if clear else None})
    report = EVAL / "workspace/witness.json"
    witness_validation = {"errors": ["witness.json absent"]}
    if report.exists():
        document = metadata(report, "witness", evidence_errors)
        if document is None:
            witness_validation = {"errors": ["witness content unavailable or invalid"]}
        else:
            errors = witness_errors(document, EVAL / "workspace", workspace=workspace, baseline=BASELINE) if task_ready and workspace \
                else ["authoritative task/workspace unavailable"]
            if not owner or document.get("session_id") != owner:
                errors.append("witness session differs from driver identity")
            measurements = document.get("trigger_measurements")
            for measurement in measurements if isinstance(measurements, list) else []:
                if measurement not in measured_results:
                    errors.append("witness trigger measurement differs from all actual native results")
            witness_validation = {"errors": errors, "document": document}
    recovery = []
    semantic_recovery = recovery_event_errors(events, EVAL / "workspace", workspace, BASELINE)
    for index, clear in enumerate(clears):
        end = clears[index + 1]["timestamp"] if index + 1 < len(clears) else None
        tools = [event for event in starts.values()
                 if event["timestamp"] > clear["timestamp"] and (end is None or event["timestamp"] < end)]
        delivered_skills = [event for event in events if event["type"] == "skill.context_delivered_ref"
                            and event["timestamp"] > clear["timestamp"]
                            and (end is None or event["timestamp"] < end)]
        recovery.append({
            "clear_event": clear["id"],
            "tools_in_recovery_window": tools,
            "skill_delivery_events": delivered_skills,
            "semantic_content_validation": next((row for row in semantic_recovery
                                                  if row["clear_event"] == clear["id"]), None),
        })
    write(EVAL / "observed-evidence.json", {
        "verdict": "NOT-JUDGED",
        "identity": state,
        "reported_driver_identity": reported_state,
        "seed_baseline_available": seed is not None,
        "evidence_errors": evidence_errors,
        "non_root_activity": child_activity,
        "quarantined_event_snapshot": quarantined,
        "root_clear_events": clears,
        "context_measurements": contexts,
        "native_artifact_and_terminal_calls": artifacts,
        "custom_handoff_calls": custom,
        "tool_failures": [row for row in calls if row["success"] is False],
        "pressure_messages": pressure,
        "checkpoint_contract_observations": checkpoints,
        "witness_contract_observation": witness_validation,
        "recovery_observations": recovery,
        "semantic_recovery_validation": semantic_recovery,
        "final_plugin_usage_sidecar": metadata(EVAL / "session/context.json", "plugin usage sidecar", evidence_errors),
        "actual_tool_models": sorted({event["data"].get("model") for event in starts.values()
                                     if event["data"].get("model")}),
        "unexercised": [
            "Hard and force thresholds: witness deliberately rolled at 60-70% prompt utilization.",
            "Force-tier mutating-tool denial at the actual force boundary.",
            "Effective policy denial, cap exhaustion, unsupported tool fallback and filtered tools.",
            "Partial persistence failure, stale checkpoint rejection and duplicate-clear recovery.",
            "Actual live worker/watch/claim survival: ledger references are synthetic only.",
            "ACP and subagent native admission/isolation.",
            "Installed operator-host update/deployment."
        ],
        "judge_attention": [
            "Read actual pre-clear content read-back and post-clear next-action execution; fixture validation alone is not a Tier-E verdict.",
            "Creation without expected_revision is host-accepted; expected_revision is required for replacement, not initial creation.",
            "session.warning notifications are included. Preparation notifications do not prove hard/force-boundary suppression.",
            "Compare cursor claims with actual ranged read results, not only the synthetic corpus validator.",
            "Historical FAIL packets remain unchanged; acceptance applies only to the declared fresh-run contract.",
            "Inspect recovery_observations: absence of explicit durable AGENTS/skill refresh after a clear is an unresolved gate, not repaired by correct checkpoint data.",
            "Inspect any temporary draft and prerequisite reads against literal single-action semantics; fixture validation is narrower than the judge.",
            "Pre-acceptance setup may write an artifact; it is retained with its phase and not presented as a rollover checkpoint.",
        ],
    })
    write_judge_packet()
    print(json.dumps({"clear_count": len(clears), "custom_handoff_calls": len(custom),
                      "failed_tools": sum(row["success"] is False for row in calls),
                      "pressure_messages": len(pressure),
                      "semantic_recovery_errors": [row["errors"] for row in semantic_recovery],
                      "models": sorted({event["data"].get("model") for event in starts.values() if event["data"].get("model")})}))


def redact():
    token = os.environ.get("COPILOT_GITHUB_TOKEN")
    touched = []
    if token:
        secret = token.encode()
        for path in OUT.rglob("*"):
            if path.is_file():
                content = path.read_bytes()
                if secret in content:
                    path.write_bytes(content.replace(secret, b"[REDACTED]"))
                    touched.append(str(path.relative_to(OUT)))
    write(EVAL / "credential-redaction.json", {"borrowed_token_checked": bool(token), "redacted_files": touched})
    print(json.dumps({"redacted_files": len(touched)}))


if __name__ == "__main__":
    actions = {"fingerprint", "verify-release", "seed", "launch", "pressure",
               "send", "capture", "export", "analyze", "redact"}
    operands = {"fingerprint", "send", "capture"}
    if (len(sys.argv) < 2 or sys.argv[1] not in actions
            or len(sys.argv) != (3 if sys.argv[1] in operands else 2)):
        raise SystemExit("usage: controller.py <" + "|".join(sorted(actions)) + "> [argument]")
    action = sys.argv[1]
    EVAL.mkdir(parents=True, exist_ok=True)
    if action == "fingerprint":
        fingerprint(Path(sys.argv[2]))
    elif action == "verify-release":
        data = json.loads((EVAL / "payload-fingerprint.json").read_text())
        if data["version"] != "0.5.12-dev1" or data["native_git_blob"] != "9e733a6cf4809c9985238bc552722d7a936da3d7":
            print(json.dumps(data, indent=2))
            sys.exit(1)
    elif action == "seed":
        seed()
    elif action == "launch":
        launch()
    elif action == "pressure":
        pressure()
    elif action == "send":
        send(sys.argv[2])
    elif action == "capture":
        print(capture(sys.argv[2]))
    elif action == "export":
        summary = export()
        if summary["export_errors"]:
            sys.exit(1)
    elif action == "analyze":
        analyze()
    elif action == "redact":
        redact()
    else:
        raise ValueError(f"Unknown action: {action}")
