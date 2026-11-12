"""Session digest engine -- turn a session's ``events.jsonl`` into Markdown.

This is the reusable core shared by every consumer that needs a readable
rendering of one Copilot session: ``collate-session`` (the digest fed to the
``session-log-writer`` charter), ``ramp-up-session`` (the takeover brief fed to
the ``session-rampup`` charter), and any future summarizing charter. The
engine owns only the mechanical conversion -- reading session artifacts,
streaming turns, and rendering/segmenting Markdown. What a summarizing agent
does with the result (a session log, a takeover briefing, ...) is its own
charter layered on top; store placement and manifests stay with the caller.

Public surface:

- :func:`build_session_digest` -- collate a session into a segmented digest
  directory (``00-context.md`` + ``NN-turns.md``) and return a
  :class:`SessionDigest` describing it, optionally observing each turn so a
  charter can keep its own view (a tail, counts) without a second pass.
- :func:`render_session_digest` -- the same digest as one bounded string.
- Readers (:func:`read_workspace`, :func:`read_checkpoints`,
  :func:`read_rewind_index`) and section renderers (:func:`format_metadata`,
  :func:`format_checkpoints`, :func:`format_turn_stats`, :func:`format_turn`)
  so a charter can compose its own document from the same building blocks.
"""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from agent_logger.segmenter.turn_stream import TurnStream

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_TOOL_OUTPUT = 600      # chars per tool result body
DEFAULT_MAX_TOTAL = 120_000        # overall output budget (~30k tokens)
DEFAULT_SEGMENT_SIZE = 80_000      # ~20k tokens per segment file

DIGEST_CONTEXT_FILE = "00-context.md"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_ts(iso: str | None) -> str:
    """Format an ISO timestamp to a readable form."""
    if not iso:
        return "unknown"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return iso


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------

def read_workspace(session_dir: Path) -> dict[str, str]:
    """Parse workspace.yaml (simple key: value format)."""
    ws_file = session_dir / "workspace.yaml"
    if not ws_file.exists():
        return {}
    result: dict[str, str] = {}
    for line in ws_file.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            result[k.strip()] = v.strip()
    return result


def read_checkpoints(session_dir: Path) -> list[dict[str, str]]:
    """Read checkpoint markdown files, returning list of {filename, content}."""
    cp_dir = session_dir / "checkpoints"
    if not cp_dir.is_dir():
        return []
    results = []
    for f in sorted(cp_dir.iterdir()):
        if f.name == "index.md" or not f.suffix == ".md":
            continue
        try:
            results.append({
                "filename": f.name,
                "content": f.read_text(encoding="utf-8"),
            })
        except Exception:
            pass
    return results


def read_rewind_index(session_dir: Path) -> list[dict[str, Any]]:
    """Read rewind-snapshots/index.json for per-turn metadata."""
    idx_file = session_dir / "rewind-snapshots" / "index.json"
    if not idx_file.exists():
        return []
    try:
        data = json.loads(idx_file.read_text(encoding="utf-8"))
        return data.get("snapshots", [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_metadata(
    workspace: dict[str, str],
    session_start: dict[str, Any],
    cutoff_time: str | None,
    since_time: str | None = None,
) -> list[str]:
    """Render the metadata block as lines."""
    parts: list[str] = []
    sid = session_start.get("session_id") or workspace.get("id", "unknown")
    parts.append("# Session Digest\n")
    parts.append("## Metadata\n")
    parts.append(f"- **Session ID:** `{sid}`")
    _start = fmt_ts(session_start.get("start_time") or workspace.get("created_at"))
    parts.append(f"- **Session start:** {_start}")
    if since_time:
        parts.append(f"- **Digest since:** {fmt_ts(since_time)}")
    if cutoff_time:
        parts.append(f"- **Digest cutoff:** {fmt_ts(cutoff_time)}")
    _branch = session_start.get("branch") or workspace.get("branch", "unknown")
    parts.append(f"- **Branch:** {_branch}")
    _cwd = session_start.get("cwd") or workspace.get("cwd", "unknown")
    parts.append(f"- **Working dir:** {_cwd}")
    head = session_start.get("head_commit", "")
    if head:
        parts.append(f"- **Head commit:** `{head[:12]}`")
    summary = workspace.get("summary", "")
    if summary:
        parts.append(f"- **Auto-summary:** {summary}")
    cli_ver = session_start.get("version", "")
    if cli_ver:
        parts.append(f"- **CLI version:** {cli_ver}")
    parts.append("")
    return parts


def format_checkpoints(checkpoints: list[dict[str, str]]) -> list[str]:
    """Render checkpoints as lines."""
    if not checkpoints:
        return []
    parts: list[str] = []
    parts.append("## Checkpoints (pre-compaction summaries)\n")
    parts.append("These are curated summaries written by the CLI before context")
    parts.append("compaction. They capture work done up to that point.\n")
    for i, cp in enumerate(checkpoints, 1):
        parts.append(f"### Checkpoint {i}: {cp['filename']}\n")
        parts.append(cp["content"])
        parts.append("")
    return parts


@dataclass
class TurnStats:
    """Running turn and tool-call counts for the stats section."""

    turns: int = 0
    tool_calls: int = 0
    failed_tool_calls: int = 0

    def add(self, turn: dict[str, Any]) -> None:
        tool_calls = turn.get("tool_calls", [])
        self.turns += 1
        self.tool_calls += len(tool_calls)
        self.failed_tool_calls += sum(
            1 for tc in tool_calls if tc.get("success") is False
        )

    @classmethod
    def of(cls, turns: Iterable[dict[str, Any]]) -> TurnStats:
        stats = cls()
        for turn in turns:
            stats.add(turn)
        return stats


def format_turn_stats(
    stats: TurnStats,
    checkpoints: list[dict[str, str]],
    snapshots: list[dict[str, Any]],
) -> list[str]:
    """Render session stats from precomputed turn counts."""
    parts: list[str] = []
    parts.append("## Session Stats\n")
    parts.append(f"- **Turns:** {stats.turns}")
    parts.append(f"- **Tool calls:** {stats.tool_calls}")
    if stats.failed_tool_calls:
        parts.append(f"- **Failed tool calls:** {stats.failed_tool_calls}")
    parts.append(f"- **Checkpoints:** {len(checkpoints)}")
    parts.append(f"- **Rewind snapshots:** {len(snapshots)}")
    parts.append("")
    return parts


def format_turn(turn: dict[str, Any], index: int) -> str:
    """Render a single turn as a Markdown string."""
    parts: list[str] = []
    parts.append(f"### Turn {index}")
    if turn.get("timestamp"):
        parts.append(f"*{fmt_ts(turn['timestamp'])}*\n")

    user_msg = turn.get("user_message", "").strip()
    if user_msg:
        parts.append("**User:**\n")
        parts.append(user_msg)
        parts.append("")

    for msg in turn.get("assistant_messages", []):
        msg = msg.strip()
        if msg:
            parts.append("**Assistant:**\n")
            parts.append(msg)
            parts.append("")

    tool_calls = turn.get("tool_calls", [])
    if tool_calls:
        parts.append("**Tool usage:**\n")
        for tc in tool_calls:
            status = "✓" if tc.get("success") else ("✗" if tc.get("success") is False else "?")
            parts.append(f"- `{tc['tool']}` [{status}] — {tc.get('args', '')}")
            result = tc.get("result")
            if result and tc.get("success") is False:
                parts.append(f"  ```\n  {result}\n  ```")
            elif result and len(result) > 20:
                parts.append("  <details><summary>Result preview</summary>\n")
                parts.append(f"  ```\n  {result}\n  ```")
                parts.append("  </details>")
        parts.append("")

    return "\n".join(parts)


def format_digest(
    workspace: dict[str, str],
    session_start: dict[str, Any],
    checkpoints: list[dict[str, str]],
    turns: Iterable[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    cutoff_time: str | None,
    max_total: int,
    since_time: str | None = None,
) -> str:
    """Render the full digest as a single string (stdout mode).

    ``turns`` may be a lazy iterable such as a :class:`TurnStream`. Rendered
    turn text is kept only until it alone exceeds ``max_total`` (the output is
    then certain to be truncated inside the transcript); remaining turns are
    still consumed so the metadata reflects the whole window. The header is
    rendered after the last turn, so ``session_start`` is read only then.
    """
    stats = TurnStats()
    turn_texts: list[str] = []
    turn_chars = 0
    for i, turn in enumerate(turns, 1):
        stats.add(turn)
        if max_total < 0 or turn_chars <= max_total:
            text = format_turn(turn, i)
            turn_texts.append(text)
            turn_chars += len(text) + 1  # + the "\n" joining it to the previous part
    truncated_early = max_total >= 0 and turn_chars > max_total

    parts: list[str] = []
    parts.extend(format_metadata(workspace, session_start, cutoff_time, since_time))
    parts.extend(format_checkpoints(checkpoints))

    parts.append("## Conversation Transcript\n")
    if checkpoints:
        parts.append("*Note: Checkpoints above cover earlier work. The transcript")
        parts.append("below includes the full session for completeness, but checkpoint")
        parts.append("content is authoritative for the periods they cover.*\n")

    parts.extend(turn_texts)

    if not truncated_early:
        parts.extend(format_turn_stats(stats, checkpoints, snapshots))

    output = "\n".join(parts)
    if len(output) > max_total:
        output = output[:max_total] + f"\n\n... [digest truncated at {max_total} chars]"
    return output


def write_segments(
    workspace: dict[str, str],
    session_start: dict[str, Any],
    checkpoints: list[dict[str, str]],
    turns: Iterable[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    cutoff_time: str | None,
    output_dir: Path,
    segment_size: int,
    max_tool_output: int,
    since_time: str | None = None,
) -> list[str]:
    """Write segmented digest files to output_dir.

    Returns a list of created file paths (relative to output_dir).

    Layout:
      00-context.md   — metadata + checkpoints + stats (written last, on success)
      01-turns.md     — first batch of turns
      02-turns.md     — next batch, etc.

    Turns are never split mid-turn. Each segment file is ≤ segment_size
    chars (soft limit — a single huge turn may exceed it).

    ``turns`` may be a lazy iterable such as a :class:`TurnStream`: each turn
    is appended to its segment file as soon as it is produced, so only one
    turn is held in memory. ``00-context.md`` is written after the last turn,
    so ``session_start`` is read only then, and its presence marks a complete
    digest. If the run fails or is interrupted, segments already closed are
    kept as partial progress; the still-open segment and any ``00-context.md``
    are removed and the original exception propagates.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = TurnStats()
    segment_names: list[str] = []
    seg_fh: TextIO | None = None
    seg_chars = 0
    ctx_file = output_dir / DIGEST_CONTEXT_FILE

    try:
        # ── Turn segments ──
        for i, turn in enumerate(turns, 1):
            stats.add(turn)
            turn_text = format_turn(turn, i)
            turn_len = len(turn_text)

            # If adding this turn would exceed the segment size, flush first
            if seg_fh is not None and seg_chars + turn_len > segment_size:
                seg_fh.close()
                seg_fh = None

            if seg_fh is None:
                seg_num = len(segment_names) + 1
                fname = f"{seg_num:02d}-turns.md"
                seg_fh = open(output_dir / fname, "w", encoding="utf-8")
                segment_names.append(fname)
                seg_fh.write(f"# Transcript Segment {seg_num}\n\n")
                seg_chars = 0
            else:
                seg_fh.write("\n")
            seg_fh.write(turn_text)
            seg_chars += turn_len

        if seg_fh is not None:
            seg_fh.close()
            seg_fh = None

        # ── Context file (the completion marker, written last) ──
        ctx_parts: list[str] = []
        ctx_parts.extend(format_metadata(workspace, session_start, cutoff_time, since_time))
        ctx_parts.extend(format_checkpoints(checkpoints))
        ctx_parts.extend(format_turn_stats(stats, checkpoints, snapshots))

        # Add segment inventory so each reader knows the full picture
        total_turns = stats.turns
        ctx_parts.append("## Segment Inventory\n")
        ctx_parts.append(f"The conversation transcript ({total_turns} turns) has been split")
        ctx_parts.append("into numbered segment files in this directory. Each segment")
        ctx_parts.append("contains a consecutive batch of turns. Read them in order to")
        ctx_parts.append("reconstruct the full session.\n")

        ctx_file.write_text("\n".join(ctx_parts), encoding="utf-8")
    except BaseException:
        # Closed segments hold whole turns and are kept. The open segment and
        # the context file (the completion marker) are removed so an
        # interrupted run never reads as a complete digest.
        unfinished = [ctx_file]
        if seg_fh is not None:
            with contextlib.suppress(OSError):
                seg_fh.close()
            unfinished.append(output_dir / segment_names.pop())
        for path in unfinished:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
        if segment_names:
            print(
                f"collate: interrupted; kept {len(segment_names)} completed segment(s) in "
                f"{output_dir}; digest incomplete (no 00-context.md)",
                file=sys.stderr,
            )
        raise

    return [DIGEST_CONTEXT_FILE, *segment_names]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

TurnObserver = Callable[[dict[str, Any]], None]
StreamFactory = Callable[..., TurnStream]


@dataclass
class SessionDigest:
    """A collated session digest and the artifacts it was rendered from."""

    session_dir: Path
    output_dir: Path
    files: list[str]
    workspace: dict[str, str]
    session_start: dict[str, Any]
    checkpoints: list[dict[str, str]]
    snapshots: list[dict[str, Any]]
    stats: TurnStats
    turn_count: int


def clear_digest_dir(output_dir: Path) -> None:
    """Remove a previous run's digest files (``*.md``, ``manifest.yaml``)."""
    if not output_dir.is_dir():
        return
    for f in output_dir.iterdir():
        if f.is_file() and (f.name.endswith(".md") or f.name == "manifest.yaml"):
            f.unlink()


def build_session_digest(
    session_dir: Path,
    output_dir: Path,
    *,
    since: str | None = None,
    cutoff: str | None = None,
    segment_size: int = DEFAULT_SEGMENT_SIZE,
    max_tool_output: int = DEFAULT_MAX_TOOL_OUTPUT,
    clean: bool = True,
    on_turn: TurnObserver | None = None,
    stream_factory: StreamFactory = TurnStream,
) -> SessionDigest:
    """Collate ``session_dir`` into a segmented digest under ``output_dir``.

    Turns are streamed one at a time; ``on_turn`` sees each turn as it is
    written, so a charter can derive its own view (e.g. the ramp-up tail)
    in the same pass. ``clean`` first removes a previous run's digest files.
    ``stream_factory`` builds the turn stream (``TurnStream`` by default).
    Interruption semantics are those of :func:`write_segments`.
    """
    if clean:
        clear_digest_dir(output_dir)
    workspace = read_workspace(session_dir)
    checkpoints = read_checkpoints(session_dir)
    snapshots = read_rewind_index(session_dir)
    stream = stream_factory(session_dir, cutoff, max_tool_output, since=since)
    stats = TurnStats()

    def _counted() -> Iterator[dict[str, Any]]:
        for turn in stream:
            stats.add(turn)
            if on_turn is not None:
                on_turn(turn)
            yield turn

    files = write_segments(
        workspace=workspace,
        session_start=stream.session_start,
        checkpoints=checkpoints,
        turns=_counted(),
        snapshots=snapshots,
        cutoff_time=cutoff,
        output_dir=output_dir,
        segment_size=segment_size,
        max_tool_output=max_tool_output,
        since_time=since,
    )
    return SessionDigest(
        session_dir=session_dir,
        output_dir=output_dir,
        files=files,
        workspace=workspace,
        session_start=stream.session_start,
        checkpoints=checkpoints,
        snapshots=snapshots,
        stats=stats,
        turn_count=stream.turn_count,
    )


def render_session_digest(
    session_dir: Path,
    *,
    since: str | None = None,
    cutoff: str | None = None,
    max_tool_output: int = DEFAULT_MAX_TOOL_OUTPUT,
    max_total: int = DEFAULT_MAX_TOTAL,
    stream_factory: StreamFactory = TurnStream,
) -> str:
    """Render ``session_dir`` as one digest string, bounded by ``max_total``."""
    stream = stream_factory(session_dir, cutoff, max_tool_output, since=since)
    return format_digest(
        workspace=read_workspace(session_dir),
        session_start=stream.session_start,
        checkpoints=read_checkpoints(session_dir),
        turns=stream,
        snapshots=read_rewind_index(session_dir),
        cutoff_time=cutoff,
        max_total=max_total,
        since_time=since,
    )
