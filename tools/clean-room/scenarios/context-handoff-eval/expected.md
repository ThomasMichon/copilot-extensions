# Context handoff efficiency eval

Credit only the literal prompt-first flow:

1. The compact seed is the single initial prompt. Copilot cannot have a session,
   run sessionStart hooks, or bind a successor before that prompt is submitted.
2. sessionStart may associate the resulting real session with the pending token
   and worktree, but the predecessor remains head.
3. One explicit `/consume-handoff` acknowledgement performs takeover. The
   expanded transcript must show the stored canary and full brief.
4. A predecessor without PID plus creation proof is preserved, not retired.
5. Use `context-handoff-eval-metrics.json` for seed characters/tokens,
   structured submitted-prompt/turn/tool-call evidence,
   takeover/retire-decision timing, fidelity hashes, and lifecycle ordering.
   The metrics must read runner-expanded `turns.jsonl`; transcript text mentions are not
   evidence of a submitted prompt or tool invocation. `turn.prompt` must equal
   the runner-written `eval/prompt.txt` literal-mode composite and contain the
   exact handoff seed. The completed `consume_handoff` call's structured
   `content` must contain the full stored payload byte-for-byte.

A run that uses the raw recovery command but never performs lifecycle
acknowledgement is a failure, even if it prints the payload.
