# GitHub Actions platform-error errata

Use with [release diagnosis](../SKILL.md) and its
[external-service receipt procedure](../../../references/external-service-outages.md).

## Internal error, startup failure or a job that never acquired a runner

Read workflow-level annotations on the run page as well as job/check records.
A provider failure may have no job log, runner, steps or job-level annotation.
Distinguish jobs that actually failed tests from jobs that never executed;
retain every executed job's result and all skipped downstream publication or
merge gates. A job titled "Promote" succeeding does not establish a Main merge.

Check [GitHub Status](https://www.githubstatus.com/) and follow the relevant
incident's **direct bulletin link**. Include that link in the report, with the
Actions component, matching UTC failure window, incident state and observation
time. A failure affecting another GitHub product is not an Actions receipt.
If no matching bulletin can be obtained, report the observed workflow failure
and label provider-outage attribution unconfirmed; do not state "GitHub is down".

Individual component badges may show operational while the bulletin still
reports monitoring or a processing backlog. Report both facts and distinguish
mitigation from full recovery. Re-read the incident before treating recovery as
changed evidence for another attempt.

Once recovery is supported, reconcile Main, the existing immutable candidate,
admitted source, retained evidence and any newer promotion owner. Follow a
newer legitimate promotion when it already owns delivery. Otherwise use the
native bounded run/job recovery supported by the current workflow; verify
admission and the actual resulting qualification, publication and merge.
Do not patch source to hide a provider error, create a competing promotion,
waive a required check or repeat unchanged retries.
