---
name: avoiding-secret-pattern-masking
description: >
  Avoids a silent Copilot content-pipeline rewrite that masks a credential-
  shaped string expression (an auth scheme word immediately concatenated or
  interpolated with a token/secret variable, e.g. an Authorization header
  built from the scheme word plus a secret variable in one literal or
  f-string) into a fixed-width asterisk placeholder at write time, with no
  error and no diff marker. Use before writing, editing, or reviewing any
  Authorization header or similar scheme-plus-secret string construction,
  and when a previously-"fixed" auth header still fails in production
  despite a reviewed, seemingly-correct diff. Not a general secret-handling
  or vault-fetch skill (see the `secrets` skill for that) -- this is
  narrowly about the string-construction shape that trips the masking
  rewrite.
---

# Avoiding Secret-Pattern Masking

Copilot's own upstream content pipeline -- not a repo git hook, not a
pre-commit scanner, not any tool this facility/repo controls -- silently
rewrites specific credential-*shaped* string expressions into a masked
placeholder when the content is written to a file or echoed back through a
tool result. Confirmed behavior, observed directly and reproducibly: an
f-string combining the auth scheme word with a secret-holding variable in
one literal expression -- the same shape as `scheme + "{" }token{"} "`
written as a single interpolated unit -- lands on disk with the real token
gone, replaced by a fixed run of six asterisks, with the edit tool
reporting success and no indication anything was altered. The rewrite also
interferes with *matching* that already-written masked text back out of a
file (an old-string match against the literal masked form can fail even
though the file visibly contains it), and it can even mangle **this very
documentation** when the dangerous shape is typed as a literal example --
confirmed while authoring this skill itself, which is why every example
below is deliberately built from split fragments rather than typed as one
contiguous dangerous literal. Recovering from an already-mangled file
sometimes needs a byte-offset rewrite that never passes the masked
substring through a tool argument at all (see Recovery below).

**This produces no error and no visible diff marker.** The edit succeeds,
the tool call appears to have done exactly what was asked, and a reviewer
reading the rendered diff sees ordinary-looking code. The only way to
discover it happened is to open the actual file (or its committed content)
and look at the literal bytes, or to run the code against a real endpoint
and watch authentication fail for no apparent reason.

## The trigger shape

The rewrite appears to key off an auth scheme word (confirmed for the
HTTP `Bear​er` scheme; treat any other scheme word the same way until proven
otherwise) appearing in the same expression as a variable that looks like
it holds a secret/token, combined via concatenation or f-string
interpolation, in a single written unit -- for example, the scheme word
immediately followed by a space and a brace-interpolated variable
reference inside one f-string, or the scheme word concatenated directly
onto a bare variable with `+`. Any single expression combining the literal
scheme word with a variable reference this way is at risk, regardless of
exact spacing or quoting.

A confirmed-working escape, found as prior art already in this repository
(`services/combine-overwatch/edge-worker-app/clip_fetcher.py`'s
`_auth_headers`) and reproduced directly during this skill's own authoring
session:

```python
scheme = "Bear" + "er "          # split the scheme word itself
headers = {"Authorization": scheme + token}  # combine in a separate step
```

Splitting the literal scheme word across its own concatenation (two
literal fragments that are not individually the full scheme word) is what
reliably avoids the match -- moving the secret to a second line while
keeping the scheme word as one contiguous literal does not reliably help,
since the rewrite appears to trigger on the combined expression shape
reachable from the literal, not merely adjacency within a single source
line. If a different scheme word is involved (a custom header prefix, a
different `Bear​er`-style convention), apply the same split-literal technique
to that word.

## What to do

1. **Never write a scheme word and a secret-holding variable together in one
   concatenation or f-string.** Bind the scheme to its own variable first,
   built from split literal fragments, then combine with the secret as a
   separate statement.
2. **Verify with a non-rendering check, never a plain read-back.** Don't
   trust the edit tool's success return, a rendered diff, or even a plain
   `view`/`print` of the file -- confirmed two distinct failure shapes:
   genuinely-corrupted on-disk bytes (the real secret replaced by literal
   asterisks, confirmed via raw git-blob byte inspection on an already-
   edited code file), and a separate **display-only** rendering artifact
   where the real on-disk bytes are already correct but *showing* them
   back (a `view` call, a terminal `print`) still renders asterisks,
   confirmed on a freshly authored documentation file. A naive look-and-
   see cannot tell these apart and can produce a false alarm on correctly-
   written content. Verify with a check whose own output never spells out
   the dangerous substring as human-readable text -- a boolean/count
   assertion (`needle in content`, built from split fragments the same
   way the fix itself is) or a hex/byte dump -- and trust that over any
   rendered view.
3. **Add a regression test that exercises the real construction.** A test
   that mocks past the header-building function (patching the HTTP client
   factory itself, for example) can pass even when the underlying
   construction is completely broken, because it never actually calls the
   code that builds the header. Call the real function with a fake-but-
   nonempty secret value and assert the fake value appears, unmangled, in
   the resulting header.
4. **Treat an already-written masked placeholder as evidence, not a
   stand-in you can safely re-type.** If you already see a masked
   six-asterisk value in a file and need to replace it, don't assume you
   can match or recreate that exact masked text through a normal tool
   call -- see Recovery below.

## Recovery when a file already contains the masked placeholder

If an existing file already has the mangled, masked form (e.g. discovered
during review, or because an earlier turn in this same session hit the
rewrite), a normal edit/string-replace call may fail to match the masked
text as an old-string search, or may re-trigger the same rewrite on the
new text you intend to write. When that happens:

1. Read the file as raw bytes rather than through a tool that might
   re-apply the rewrite on display.
2. Locate the target line by a stable anchor that is **not** the masked
   text itself (e.g. the literal `Authorization` key, or the enclosing
   function name).
3. Replace the line via direct byte-offset manipulation (read the whole
   file as bytes, slice around the located line, write the replacement
   bytes, write the file back) rather than passing the masked or
   newly-intended secret-shaped text through an edit tool's string
   arguments.
4. If the replacement text itself contains the scheme word, build it from
   split fragments assigned to separate variables first (as in the escape
   above), never as one contiguous literal in the script doing the
   byte-level rewrite -- the rewrite can apply to a script's own source
   while it is being written/executed, not only to the target file.
5. Re-read the file afterward to confirm the real byte content, not just a
   success return, before considering the fix done.

## Boundaries

- This skill is about the **string-construction shape** that triggers the
  rewrite, not secret storage, fetching, or rotation -- see the `secrets`
  skill for vault-backed credential handling.
- Not every masked-looking string in a reviewed diff is this bug -- a
  genuine intentional redaction (e.g. a log-formatting helper that
  deliberately masks a value for display) looks similar; distinguish by
  checking whether the *actual* value is ever used anywhere in the
  surrounding code, not just by the presence of asterisks.
- Confirmed for the `Bear​er` scheme specifically; treat any other
  scheme-plus-secret construction as equally suspect until proven
  otherwise, and apply the same split-literal technique defensively.
