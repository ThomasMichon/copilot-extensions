*The following contribution was assisted using Copilot.*

# Caller-owned interactive SSH and healthy Git execution keep separate owners

Canonical contract: [README.md](README.md).

## #2316 - Preserve the held caller-owned interactive contract

Retain the original explicit contributor claim: interactive command-file/PTY,
repeatable strictly loopback local/reverse forwards, prevalidation before
acquisition, diagnostic/ACP compatibility, provisioning opt-outs, account/
claim/target-lock and relay lifetime, exact child exit status and owned cleanup.
The later generation-bound native start/attach/resume/status/stop, live routing/
rebinding and provider-mode exclusion remain under the same architecture hold.

The actual #2318 is CLOSED but UNMERGED, not a released implementation or
claim release. Managed hosting and ordinary noninteractive/no-PTY command-file
execution are different consumers. Preserve its concrete architecture decision
and live acceptance rather than infer abandonment from age or invent a general
acknowledgement ceremony.

The shared relay-readiness message and Peer Launch typing do not establish
this interactive/native outcome. A known protected ranked description stays
unread without retry. #2316 remains OPEN under its existing custody.

## #2399 - Separate normal Git execution from connection preparation

Retain the ordinary noninteractive healthy Git/partial-clone case. Establish
a reviewed normal command budget **OR** supported no-execution-deadline behavior
after connection, separate from connect/provision/credential warmup policy at
the actual execution/cleanup boundary. Flag existence alone is not independent
phase semantics.

Keep connect/credential/command expiry diagnostics, explicit overrides, exit
status, cancellation/relay/claim/owned-child cleanup and actual long-progress
versus true-stall qualification. Inspect interrupted Git state before retry;
do not automatically replay mutation-capable recovery commands or raise every
worker/transport deadline.

Pre-plugin relay readiness/cleanup, read-only update-authentication probes and
hook-timeout review guidance are different contracts. The #10293 whole
description concerns bounded authentication defaults, not this remote-command
budget. Earlier explicit relay/token guidance remains credited.

#2399 remains OPEN. Neither held interactive #2316 nor far-side ACP readiness
receives this independent ordinary execution outcome. All six ranked relations
retain their noncompletion/protected tiers in the
[candidate graph](graph/completed-work-candidates.json).
No SSH, listener, forwarding, Git operation, timeout experiment, credential
access or resource/claim mutation occurs.
See the [shared journal](<journal/2026/11.04 guidance-platform-state-contracts.md>).
