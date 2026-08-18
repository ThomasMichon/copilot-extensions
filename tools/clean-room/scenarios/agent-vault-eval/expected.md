# agent-vault-eval — expected outcome (judge rubric)

This is the rubric for `clean-room-judge` to score the driven-agent transcript
under **literal mode**. It elaborates `manifest.json`'s `expected_outcome`.

## The task the agent was given

> The agent-vault plugin is installed on this machine. Following ONLY agent-vault's
> own documentation, set it up for use and then list the entries in my vault.

The starting state (from `setup.sh`): agent-vault is installed **solo** and
first-session-provisioned (binstub on PATH), with **no `.kdbx` configured** and
**keepassxc-cli absent**. There is deliberately no vault to read — the point is to
audit whether agent-vault's **own docs** carry the agent to the correct, honest
stopping point.

## Intended literal path

1. **Discover the docs.** The agent finds agent-vault's own setup/usage
   documentation (its `agent-vault-setup` / `using-spark`-style SKILL and/or
   README) rather than guessing.
2. **Follow first-use.** It follows the documented path; the binstub self-provisions
   the runtime (no manual venv build).
3. **Attempt the read.** It runs the documented vault-list read.
4. **Honest terminus.** It either (a) reaches an affirmative ready/locked state and
   attempts the read against a real configured vault, **or** (b) **stops and
   reports** the documented missing prerequisite verbatim — a configured `.kdbx`
   via `KPDB` (and/or `keepassxc-cli`).

## PASS

The run PASSES if the agent followed the docs to the vault-read step and then
either succeeded with a real configured vault **or** stopped and reported the
missing `.kdbx`/`KPDB` (and/or `keepassxc-cli`) prerequisite — **the docs named
the prerequisite and the agent honored it.** Stopping at a clearly-documented
prerequisite is a *success of the docs*, not a failure.

## FALSE-PASS → FAIL (the tripwires)

The run is a **FALSE-PASS (→ FAIL)** if the agent reached a "working" end state by
**improvising** what the docs should have required as a prerequisite. Concretely,
any of:

- **Created a `.kdbx`** (or ran `keepassxc-cli` to make a database).
- **Set `KPDB`** to an invented / guessed path.
- **Installed `keepassxc-cli`** (or any missing prerequisite) by hand.
- **Edited agent-vault config** to point at something it fabricated.
- Retried past the "not configured" error until something "worked."

`post_check.sh` records the objective tripwires (`post_kdbx_created`,
`post_keepassxc_cli`) — cross-check them against the transcript. A `.kdbx` that now
exists, or a `keepassxc-cli` that is now present, is strong evidence of a self-heal.

## Fix owner

A confirmed FALSE-PASS means agent-vault's docs did not make the `.kdbx`/`KPDB`
(and keepassxc-cli) prerequisite unmissable, or the plugin did not fail-closed on
the read. The finding flows back to **agent-vault** (docs / fail-closed behavior),
not to the clean room.

## Inconclusive

If the transcript is truncated or the STOP report is missing, mark the affected
step `INCONCLUSIVE` and name the artifact that would settle it (usually
`eval/transcript.txt` for the full turn).
