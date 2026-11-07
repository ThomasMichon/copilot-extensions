# Effort readback measurement

Back to the [effort](README.md).

## Method

A read-only script scanned seven days of local Copilot session event logs from
one heavy user (20 sessions; 9 read effort documents). It counted tool calls
that read `efforts/**/*.md`:

- `view` calls with an effort path, with or without a line range;
- shell reads of an effort path (`cat`, `sed -n`, `head`, `tail`, `Get-Content`
  and similar), matched heuristically.

Result size was estimated from each tool result's length (about four characters
per token). Each call was assigned to one category, using the session's
context-clear events as window boundaries:

| Category | Meaning |
|---|---|
| First read in the session | The file had not been read earlier in the session. |
| First read after a new context window | Read earlier in the session, but not yet in this window. |
| Same window, new range | A range of the file not yet read in this window. |
| Same window, after a local edit | Re-read after the agent edited the file in this window. |
| Same window, overlapping re-read | Overlaps a range already read in this window; file unchanged. |
| Same window, byte-identical duplicate | Same path, range and result as an earlier read in this window. |

## Results

| Category | Calls | Result tokens | Share |
|---|---:|---:|---:|
| First read after a new context window | 2,330 | 2.53M | 34.8% |
| Same window, new range | 1,358 | 1.78M | 24.4% |
| Same window, overlapping re-read | 1,809 | 1.57M | 21.6% |
| First read in the session | 1,173 | 0.83M | 11.4% |
| Same window, after a local edit | 482 | 0.32M | 4.5% |
| Same window, byte-identical duplicate | 193 | 0.24M | 3.3% |
| **Total** | **7,345** | **7.27M** | 10.3% of 70.26M tool-result tokens |

- README files were about 82% of the re-read tokens among the 20 most re-read
  effort files. One README alone accounted for 873 reads and about 1.16M tokens.
- The largest active READMEs were 100–390 KB, mostly inline journals or Plan
  checkboxes that accumulated PR-by-PR history.

## Interpretation

- Recovery after a new context window and paging through large files (about
  4.3M tokens) is the addressable bulk. A small README and a targeted digest
  shrink both.
- Overlapping re-reads and duplicates (about 1.8M tokens) look avoidable, but
  this is a heuristic: some overlap is legitimate (for example, an older read
  may have been compacted away). It is not a proven waste floor.

## Caveats

- Tool-result tokens are telemetry estimates, not billed input or spend.
- `grep`/search results were excluded; shell read detection is heuristic.
- A shell command reading several paths had its result split evenly across them.
- One older session lacked complete telemetry and is undercounted.
- One user's week; other repositories and work styles will differ.

## Digest check

[`effort_digest.py`](../../../../plugins/efforts/skills/planning-efforts/scripts/effort_digest.py)
text output on the eight largest active READMEs in this repository at the time
(226–387 KB) was 1–9% of each README's size (2.7–35 KB).
