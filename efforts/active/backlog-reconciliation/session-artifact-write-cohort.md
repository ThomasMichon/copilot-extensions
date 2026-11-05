*The following contribution was assisted using Copilot.*

# Session artifacts need the retained write-time boundary, not another cleanup

Canonical contract: [README.md](README.md).

The whole #2165 original and escalation retain structural write-time **block
OR flag** behavior for repository checkouts, dependency trees, SDK/toolchain
installs, browser profiles/user data and raw monitoring captures, while allowing
small legitimate curated session artifacts. Size/depth guidance and a lint/helper
are possible mechanisms, not permission to drop the stronger data-handling
boundary.

Credit sync-time category exclusion and portable scratch guidance. Destination
exclusion or deletion does not prove that source material was never persisted,
that every replica is clean or that exported material cannot reappear.
The historical escalation is not a new inspection or incident diagnosis.
No incident-specific source, profile, capture, replica or synced content is
accessed or republished here.

The three ranked message comparisons retain their finite scopes: existing-owner
workspace/source reads, build-tool acquisition and retained-source verification
do not implement this session-artifact write interception. An already-read
build-tool message is reused without new completion credit.

#2165 remains OPEN. No complete accepted write-time receiver, cleanup
appointment, sync change, interception implementation or deletion is established.
See the [shared journal](<journal/2026/11.04 guidance-platform-state-contracts.md>)
and [candidate graph](graph/completed-work-candidates.json).
