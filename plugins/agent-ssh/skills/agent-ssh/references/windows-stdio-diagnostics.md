# Windows SSH stdio diagnostics

Use this procedure from the [agent-ssh skill](../SKILL.md) when a bounded SSH
probe stalls. It changes only owned diagnostic children, never shared services,
SSH profiles, host-key policy, remote sessions or the parent environment.

## Separate process lifetime from stream lifetime

Record the exact executable/version, argument shape, standard-handle setup and
elapsed time without publishing credentials, environment values or key bytes.
Check whether the owned SSH root is still alive before cleanup:

- A live child can be blocked in OpenSSH's logging/write-completion path.
  Concurrently draining pipes does not rule out that class of stall.
- A child that already exited can leave the parent waiting for EOF because a
  descendant still holds an inherited pipe. Waiting for stream EOF is not the
  same observation as waiting for process exit.
- A full output buffer is another possibility, not the default explanation.
  Drain stdout and stderr concurrently whenever both are piped.

Do not normalize a multi-minute local-only probe as network latency. A
configuration-only `ssh -G` probe that makes no connection can isolate local
startup from remote routing. A fresh stderr pipe or even a parent-opened regular
file can change the behavior being investigated.

## Bounded comparisons, not a deployment workaround

1. Preserve the real launch shape and use the OS-correct no-window primitive;
   see `agent-conduct-guidance:spawning-headless-processes`. Never pop up a
   terminal to make a headless failure disappear.
2. Compare only the missing variable. For logging interference, compare the
   same harmless command with stderr discarded and with `ssh -E <private-log>`
   opening its own diagnostic file. `-q` can be a diagnostic control, but hides
   evidence and is not a sufficient production repair. A parent-opened stderr
   file is not equivalent to SSH opening its own `-E` log.
3. Keep stdin/stdout byte-transparent for framed protocols. Keep diagnostic
   output separate from protocol stdout, bound the probe deadline and log
   retention, and treat the log as private. Invoke
   `agent-conduct-guidance:using-scratch-space` before allocating it.
4. If the child is alive but stalled, an available native stack/IO capture of
   that owned child can distinguish logging completion waits from network or
   authentication waits. Do not install a debugger implicitly or attach to,
   suspend or terminate an unrelated process.
5. Close and reap only the owned probes and remove their temporary logs after
   extracting a sanitized finding. Successful alternate-handle probes do not
   prove the original caller or remote service recovered.

The shared ssh-manager Windows spawn path already removes Win32-OpenSSH's
inherited descriptor-state environment entry for its newly redirected handles.
That is one known cause, not a universal explanation or a guarantee that all
stdio stalls are fixed. Do not clear the parent environment or assume a newer
source implementation is installed in the running consumer.

For Bridge failures, use `agent-bridge:agent-bridge-troubleshooting` when that
optional sibling is installed. SSH execution alone proves neither a valid
carrier handshake nor hosting-service readiness or message acceptance.
