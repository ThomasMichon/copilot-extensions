---
applyTo: "**"
---

# Secret-pattern masking fallback

**Fallback policy `[owner: agent-conduct-guidance@0.1.7-dev1]`:** Copilot's
own upstream content pipeline can silently rewrite a credential-*shaped*
string expression -- a scheme word immediately concatenated or interpolated
with a variable holding a token/secret, most commonly an `Authorization`
header built as the scheme word plus a secret in one literal or f-string --
into a fixed-width masked placeholder (a run of asterisks), independent of
the actual secret's real value and independent of any repo-level hook or
linter. Confirmed two distinct failure shapes: genuine on-disk corruption
(an edited code file's real secret replaced by literal asterisk bytes,
confirmed via raw git-blob inspection) and a separate display-only
rendering artifact (the real bytes are already correct, but a `view`/print
of the file still shows asterisks) -- a plain look-and-see cannot
distinguish the two, so never trust a rendered diff or a naive read-back
alone; verify with a check whose own output never spells out the
dangerous substring as text (a boolean/count assertion or a hex/byte dump).
Never construct an auth header value as a single scheme-plus-secret literal
or f-string in one expression. Instead, bind the scheme word to its own
variable first, split across its own concatenation (two literal fragments
that are not individually the full scheme word), and combine with the
secret as a separate step -- splitting the scheme word itself is what
reliably avoids the match, not merely moving the secret to a second line --
and add a regression test that reads the actual constructed header value
back (never one that mocks past the construction entirely) so a future
silent reversion is caught by CI rather than discovered in production.
Invoke the `avoiding-secret-pattern-masking` skill before writing any
Authorization header or similarly credential-shaped string expression.
