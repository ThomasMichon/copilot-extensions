"""Small default-tier controls for the explicit historical qualification launcher."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tarfile

import pytest

import qualify_http46_capture as qualification

pytestmark = [pytest.mark.portfolio_tier("T1"), pytest.mark.effect("filesystem")]


def archive(member: tarfile.TarInfo, content: bytes = b"") -> bytes:
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w") as stream:
        member.size = len(content)
        stream.addfile(member, io.BytesIO(content))
    return result.getvalue()


@pytest.mark.parametrize(
    "name",
    ["../outside", "/absolute", "nested/../../escape", "C:/absolute", r"..\escape"],
)
def test_historical_archive_refuses_escape_before_writing(tmp_path: Path, name: str) -> None:
    destination = tmp_path / "archive"
    destination.mkdir()
    with pytest.raises(ValueError, match="unsafe historical archive"):
        qualification.extract(archive(tarfile.TarInfo(name), b"refused"), destination)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_historical_archive_refuses_aliases_and_special_files(tmp_path: Path, kind: bytes) -> None:
    member = tarfile.TarInfo("linked")
    member.type, member.linkname = kind, "target"
    destination = tmp_path / "archive"
    destination.mkdir()
    with pytest.raises(ValueError, match="unsafe historical archive"):
        qualification.extract(archive(member), destination)
    assert list(destination.iterdir()) == []


def test_historical_archive_preserves_actual_file_bytes(tmp_path: Path) -> None:
    qualification.extract(archive(tarfile.TarInfo("src/file.py"), b"actual source\n"), tmp_path)
    assert (tmp_path / "src/file.py").read_bytes() == b"actual source\n"


@pytest.mark.parametrize("source", ["HEAD", "a" * 39, "../source", "A" * 40])
def test_historical_source_requires_full_actual_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    source: str,
) -> None:
    def unexpected_git(*args: object) -> bytes:
        pytest.fail("invalid input reached Git")

    monkeypatch.setattr(qualification, "git", unexpected_git)
    with pytest.raises(ValueError, match="full historical commit"):
        qualification.prepare(source, tmp_path)


def positive_receipt() -> dict:
    return {
        "historical_source": "a" * 40,
        "composed_fixture_not_original_capture": True,
        "cases": [
            {
                "representation": kind,
                "write": True,
                "no_write": True,
                "source": "b" * 40,
                "retained_chunks": count,
            }
            for kind, count in (("inline", 0), ("chunked", 3))
        ],
    }


def test_qualification_requires_both_positive_cases() -> None:
    receipt = positive_receipt()
    text = "HTTP46_CAPTURE_QUALIFIED " + json.dumps(receipt)
    assert qualification.qualification_receipt(text) == receipt
    for output in ("1 passed", text + "\n" + text):
        with pytest.raises(ValueError, match="exactly one positive receipt"):
            qualification.qualification_receipt(output)


@pytest.mark.parametrize("failure", ["missing", "skip", "no-chunks", "synthetic-sha"])
def test_qualification_rejects_incomplete_or_success_shaped_receipts(failure: str) -> None:
    receipt = positive_receipt()
    if failure == "missing":
        receipt["cases"].pop()
    elif failure == "skip":
        receipt["cases"][1]["no_write"] = False
    elif failure == "no-chunks":
        receipt["cases"][1]["retained_chunks"] = 0
    else:
        receipt["cases"][1]["source"] = "invented"
    with pytest.raises(ValueError, match="both actual representation cases"):
        qualification.qualification_receipt("HTTP46_CAPTURE_QUALIFIED " + json.dumps(receipt))
