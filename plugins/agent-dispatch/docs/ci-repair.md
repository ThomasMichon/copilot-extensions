# Verified-failure CI repair emitter

`producers.ci_repair` is a built-in type of the existing lease-gated emitter.
It creates tasks through the existing coordinator's atomic dedup boundary.
It owns no scheduler, queue, credential relay or failure detector.

An inert registrar spec (use ordinary `supervise register --kind emitter --spec`)
is:

```json
{
  "id": "ci-repair",
  "interval_seconds": 3600,
  "ci_repair": {
    "enabled": false,
    "repository": "example/project",
    "source_command": ["python", "tools/ci_failure_dispatch_source.py", "--repo", "example/project", "--run-id", "123", "--sha", "0123456789012345678901234567890123456789", "--gh-command", "[\"reviewed-repository-gh-wrapper\"]"],
    "admission_command": ["python", "tools/reviewed-ci-admission.py"],
    "execution": {
      "venue": "container:ci-repair-1",
      "charter": "ci-repair-worker",
      "workspace": "/workspace/project"
    }
  }
}
```

The admission adapter is an **adopter-owned activation prerequisite**, not an
allow-all script supplied by this plugin. Enabling without it fails closed.
`enabled` defaults to false, must be a real boolean, and OFF invokes neither
source nor admission commands. Registering this inert spec does not authorize
enabling it or launching a container.

Commands are trusted, operator-reviewed argv bindings, not paths from issue
text. They receive JSON on stdin, have bounded execution, and return JSON;
nonzero/malformed output is an explicit refusal, not a successful empty tick.
The source receives `repository` and returns a list of candidate records with
exact fields `repository`, positive integer `run_id`, `run_attempt`, `job_id`,
gate-verified full lowercase `sha`, and watchdog's 12-hex `signature`.
`tools/ci_failure_dispatch_source.py` reuses watchdog parsing over current,
completed failing jobs, rejects stale run attempts, and never files issues.
Its supplied SHA must come from the independent commit gate: the outer
workflow's `head_sha` is not that gate.
The source adapter requires `--gh-command` with the existing attributable,
repository-scoped command prefix, refuses a bare ambient gh executable and
uses that same prefix for run, job and log reads. It acquires no credentials
and never switches the global gh account.

The **independent admission command** receives `failure` and `execution`.
It must re-read authoritative CI run/jobs/logs and independently verify the
commit gate and exact signature, repository/source authorization, freshness,
the separately reviewed recurrence/attempt/rearm/cooldown decision, and current
independent inference and repository authority **in the selected venue**.
Failure signatures and issue labels are correlation, never authorization.
Issue text/logs are untrusted data. Return `allowed: false` plus `reason`
on refusal. On admission return `allowed: true`, exactly matching `failure`
and `execution`, and nonempty `source_ref`, `policy_ref`,
`inference_auth_ref`, `repository_auth_ref`. These are audit references,
never credentials; the two auth references must be different. The plugin
checks the contract, not the external truths itself. A caller-controlled
JSON file that merely says `allowed: true` is not a valid admission adapter.

All candidates are admitted before task creation. Active tasks dedup by
repository/signature (independent of run ID and attempt). Coordinator dedup
releases terminal tasks; **only the reviewed admission policy** decides whether
a later episode may be emitted. This module does not choose that policy or
claim per-task spawn limits are a failure-episode attempt budget.

The task's JSON `prompt` contains `failure`, `execution`, audit references and
intent-preserving triage constraints; `venue` and `charter` are distinct.
`lane_repository` can name the adopting coordinator lane while `target_repo`
retains the failing code repository.

For execution, an existing headless supervised-lane spec can carry
`ci_repair_bindings: [{"venue":"container:ci-repair-1",
"charter":"ci-repair-worker","workspace":"/workspace/project"}]`.
It must also carry `ci_repair_admission_command: ["python",
"tools/reviewed-ci-admission.py"]`. The launch gate revalidates the same
failure/execution and current source/policy/auth proof with `phase: "spawn"`;
the admission references stored in a task are an audit trail, not launch
authorization. This prevents forged source labels or stale admission from
launching a worker.
The registrar serializes this to `--ci-repair-bindings`; it selects only
admitted `ci-repair` tasks and exact allowlisted overlays and calls
agent-bridge's real `create --target-dir ... --charter ...` transport.
No host worktree is allocated. Other ordinary lanes/emitter types are unchanged.
Bind a dedicated label-gated, headless, non-fleet lane for this purpose.

The [portable container template](../../agent-containers/templates/ci-repair/README.md)
is network-disconnected and carries no identity. No auth adapter, live policy,
workspace materialization, worker activation or deployment is implied by
offline transport tests. Repository operations must never use inference
credentials or change global gh identity.
