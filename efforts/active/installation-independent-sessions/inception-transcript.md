# Installation-Independent Sessions - Inception

Back to the [canonical effort](README.md).

The operator's literal proposal is retained below, including original typos.
The effort README labels subsequent agent-recommended controls separately.

## Operator - Minimal launcher and redirectable hooks

> Can we look into making the session launcher as barebones as possible, using "hooks" before and after the copilot session that call through to redirectable binstubs? By the time a Copilot session exits, the universe around it can look very different, but the core construction stays the same:
>
> \<process root>
>   mux server
>     session wrapper
>       copilot
>
> Before copilot, we obviously need to ping the mux daemon that the session exists, and at the end of copilot we'd like to send an affirmative cancellation notice, so that we're not tracked as a crash. But in theory all that can e routed through the version-redirected binstub. Simply session-start and session-end ps1 or sh calls, taking arguments for the worktree id, copilot session and exit code, and some other basic data.

## Agent - Proposed boundary and safeguards

> **Yes—the long-lived wrapper should own the process lifetime, not the surrounding runtime’s policy.** I traced the current implementation: its callbacks deliberately use the Manager version that launched the pane, and the wrapper carries logging, cleanup, token/prompt handling, and crash UI. That is exactly the coupling to remove.
>
> The target should be:
>
> ```text
> prepare launch using current runtime → Copilot argv/env/cwd
>
> stable session wrapper:
>     invoke redirectable session-start hook
>     run Copilot directly; capture its real exit status
>     finally invoke redirectable session-end hook
>     exit
> ```
>
> Each hook invocation should resolve the **currently selected runtime within the same installation**—not reuse a Python path or functions loaded before Copilot started.
>
> A few contracts matter:
>
> - **End means “stop watching this launch.”** Preserve Copilot’s actual exit code separately; an observed nonzero exit is not an unobserved host crash, nor automatically an intentional user cancellation.
> - **Identify the launch incarnation**, not just the worktree. A delayed old end-hook must never unregister its replacement. Copilot’s session ID can be bound later if unknown before launch.
> - **Await bounded admission of the end notice**, not merely successful background-process creation. Failed delivery remains explicit uncertainty.
> - **Keep kernel containment in the wrapper.** On Windows, the kill-on-close Job handle must live as long as the wrapper; a short-lived hook cannot hold it.
>
> Two implementation details are easy to miss: the shell wrapper itself must not be rewritten/pruned while running, and preparation must finish before launching Copilot directly—otherwise the extra setup shells remain in the process tree.
>
> This fits the existing session-hosting boundary well. I’d make it a small, stable wrapper protocol with current-version lifecycle handlers, rather than another layer of callbacks inside the existing large wrapper.

## Operator - Installation-removal survival

> This sounds good. The expectation is that you can uninstall+reinstall or live-update the entire worktree-manager and agent-worktrees systems around a running, muxed Copilot session, and it *shouldn't notice*. Only during initial launch and final exit should it then call into "whatever is installed a the time" and delegate the current incarnation of the flow. We could hedge by copying the wrapper ps1 to a worktree-specific folder, and running that instance of the script, just to ensure even it isn't holding any locks. This lie like falling asleep and your house gets deconstructed around you; you wake up on your bed on a patch of dirt, but you didn't get disturbed.

## Agent - Literal survival experiment

> **Exactly—the running session should belong to the worktree, not either installation.** Keeping an old runtime alive isn’t enough; both installations must be removable without disturbing it.
>
> I’d make the wrapper copy **mandatory, not a hedge**:
> - Copy the tiny `.ps1`/`.sh` into a launch-specific location outside both runtime roots and removable installation configuration.
> - Leave it dependent only on the OS shell, kernel containment, terminal handles, and Copilot—not installed helper files or waiting Python processes.
> - Resolve the lifecycle binstub afresh at start and end. No cached interpreter, daemon endpoint, or dot-sourced runtime code.
> - If nothing is installed at exit, close quietly without attempting provisioning or pretending the end notice was delivered.
>
> The mux **server** must survive independently; the mux **management daemon** can disappear and return. Its replacement handles the original launch’s eventual end, using the stable launch incarnation.
>
> The decisive acceptance test is your house analogy made literal: start under installation A, actually uninstall both systems in disposable roots, confirm the **same mux, wrapper, and Copilot processes remain interactive**, install B, then exit and verify B handles the end correctly. Also test exiting while neither system exists.
>
> Simply copying today’s wrapper wouldn’t achieve that—it still reaches back into installed helpers. The copied wrapper must be genuinely self-contained.

## Operator - Proof and renewed direction

> Worth trying this in a clean-room container

> Great. Continue driving my proposal as a proper effort.

The operator confirmed the proposed slug:

> installation-independent-sessions

## Operator - Priority and maintenance window

> I have quiesced nearly all other sessions across all machines. Your work, which isolated Copilot sessions from updates to copilot-extensions tools and services, has priorty. I'm expanding the vision to include the concept of a "maintenance window and notification", where if the agent-* tols suddenly disappear out from under a running agent, then agent need to have a strategy to "pause", read a local-machine banner with instructions, and wait out the maintenance window. Once the maintenance window ends (the yaml file clears), then agents can resume.

## Operator - Selected live test host

Only the private host name is redacted below. The authorization applies to
that one operator-selected machine, not to an arbitrary host or fleet.

> As you continue this effort, feel free to use \<selected-test-host> as your test machine: you may audit the install footprint, separate state files from runtime data, and then carefully uninstall copilot-extensions services and tools from it, taking care to leave SSH-gateway connectivity running. Try to get to the point where you only leave muxed copilot processes alive, with all our services stopped and gone. Then focus on being able to perform a reinstall which stitches back up to the runtime data and carries on as though nothing was amiss. YOu have my full approval to uninstall, reinstall, re-release, and unblock main promotion with workflow fixes as needed until \<selected-test-host> demonstrates a "round trip" through install and reinstall, with a "clean state" in the middle. Definitiely worth making backups of data we store in ~/.agent- and ~/.&lt;repo&gt; first!

## Operator - Legacy session migration

The agent explained that the existing muxed sessions have legacy runtime-Python
ancestors which a source update cannot retrofit. It asked how to handle those
sessions after verified backups and qualification of the fixed launcher.

The operator selected the structured-form value:

> controlled-relaunch-after-fix

The option's full label was:

> Authorize controlled closure and relaunch after backups and launcher qualification, preserving resumable session state
## Operator - exact-set mux restoration and command entry points

> Once you have the new Mux wrapper, it should be pretty straightforward to each existing Mux pane on the target machine and then replace it with the new wrapper process. You'll just need to note which worktrees are currently active you can restore that exact set, properly resuming back into their associated sessions.
>
> I'd like it if `<repo> worktrees copilot --worktree-id ____` was the straightforward "launch this terminal into the mux session for the target worktree, resuming or re-attaching as appropriate". And we need a command which can simply create/restore the mux session for a worktree without attaching the current terminal, so other systems can leverage it to resume work.

## Operator - per-worktree admission and two-cycle live test

The private selected host name is replaced by `[selected test host]`; no
surrounding requirement is changed.

> We only want to block launches into the *same* worktree concurrently, before we have a chance to stand up the Mux session that other launch attempts would find themselves joining. We don't want to serializae launches into different worktrees; different worktrees should be able to launch in parallel

> Great. Continue driving. Your goal is to be able to have us live-test on [selected test host]: we'll record which sessions are currently active, exit all their current mux sessions, uninstall (and likely manually scrub) copilot-extensons services and tools, do a fresh install, restore the muxes using the new system, then uninstall, scrub, and reinstall again. When I say "scrub" I just mean straggler processes and detritus that our current uninstall flow might miss, not runtime state or config.

> Make sure you have an effort tracking the remaining phases of this work, so you can burndown to completion
