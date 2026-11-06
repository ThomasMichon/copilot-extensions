"""Behavior tests for the read-only effort readback digest."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "skills" / "planning-efforts" / "scripts" / "effort_digest.py"

_spec = importlib.util.spec_from_file_location("effort_digest", SCRIPT)
digest_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = digest_mod
_spec.loader.exec_module(digest_mod)

README = """# Sample Effort

- **Status:** Active

## Plan

### Phase 1 — Build
- [x] Done thing
- [ ] Open build item

```markdown
## Not a heading
- [ ] Not an item inside a fence
```

### Phase 2 — Ship
- [ ] Open ship item

## Validation Plan

- [ ] Open check
- [x] Closed check

## Journal

### 2026-11-01 — Kickoff
- started

### 2026-11-03 — Progress
- moved
"""


def _write(folder: Path, text: str = README) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    readme = folder / "README.md"
    readme.write_text(text, encoding="utf-8")
    return readme


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_open_items_only_from_plan_and_validation(tmp_path: Path) -> None:
    digest = digest_mod.build_digest(_write(tmp_path / "effort"))

    assert [(i["section"], i["text"]) for i in digest["open_items"]] == [
        ("Plan > Phase 1 — Build", "Open build item"),
        ("Plan > Phase 2 — Ship", "Open ship item"),
        ("Validation Plan", "Open check"),
    ]
    titles = [entry["title"] for entry in digest["outline"]]
    assert "Not a heading" not in titles
    assert "2026-11-01 — Kickoff" not in titles  # journal entries stay out of the outline


def test_inline_journal_latest_first(tmp_path: Path) -> None:
    digest = digest_mod.build_digest(_write(tmp_path / "effort"), journal_limit=1)

    journal = digest["journal"]
    assert journal["entries_total"] == 2
    assert [e["title"] for e in journal["latest"]] == ["2026-11-03 — Progress"]


def test_mixed_order_journal_sorted_by_date(tmp_path: Path) -> None:
    text = (
        "# E\n\n## Journal\n\n"
        "### 2026-10-30 — Prepended\n\n### 2026-11-03 — Late A\n\n"
        "### 2026-11-03 — Late B\n\n### 2026-11-01 — Middle\n\n"
        "### 2026-10-28 — Oldest\n"
    )
    digest = digest_mod.build_digest(_write(tmp_path / "effort", text), journal_limit=3)

    # Newest-first document order is kept for same-date entries.
    assert [e["title"] for e in digest["journal"]["latest"]] == [
        "2026-11-03 — Late A",
        "2026-11-03 — Late B",
        "2026-11-01 — Middle",
    ]


def test_journal_md_sidecar_entries_located(tmp_path: Path) -> None:
    folder = tmp_path / "effort"
    _write(folder, "# E\n\n## Journal\n\nSee [journal.md](journal.md).\n\n### 2026-07-13 — Inline\n")
    (folder / "journal.md").write_text(
        "# Journal - e\n\n## 2026-09-16 — Sidecar newest\n\n### detail\n\n## 2026-08-01 — Older\n",
        encoding="utf-8",
    )

    journal = digest_mod.build_digest(folder, journal_limit=2)["journal"]

    assert journal["entries_total"] == 3
    assert [(e["file"], e["line"], e["title"]) for e in journal["latest"]] == [
        ("journal.md", 3, "2026-09-16 — Sidecar newest"),
        ("journal.md", 7, "2026-08-01 — Older"),
    ]


def test_split_journal_files_latest_first(tmp_path: Path) -> None:
    folder = tmp_path / "effort"
    _write(folder, "# E\n\n## Plan\n- [ ] x\n\n## Journal\n\n- [11.02](journal/2026/11.02 b.md)\n")
    for name in ("10.30 a.md", "11.02 b.md"):
        path = folder / "journal" / "2026" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("### entry\n", encoding="utf-8")

    journal = digest_mod.build_digest(folder, journal_limit=1)["journal"]

    assert journal["entries_total"] == 0
    assert journal["files_total"] == 2
    assert [e["file"] for e in journal["files_latest"]] == ["journal/2026/11.02 b.md"]


def test_since_reports_only_changed_sections_and_files(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    folder = tmp_path / "efforts" / "active" / "sample"
    readme = _write(folder)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")

    readme.write_text(
        README.replace("- [ ] Open ship item", "- [x] Open ship item")
        + "### 2026-11-04 — Shipped\n- landed\n",
        encoding="utf-8",
    )
    (folder / "detail.md").write_text("new\n", encoding="utf-8")

    since = digest_mod.build_digest(folder, since="HEAD")["since"]

    assert {(e["state"], e["section"]) for e in since["sections"]} == {
        ("changed", "Plan > Phase 2 — Ship"),
        ("added", "Journal > 2026-11-04 — Shipped"),
    }
    assert {e["file"] for e in since["files"]} == {
        "efforts/active/sample/README.md",
        "efforts/active/sample/detail.md",
    }
    assert since["removed_sections"] == []


def test_since_refuses_outside_git(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = tmp_path / "effort"
    _write(folder)

    assert digest_mod.main([str(folder), "--since", "HEAD"]) == 2
    assert "needs a git checkout" in capsys.readouterr().err


def test_text_render_is_compact(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    folder = tmp_path / "effort"
    _write(folder)

    assert digest_mod.main([str(folder)]) == 0
    out = capsys.readouterr().out
    assert "[Plan > Phase 1 — Build]\nL" in out
    assert "Done thing" not in out


def test_longer_fence_quotes_inner_fence(tmp_path: Path) -> None:
    text = (
        "# E\n\n## Plan\n\n````markdown\n```\n## Sample heading\n- [ ] sample item\n```\n````\n\n"
        "- [ ] Real item after example\n"
    )
    digest = digest_mod.build_digest(_write(tmp_path / "effort", text))

    assert [i["text"] for i in digest["open_items"]] == ["Real item after example"]
    assert "Sample heading" not in [entry["title"] for entry in digest["outline"]]


DUPLICATES = "# E\n\n## Plan\n\n### Follow-up\n- [ ] first\n\n### Follow-up\n- [ ] second\n"


def test_since_keeps_duplicate_headings_distinct(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    folder = tmp_path / "effort"
    readme = _write(folder, DUPLICATES)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")

    unchanged = digest_mod.build_digest(folder, since="HEAD")["since"]
    assert unchanged["sections"] == [] and unchanged["removed_sections"] == []

    readme.write_text("# E\n\n## Plan\n\n### Follow-up\n- [ ] second\n", encoding="utf-8")
    deleted = digest_mod.build_digest(folder, since="HEAD")["since"]

    assert deleted["sections"] == []
    assert deleted["removed_sections"] == ["E > Plan > Follow-up"]


def test_since_ignores_inherited_repository_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    _git(other, "init", "-q")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    folder = repo / "effort"
    readme = _write(folder)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    readme.write_text(README + "### 2026-11-05 — More\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    since = digest_mod.build_digest(folder, since="HEAD")["since"]

    assert [e["file"] for e in since["files"]] == ["effort/README.md"]


def test_since_reports_non_ascii_and_renamed_paths(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    folder = tmp_path / "effort"
    _write(folder)
    (folder / "old.md").write_text("same content\n" * 20, encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "mv", "effort/old.md", "effort/renamed-é.md")
    (folder / "notes-é.md").write_text("new\n", encoding="utf-8")

    files = digest_mod.build_digest(folder, since="HEAD")["since"]["files"]

    assert {(e["status"][:1], e["file"]) for e in files} == {
        ("R", "effort/renamed-é.md"),
        ("?", "effort/notes-é.md"),
    }


@pytest.mark.parametrize("as_json", [False, True])
def test_non_utf8_stdout_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_json: bool
) -> None:
    folder = tmp_path / "effort"
    _write(folder, "# E\n\n## Plan\n\n### Phase 3 … ✅ landed\n- [ ] item ✅\n")
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)

    assert digest_mod.main([str(folder)] + (["--json"] if as_json else [])) == 0
    stream.flush()
    out = buffer.getvalue().decode("cp1252")
    if as_json:
        assert json.loads(out)["open_items"][0]["text"] == "item ✅"
    else:
        assert "item \\u2705" in out
