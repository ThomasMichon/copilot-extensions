# Process registry - inception guidance

**Parent:** [effort](README.md). This sidecar preserves the operator statements
that govern the architecture. Technical safeguards proposed by the agent live
in [architecture](architecture.md), explicitly labeled as recommendations.
This is the relevant design exchange, not a dump of the surrounding session's
unrelated CI investigations or private process inventory.

## 2026-10-06 - Initial request

**Operator:**

> Determine how many of each agent-* singleton we havew, determine how many
> agent-mcp shims we have, etc. We really need a way to have our processes
> self-report: role, phost plugin, Pid, owner sessionid, cwd, worktree, etc.
> I wonder if there is a way or place do track this reliably and with consistency

**Agent's relevant choice:**

> Want me to (a) investigate/fix the 3 stale mux-daemons first, (b) file both
> findings as tracked issues, or (c) start scoping the self-report-registry idea
> as a real effort?

**Operator:**

> All three.

## 2026-10-07 - Stateful log, not telemetry

**Operator:**

> Ah, let's try to differentiate this from proper "telemetry" and treat this
> more like a "stateful log", more akin to the port-reservation system. We want
> a folder, DB, or little daemon (most likely) where as processes start and
> exit, they announce and remove themselves from registration. This daemon can
> be *asked* at any time for a state snapshot, and it can journal a couple
> snapshots an hour into a folder. But what it *won't* do is hook up to any
> formal telemetry sink. Kind of like how agent-logger accumulates and provides
> on-demand reporting of copilot logs/ and session-state, but absolutely does
> not hook up to a formal telemetry-reporting sink system.

## 2026-10-07 - Optional handler and non-wedging reporting

**Operator:**

> Kind of like ssh-manager or whatever it is, we might need agent-* plugins or
> worktree manager to take ownership over this shared daemon. Seems like
> something worktree-manager could set up; if there isn't a registered "local
> handler", processes can avoid sending the data. It's important that we don't
> wedge processes on account of the system designed to detect wedged processes

## 2026-10-07 - Coverage and performance

**Operator:**

> This sounds good. With a daemon, we can have the daemon "ping back" processes
> by id, plus actually research more about them (though being careful to avoid
> CPU churn doing so). Essentially, we want all of *our* processes to "check
> in" with the system, so we can tell our python.exe, cmd.exe, and conhost.exe
> from others'. An obvious hook for this is our shared procutil and other
> vendored process invokers, but we can also put check-in points in .py, .sh,
> and .ps1 scripts themselves to self-announce. So long as we're just tagging
> PID values and a time, then it should debounce. Assess performanc and
> reliability concerns here.

**Agent assessment (summary, not operator intent):** use PID birth identity,
not report time; distinguish physical wrappers from logical roles; avoid
per-process callback servers/reporting helpers; cache and bound inspection;
expose unknown/partial coverage; measure rather than assume performance.

## 2026-10-07 - One broker, file ingress

**Operator:**

> I would only want *one* global daemon to act as the broker, assuming we go
> with that model. We definitiely don't want to spawn a process just to report
> an a process. So the safest thing is "drop a file with the pid, and then
> later try to clean it up", and then the daemon finds that, checks the caller,
> records information, and then checks later if it's still alive. monitor
> daemon can file-watch a directory for these files or something.
>
> Help build out a robust architecture here, and draft an effort+vision

**Agent clarification:**

> For "one global daemon," I'll make it shared across all plugins,
> marketplaces, and worktrees. One security boundary affects the design:
> should the first version serve only the machine's designated operator
> account, or all OS user accounts through a machine-wide service?
> Neither option allows a reporting subprocess.

**Operator selected:** `operator-account` - one host broker for the designated
operator account; other accounts cannot start separate brokers.

## See Also

- [Effort](README.md), [architecture](architecture.md), and
  [vision](../../../visions/process-registry/README.md).
