---
name: controlling-copilot-updates
description: >-
  Controls Copilot CLI live auto-update preferences, inspects inherited version
  pins, delegates native updates and selects cached versions. Use when enabling
  or disabling Copilot auto-update, sweeping stale CLI pins, propagating update
  preferences across machines, rolling Copilot back or pulling a version forward.
---

# Controlling Copilot updates

Use the exact Agent Machines executable from this session's command catalog,
written below as `<agent-machines catalog argv[0]>`. These operations change
live user settings, not repository policy. The complete contract and limitations
are in `../../docs/copilot-cli.md`.

## 1. Inventory the actual execution environments

Resolve the selected local/remote environments through their existing machine
and transport owners. Treat native Windows and WSL as distinct; do not infer a
guest environment that is not declared. On each target, use its own actual
Agent Machines invocation:

```text
<agent-machines catalog argv[0]> copilot-cli status --json
<agent-machines catalog argv[0]> copilot-cli versions --json
```

Record the live preference, channel, native version answer, inherited/user
overrides and cache readiness. A current parent can carry an old package or
disable variable even after the persistent user setting changes. Identify
whether a pin is an intentional isolated test/sandbox selection before removing
it. Do not equate the native updater with plugin/runtime maintenance.

If a target does not recognize this additive command family, update that owner
through its supported workflow first. Do not issue an effectful new command and
then replay another form on a parser-looking error.

## 2. Confirm and apply the requested live policy

Preview before application:

```text
<agent-machines catalog argv[0]> copilot-cli auto-update on --json
<agent-machines catalog argv[0]> copilot-cli auto-update on --apply --clear-overrides --json
<agent-machines catalog argv[0]> copilot-cli auto-update off --apply --json
```

Use `--clear-overrides` only when removing the named stale update/package
overrides is authorized. It does not edit profiles or restart existing parents.
Do not use a persistent `COPILOT_AUTO_UPDATE=false` as the ordinary quick toggle:
the live `autoUpdate=false` preference stops downloads without forcing an older
bundled version. Preserve declarative requirements and report any owner that
would reassert a conflicting value; a live edit does not overrule it.

For propagation, execute the same operation through each selected target's
already admitted local/SSH channel and retain a result per environment. Do not
copy this machine's catalog path to another machine, invent topology or create
a parallel synchronization service. An unreachable/unsupported target is
unfinished coverage, not a successful fleet-wide change.

## 3. Update or deliberately choose a version

```text
<agent-machines catalog argv[0]> copilot-cli update --json
<agent-machines catalog argv[0]> copilot-cli update --apply --json
<agent-machines catalog argv[0]> copilot-cli launch 1.0.100-1 --json
<agent-machines catalog argv[0]> copilot-cli launch 1.0.100-1 --apply
```

Omit `--channel` to retain the native effective current channel. Select
`--channel stable` or `--channel prerelease` only when requested. Update one
environment at a time, retain native installer custody and leave active
sessions untouched.

Cached launch is explicit per invocation, not a persistent global pin. Use
`--arg=--flag` for forwarded native arguments. Native `/downgrade <version>` is
the exact-version acquisition/restart path when the desired version is not
cached; it requires consent to restart the attended session. Do not invent a
download URL, replace the binary, delete newer caches or bypass native checks.
For a managed session, retain its existing launch/resume owner instead of
creating a second session owner through the raw launch command.

## 4. Verify the actual outcome

Re-read status in a refreshed environment. Distinguish:

- preference persisted versus still-blocking inherited/machine policy;
- update downloaded versus the version a fresh launch selects;
- a fresh launch versus an already-running session;
- inspected source/released commands versus an installed target's capabilities.

Preserve failures and unknown native outcomes before retrying. No operation here
authorizes killing another process, clearing an installer lock or restarting a
shared service.
