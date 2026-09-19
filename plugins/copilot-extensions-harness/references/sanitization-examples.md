# Sanitization — worked examples

The `contributing-to-copilot-extensions` skill's *Sanitization* rule requires
abstracting every attached artifact before it lands in this public repo.
Examples, traces, repros, and references are the easy leaks — anything pasted
to illustrate a change tends to smuggle consumer-side detail. Per category:

- **Error output / stack traces / logs** carry internal paths, hostnames,
  usernames, and IPs — replace them with neutral placeholders (`/path/to/repo`,
  `HOST`, `user`, `192.0.2.10`) and drop lines that don't bear on the issue.
  *E.g.* `at C:\Users\jdoe\src\internal-app\...` → `at <repo>/...`.
- **Reproductions** must reduce to the **minimal, generic steps** that repro on
  a bare checkout — not "run it inside <private system> with <private
  config>". Strip the private setup; keep only what a stranger needs.
- **Example / sample data** must be synthetic, never real internal values
  (record IDs, tokens, topic roots, private URLs). Use `example.com` and
  obviously-fake values.
- **References** must point only at **public** anchors (a repo issue/PR/commit
  in this repo) — never an internal tracker, private doc, or session/task ID.
  Attach the concrete internal artifact to the driver's **private** plan and
  link the public issue to that; never the reverse.
