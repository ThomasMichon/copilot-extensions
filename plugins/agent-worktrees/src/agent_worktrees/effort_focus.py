"""Validation and rendering for a worktree's active-effort binding."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from . import git_ops

MAX_EFFORT_PATH_CHARS = 512
MAX_EFFORT_LABEL_CHARS = 180
MAX_EFFORT_FILE_BYTES = 512 * 1024
MAX_EFFORT_ORIENTATION_CHARS = 480

_CLOSED_STATUSES = frozenset({"done"})
_ALLOWED_STATUSES = frozenset({"draft", "active", "blocked", "done"})
_HEADER_RE = re.compile(
    r"(?mi)^-\s+\*\*(?P<name>Slug|Status):\*\*\s*(?P<value>[^\r\n]+?)\s*$"
)
_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
_TASK_RE = re.compile(r"(?m)^\s*(?:[-*+]|\d+[.)])\s+\[([^\]])\]\s+")
_HTML_COMMENT_RE = re.compile(r"\s*<!--.*?-->\s*$")
_SPACE_RE = re.compile(r"\s+")
_ILLEGAL_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class EffortFocusError(ValueError):
    """A binding is malformed, unsafe, or incompatible with the effort."""


@dataclass(frozen=True)
class ActiveEffort:
    """One worktree-local pointer to a canonical effort and declared slice."""

    path: str
    participant: str
    slice: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "participant": self.participant,
            "slice": self.slice,
        }


@dataclass(frozen=True)
class EffortInspection:
    """Current state derived from an active-effort pointer."""

    ref: ActiveEffort
    state: str
    reason: str | None = None
    status: str | None = None
    slug: str | None = None
    summary: str | None = None

    @property
    def active(self) -> bool:
        return self.state == "open"

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            **self.ref.to_dict(),
            "state": self.state,
            "active": self.active,
        }
        if self.status:
            data["status"] = self.status
        if self.slug:
            data["slug"] = self.slug
        if self.summary:
            data["summary"] = self.summary
        if self.reason:
            data["reason"] = self.reason
        return data


def _clean_label(value: str, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise EffortFocusError(f"{field} must be text")
    clean = _SPACE_RE.sub(" ", _ILLEGAL_CTRL_RE.sub("", value)).strip()
    if required and not clean:
        raise EffortFocusError(f"{field} is required")
    if len(clean) > MAX_EFFORT_LABEL_CHARS:
        raise EffortFocusError(
            f"{field} exceeds {MAX_EFFORT_LABEL_CHARS} characters"
        )
    return clean


def normalize_relative_path(value: str) -> str:
    """Return a canonical repository-relative POSIX path."""
    if not isinstance(value, str):
        raise EffortFocusError("effort path must be text")
    clean = value.strip()
    if not clean or len(clean) > MAX_EFFORT_PATH_CHARS:
        raise EffortFocusError("effort path is empty or too long")
    if "\\" in clean:
        raise EffortFocusError("effort path must use repository-relative '/' separators")
    pure = PurePosixPath(clean)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise EffortFocusError("effort path must be contained and repository-relative")
    if pure.parts and ":" in pure.parts[0]:
        raise EffortFocusError("effort path must not contain a drive-qualified root")
    if pure.name != "README.md":
        raise EffortFocusError("effort path must point to the effort README.md")
    return pure.as_posix()


def active_effort_from_mapping(raw: object) -> ActiveEffort | None:
    """Parse the strict record shape; malformed pointers degrade to absent."""
    if not isinstance(raw, dict) or set(raw) != {"path", "participant", "slice"}:
        return None
    try:
        return ActiveEffort(
            path=normalize_relative_path(raw["path"]),
            participant=_clean_label(raw["participant"], "participant"),
            slice=_clean_label(raw["slice"], "slice"),
        )
    except EffortFocusError:
        return None


def make_active_effort(path: str, participant: str, slice_name: str) -> ActiveEffort:
    return ActiveEffort(
        path=normalize_relative_path(path),
        participant=_clean_label(participant, "participant"),
        slice=_clean_label(slice_name, "slice"),
    )


def normalize_label(value: str, field: str) -> str:
    """Normalize one user-facing binding/release label."""
    return _clean_label(value, field)


def repository_root(worktree_path: str) -> Path:
    """Resolve and verify the authoritative Git root for a tracked worktree."""
    try:
        tracked = Path(worktree_path).resolve(strict=True)
        result = subprocess.run(
            ["git", "-C", str(tracked), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
            env=git_ops.repository_identity_env(),
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise EffortFocusError("tracked worktree is not an available Git checkout")
        root = Path(result.stdout.strip()).resolve(strict=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EffortFocusError("could not verify the tracked worktree Git root") from exc
    if root != tracked:
        raise EffortFocusError("tracked worktree path is not the authoritative Git root")
    return root


def _is_reparse(info: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def resolve_effort_path(repo_root: Path, relative_path: str) -> Path:
    """Resolve a regular effort file without traversing links/reparse points."""
    relative = normalize_relative_path(relative_path)
    try:
        root = repo_root.resolve(strict=True)
    except OSError as exc:
        raise EffortFocusError("repository root is unavailable") from exc

    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise EffortFocusError("effort path does not exist") from exc
        if _is_reparse(info):
            raise EffortFocusError("effort path traverses a link or reparse point")

    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise EffortFocusError("effort path escapes the repository") from exc
    if not resolved.is_file():
        raise EffortFocusError("effort path is not a regular file")
    return resolved


def _read_descriptor(descriptor: int) -> str:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise EffortFocusError("effort path is not a regular file")
    if info.st_size > MAX_EFFORT_FILE_BYTES:
        raise EffortFocusError("effort README exceeds the supported size")
    chunks: list[bytes] = []
    remaining = MAX_EFFORT_FILE_BYTES + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > MAX_EFFORT_FILE_BYTES:
        raise EffortFocusError("effort README exceeds the supported size")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EffortFocusError("effort README is not valid UTF-8") from exc


def _read_effort(repo_root: Path, relative_path: str) -> str:
    """Read one effort through no-follow handles where the platform supports it."""
    relative = normalize_relative_path(relative_path)
    if os.name != "nt" and hasattr(os, "O_NOFOLLOW"):
        descriptors: list[int] = []
        try:
            root = repo_root.resolve(strict=True)
            directory_flags = (
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | os.O_NOFOLLOW
            )
            descriptors.append(os.open(root, directory_flags))
            parts = PurePosixPath(relative).parts
            for part in parts[:-1]:
                descriptors.append(
                    os.open(part, directory_flags, dir_fd=descriptors[-1])
                )
            file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
            descriptors.append(
                os.open(parts[-1], file_flags, dir_fd=descriptors[-1])
            )
            return _read_descriptor(descriptors[-1])
        except EffortFocusError:
            raise
        except OSError as exc:
            raise EffortFocusError("effort README could not be opened safely") from exc
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    path = resolve_effort_path(repo_root, relative)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise EffortFocusError("effort README could not be opened safely") from exc
    try:
        if os.name == "nt":
            import ctypes
            import msvcrt

            get_final_path = ctypes.windll.kernel32.GetFinalPathNameByHandleW
            get_final_path.argtypes = (
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_uint32,
            )
            get_final_path.restype = ctypes.c_uint32
            handle = msvcrt.get_osfhandle(descriptor)
            size = get_final_path(ctypes.c_void_p(handle), None, 0, 0)
            if not size:
                raise EffortFocusError("could not verify the opened effort path")
            buffer = ctypes.create_unicode_buffer(size)
            if not get_final_path(ctypes.c_void_p(handle), buffer, size, 0):
                raise EffortFocusError("could not verify the opened effort path")
            final_name = buffer.value
            if final_name.startswith("\\\\?\\UNC\\"):
                final_name = "\\\\" + final_name[8:]
            elif final_name.startswith("\\\\?\\"):
                final_name = final_name[4:]
            final_path = Path(final_name).resolve(strict=True)
            root = repo_root.resolve(strict=True)
            final_path.relative_to(root)
            if final_path != path:
                raise EffortFocusError(
                    "opened effort path changed during reparse validation"
                )
            resolve_effort_path(repo_root, relative)
        return _read_descriptor(descriptor)
    except EffortFocusError:
        raise
    except OSError as exc:
        raise EffortFocusError("effort README could not be read") from exc
    finally:
        os.close(descriptor)


def _headers(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for match in _HEADER_RE.finditer(text):
        name = match.group("name").lower()
        if name in headers:
            raise EffortFocusError(f"effort README declares {name} more than once")
        headers[name] = match.group("value").strip().strip("`")
    return headers


def _section(text: str, name: str) -> str:
    matches = list(_HEADING_RE.finditer(text))
    bodies: list[str] = []
    for index, match in enumerate(matches):
        if match.group(1).strip().casefold() != name.casefold():
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        bodies.append(text[match.end():end].strip())
    if len(bodies) > 1:
        raise EffortFocusError(f"effort README declares {name} more than once")
    return bodies[0] if bodies else ""


def _sections(text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(text))
    return [
        (
            match.group(1).strip(),
            text[
                match.end():
                matches[index + 1].start() if index + 1 < len(matches) else len(text)
            ].strip(),
        )
        for index, match in enumerate(matches)
    ]


def _declares(section: str, value: str) -> bool:
    expected = _SPACE_RE.sub(" ", value).strip().casefold()
    for line in section.splitlines():
        stripped = line.strip()
        candidates: list[str] = []
        if stripped.startswith("|") and stripped.endswith("|"):
            candidates.extend(cell.strip() for cell in stripped.strip("|").split("|"))
        heading = re.match(r"^#{3,6}\s+(.+?)\s*$", stripped)
        if heading:
            candidates.append(heading.group(1))
        for candidate in candidates:
            normalized = _SPACE_RE.sub(" ", candidate).strip(" `*_").casefold()
            if normalized == expected:
                return True
    return False


def _normalized_status(status: str) -> str:
    without_comment = _HTML_COMMENT_RE.sub("", status).strip()
    return re.split(r"[;(]", without_comment, maxsplit=1)[0].strip().casefold()


def _declares_participant(text: str, value: str) -> bool:
    excluded = {
        "request",
        "guiding intent",
        "context",
        "plan",
        "validation plan",
        "success criteria",
        "blockers",
        "journal",
        "references",
    }
    return any(
        heading.casefold() not in excluded and _declares(body, value)
        for heading, body in _sections(text)
    )


def inspect_effort(repo_root: Path, ref: ActiveEffort) -> EffortInspection:
    """Inspect a pointer without raising; invalid state is explicit and fail-open."""
    try:
        text = _read_effort(repo_root, ref.path)
        headers = _headers(text)
        slug = _clean_label(headers.get("slug", ""), "effort slug")
        status = _clean_label(headers.get("status", ""), "effort status")
        if not _section(text, "Plan") or not _section(text, "Validation Plan"):
            raise EffortFocusError("effort README is missing Plan or Validation Plan")
        normalized_status = _normalized_status(status)
        if normalized_status not in _ALLOWED_STATUSES:
            raise EffortFocusError(f"effort status is not recognized: {status!r}")
        state = "closed" if normalized_status in _CLOSED_STATUSES else "open"
        summary = _clean_label(
            f"Effort {slug}: {ref.slice}", "effort summary"
        )
        return EffortInspection(
            ref=ref,
            state=state,
            status=status,
            slug=slug,
            summary=summary,
        )
    except EffortFocusError as exc:
        return EffortInspection(ref=ref, state="stale", reason=str(exc))


def validate_binding(repo_root: Path, ref: ActiveEffort) -> EffortInspection:
    """Validate an open effort and its declared participant/slice."""
    inspection = inspect_effort(repo_root, ref)
    if inspection.state == "stale":
        raise EffortFocusError(inspection.reason or "effort pointer is stale")
    if not inspection.active:
        raise EffortFocusError(
            f"effort status is {inspection.status!r}; only open efforts can be bound"
        )
    text = _read_effort(repo_root, ref.path)
    coordination = _section(text, "Coordination")
    plan = _section(text, "Plan")
    if not _declares_participant(text, ref.participant):
        raise EffortFocusError(
            "participant is not declared in the effort participant/coordination section"
        )
    if not _declares(f"{plan}\n{coordination}", ref.slice):
        raise EffortFocusError("slice is not declared in the effort Plan/Coordination")
    parts = PurePosixPath(ref.path).parts
    if parts[:2] != ("efforts", "active") or len(parts) not in (4, 5):
        raise EffortFocusError("open effort path must live under efforts/active")
    return inspection


def duplicate_binding(
    records: list[object],
    current_worktree_id: str,
    current_repo: str,
    ref: ActiveEffort,
) -> str | None:
    """Return a same-repository worktree already owning this effort slice."""
    key = (ref.path.casefold(), ref.slice.casefold())
    for record in records:
        if getattr(record, "worktree_id", None) == current_worktree_id:
            continue
        if getattr(record, "repo", None) != current_repo:
            continue
        other = getattr(record, "active_effort", None)
        if other is not None and (
            other.path.casefold(), other.slice.casefold()
        ) == key:
            return str(getattr(record, "worktree_id", "unknown"))
    return None


def archived_effort_path(repo_root: Path, ref: ActiveEffort) -> Path | None:
    """Find one archived copy of a now-moved effort by its bound slug."""
    parts = PurePosixPath(ref.path).parts
    if len(parts) == 4:
        slug = parts[2]
        patterns = (
            f"efforts/[0-9][0-9][0-9][0-9]/*/* {slug}/README.md",
        )
    elif len(parts) == 5:
        repo_name, slug = parts[2:4]
        patterns = (
            f"efforts/[0-9][0-9][0-9][0-9]/{repo_name}/*/* {slug}/README.md",
        )
    else:
        return None
    matches = sorted(
        {candidate for pattern in patterns for candidate in repo_root.glob(pattern)}
    )
    regular: list[Path] = []
    for candidate in matches:
        try:
            relative = candidate.relative_to(repo_root).as_posix()
            candidate_parts = PurePosixPath(relative).parts
            if len(parts) == 4:
                if len(candidate_parts) != 5:
                    continue
                _, year, month, dated_slug, readme = candidate_parts
            else:
                if len(candidate_parts) != 6 or candidate_parts[2] != parts[2]:
                    continue
                _, year, _repo, month, dated_slug, readme = candidate_parts
            day_text, separator, archived_name = dated_slug.partition(" ")
            if (
                readme != "README.md"
                or not separator
                or archived_name != slug
                or len(year) != 4
                or len(month) != 2
                or len(day_text) != 2
            ):
                continue
            date(int(year), int(month), int(day_text))
            regular.append(resolve_effort_path(repo_root, relative))
        except (EffortFocusError, OSError, ValueError):
            continue
    return regular[0] if len(regular) == 1 else None


def _completion_ready(text: str) -> bool:
    headers = _headers(text)
    status = headers.get("status", "")
    normalized = _normalized_status(status)
    plan = _section(text, "Plan")
    validation = _section(text, "Validation Plan")
    task_states = [
        match.group(1).casefold()
        for section in (plan, validation)
        for match in _TASK_RE.finditer(section)
    ]
    return bool(
        normalized in _CLOSED_STATUSES
        and plan
        and validation
        and all(state == "x" for state in task_states)
    )


def _active_path_is_absent(repo_root: Path, relative_path: str) -> bool:
    current = repo_root
    for part in PurePosixPath(normalize_relative_path(relative_path)).parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        if _is_reparse(info):
            return False
    return False


def completed_or_archived(repo_root: Path, ref: ActiveEffort) -> bool:
    try:
        text = _read_effort(repo_root, ref.path)
    except EffortFocusError:
        if not _active_path_is_absent(repo_root, ref.path):
            return False
    else:
        return _completion_ready(text)

    try:
        archived = archived_effort_path(repo_root, ref)
        if archived is None:
            return False
        archived_relative = archived.relative_to(repo_root).as_posix()
        text = _read_effort(repo_root, archived_relative)
        headers = _headers(text)
        archived_slug = _clean_label(headers.get("slug", ""), "effort slug")
        return archived_slug.casefold() == (
            PurePosixPath(ref.path).parent.name.casefold()
        ) and _completion_ready(text)
    except EffortFocusError:
        return False


def orientation(repo_root: Path, ref: ActiveEffort | None) -> str:
    """Return a bounded record-first orientation pointer for session start."""
    if ref is None:
        return ""
    inspection = inspect_effort(repo_root, ref)
    if not inspection.active:
        return ""
    text = (
        f"Active effort: `{ref.path}`; participant `{ref.participant}`; "
        f"slice `{ref.slice}`. Load that effort first; it remains the worktree "
        "objective and completion gate."
    )
    if len(text) <= MAX_EFFORT_ORIENTATION_CHARS:
        return text
    return ""
