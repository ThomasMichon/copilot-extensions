# External-service outage receipts

Use with [deployment diagnosis](../skills/diagnosing-copilot-extensions/SKILL.md)
or [release diagnosis](../skills/diagnosing-validate-and-promote-failures/SKILL.md).
This applies to outage claims about any external provider, not only GitHub.
It is a reporting standard, not a prerequisite for diagnosing or recovering
an already-authorized operation.

## Bring the bulletin

Before calling a failure an external-service outage, find the provider's
first-party incident bulletin or service-health advisory and include its
direct link in the operator-facing report. A status homepage, search result,
third-party monitor, generic error or failed request alone is not that receipt.
Retain the bulletin title, affected component and applicable region/scope,
incident window, current state, provider update time and your observation time
in UTC. Match those facts to the observed failure's component and time.

Distinguish provider acknowledgement from causation: a matching bulletin can
support attribution, but does not prove that it caused this particular failure.
Preserve the operation's own error, request/run evidence and actual outcome.
Do not stretch a component-specific incident into a provider-wide outage.

If the bulletin is unavailable, inaccessible, stale or does not match, say
**suspected provider-side issue; unconfirmed** and state what was checked and
when. Missing acknowledgement proves neither an outage nor provider health.
Continue checking local configuration, authentication, permissions and source
behavior rather than using an outage guess to dismiss a real defect.

For private service-health advisories, use the existing authorized access owner.
Cite or share the advisory only within its permitted audience; otherwise report
the evidence limitation. Do not obtain new access, copy restricted details into
public reports or invent a public bulletin.

## Recovery is another observation

Read the bulletin's current state and incident timeline, not only an
"operational" component badge. Monitoring, mitigation and a draining job backlog
are not the same as a resolved incident. A resolved bulletin is provider
recovery evidence, not proof this operation succeeded.

Before a bounded retry, reconcile the original operation's effects and current
owner/candidate, then use its supported recovery path. Preserve failed history,
unknown outcomes, approvals and every required gate. Bulletin evidence never
authorizes force, service restarts, policy changes or blind replay.
