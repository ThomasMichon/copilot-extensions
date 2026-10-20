"""Tests for opt-in session-sync protection: secret redaction
(``sync.redact_secrets``), session file projection (``sync.session_files``)
and whole-file deferral of incomplete or malformed protected files."""

from __future__ import annotations

import copy
import io
import json
import random
import tracemalloc
from pathlib import Path

import pytest

from agent_logger import secret_patterns
from agent_logger.config import Config, load_config
from agent_logger.sync import engine, protection, protection_engine, rescue, rescue_projection
from agent_logger.sync.change_tracker import ChangeTracker, resolve_db_path
from agent_logger.sync.meta import read_sync_meta
from agent_logger.sync.rescue import RescueSourceError
from agent_logger.sync.targets import filesystem, protected_publication
from agent_logger.sync.targets.filesystem import LocalTarget

SID = "abc-123"
MARKER = secret_patterns.REDACTION_MARKER


# Secret-shaped values are assembled at runtime so no literal token appears in
# the source; assertions only test presence or absence, never print values.
def _gh() -> str:
    return "gh" + "p_" + "A" * 36


def _aws() -> str:
    return "AK" + "IA" + "ABCDEFGHIJKLMNOP"


def _bearer() -> str:
    return "Bear" + "er " + "x" * 20


def _jwt() -> str:
    return "ey" + "J" + "a" * 20 + ".ey" + "J" + "b" * 20


@pytest.fixture(autouse=True)
def _fixed_machine(monkeypatch):
    monkeypatch.setattr(engine, "_machine", lambda _cfg: "machine")


def _make_source(root: Path) -> Path:
    sess = root / "copilot" / "session-state" / SID
    sess.mkdir(parents=True)
    (sess / "events.jsonl").write_bytes(b'{"ts": 1}\n')
    (sess / "workspace.yaml").write_bytes(b"id: abc-123\n")
    return root / "copilot"


def _cfg(home, src, dest, *, redact=None, files=None, target=None, compact=None,
         onedrive_root=None):
    data = copy.deepcopy(load_config(home=home).as_dict())
    sync = data["sync"]
    sync["usage_capture"] = False
    sync["source"] = str(src)
    sync["targets"]["local"]["path"] = str(dest)
    if onedrive_root is not None:
        sync["targets"]["onedrive"]["root"] = str(onedrive_root)
    if redact is not None:
        sync["redact_secrets"] = redact
    if files is not None:
        sync["session_files"] = files
    if target is not None:
        sync["target"] = target
    if compact is not None:
        sync.setdefault("compact", {})["enabled"] = compact
    return Config(data, home)


def _tracker(cfg) -> ChangeTracker:
    return ChangeTracker(resolve_db_path(cfg.sync_change_tracking["db_path"], cfg.home))


def _dest_session(dest: Path) -> Path:
    return dest / "machine" / "session-state" / SID


def _events(src: Path) -> Path:
    return src / "session-state" / SID / "events.jsonl"


def _jsonl(data: bytes):
    sink = io.BytesIO()
    reader = protection.BoundedReader(io.BytesIO(data), len(data))
    outcome = protection.transform_jsonl(reader, sink.write)
    return outcome, sink.getvalue()


def _file(member: str, data: bytes):
    sink = io.BytesIO()
    reader = protection.BoundedReader(io.BytesIO(data), len(data))
    outcome = protection.transform_file(member, reader, len(data), sink.write)
    return outcome, sink.getvalue()


# --- JSONL transform: whole-file deferral (R16) ------------------------------


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b'{"a": 1}\n{"b": ', protection.REASON_INCOMPLETE_RECORD),
        (b'{"a": 1}\n12', protection.REASON_INCOMPLETE_RECORD),
        (b'{"a": 1}\n{bad}\n{"c": 3}\n', protection.REASON_MALFORMED_RECORD),
        (b'{"a": 1}\n{"b": \n', protection.REASON_MALFORMED_RECORD),
        (b'{"a": "\xff"}\n', protection.REASON_MALFORMED_RECORD),
        (b'{"a": NaN}\n', protection.REASON_MALFORMED_RECORD),
        (b'{"a": Infinity}\n{"b": 2}\n', protection.REASON_MALFORMED_RECORD),
    ],
    ids=[
        "incomplete-final-fragment",
        "unterminated-number",
        "interior-malformed",
        "terminated-malformed-final",
        "invalid-utf8",
        "nan",
        "infinity",
    ],
)
def test_jsonl_defers_whole_file_on_incomplete_or_malformed_record(data, reason):
    outcome, _ = _jsonl(data)
    assert outcome.state == protection.DEFERRED
    assert outcome.reason == reason
    assert not outcome.published


def test_jsonl_publishes_unterminated_complete_object():
    outcome, out = _jsonl(b'{"a": 1}\n{"b": 2}')
    assert outcome.published
    assert out == b'{"a": 1}\n{"b": 2}'


def test_jsonl_preserves_bom_blank_lines_and_crlf_byte_for_byte():
    data = b'\xef\xbb\xbf{"a": 1}\r\n\r\n\n{"b": [1, 2]}\r\n'
    outcome, out = _jsonl(data)
    assert outcome.published
    assert out == data
    assert outcome.redacted == 0
    assert outcome.records == 2


# --- JSONL transform: secret redaction (R6) ----------------------------------


def test_jsonl_redaction_keeps_every_line_valid_json():
    gh = _gh()
    records = [
        {"data": {"nested": [{"deep": "token " + gh}], "n": 1}},
        {"msg": "h\u00e9llo \u2713 " + _aws()},
        {"auth": "Authorization: " + _bearer()},
        {"jwt": _jwt()},
        {"cmd": "login --pass" + "word=hunter2xyz"},
        {"access_token": "plain-value", "api_key": "", "secret": MARKER},
        {"clean": "nothing to see", "n": [1, 2.5, None, True]},
    ]
    data = b"".join(json.dumps(r, ensure_ascii=False).encode() + b"\n" for r in records)
    outcome, out = _jsonl(data)
    assert outcome.published
    lines = out.split(b"\n")
    assert lines[-1] == b""
    parsed = [json.loads(line) for line in lines[:-1]]
    assert len(parsed) == len(records)
    for value in (gh, _aws(), "x" * 20, _jwt(), "hunter2xyz", "plain-value"):
        assert value.encode() not in out
    assert parsed[0]["data"]["nested"][0]["deep"] == "token " + MARKER
    assert parsed[0]["data"]["n"] == 1
    assert parsed[1]["msg"].startswith("h\u00e9llo \u2713 ")
    assert MARKER in parsed[2]["auth"]
    assert ("pass" + "word=" + MARKER) in parsed[4]["cmd"]
    assert parsed[5] == {"access_token": MARKER, "api_key": "", "secret": MARKER}
    assert lines[6] == data.split(b"\n")[6]
    assert outcome.redacted >= 6
    assert outcome.records == len(records)


def test_jsonl_redacts_unicode_escaped_values_and_keys():
    gh = _gh()
    data = (
        '{"a": "\\u0067' + gh[1:] + '"}\n'
        + '{"\\u0061pi_key": "abc"}\n'
    ).encode()
    outcome, out = _jsonl(data)
    assert outcome.published
    first, second = out.split(b"\n")[:2]
    assert json.loads(first) == {"a": MARKER}
    assert json.loads(second) == {"api_key": MARKER}
    assert gh[1:].encode() not in out
    assert b"abc" not in out


def test_jsonl_untouched_lines_are_byte_identical():
    gh = _gh()
    clean = b'{"z":  1,"a" :"\\u00e9\\n"}'
    data = clean + b"\n" + ('{"k": "' + gh + '"}\n').encode() + clean + b"\n"
    outcome, out = _jsonl(data)
    assert outcome.published
    assert outcome.redacted == 1
    lines = out.split(b"\n")
    assert lines[0] == clean
    assert lines[2] == clean


def test_redact_json_text_rewrites_only_changed_string_tokens():
    gh = _gh()
    text, count = protection.redact_json_text('{"b":  "x",   "a": "' + gh + '"}')
    assert count == 1
    assert text == '{"b":  "x",   "a": "' + MARKER + '"}'
    same, none = protection.redact_json_text('{"b": "x"}')
    assert (same, none) == ('{"b": "x"}', 0)


def test_redact_json_text_lone_surrogate_falls_back_to_ascii_escapes():
    gh = _gh()
    text, count = protection.redact_json_text('{"a": "\\ud800 ' + gh + '"}')
    assert count == 1
    assert protection.is_valid_json(text)
    assert gh not in text
    assert text.isascii()
    assert json.loads(text)["a"] == "\ud800 " + MARKER


# --- settings and validation -------------------------------------------------


def test_defaults_leave_protection_disabled(tmp_path):
    cfg = load_config(home=tmp_path / "home")
    settings = cfg.sync_protection
    assert settings == protection.ProtectionSettings()
    assert settings.enabled is False
    assert settings.redact_secrets is False
    assert settings.session_files == "all"


@pytest.mark.parametrize("value", ["yes", 1, 0, "true", [True]])
def test_config_rejects_non_boolean_redact_secrets(tmp_path, value):
    with pytest.raises(ValueError, match="sync.redact_secrets must be true or false"):
        _cfg(tmp_path / "home", tmp_path / "src", tmp_path / "dest", redact=value)


def test_config_treats_explicit_null_settings_as_defaults(tmp_path):
    home = tmp_path / "home"
    data = copy.deepcopy(load_config(home=home).as_dict())
    data["sync"]["redact_secrets"] = None
    data["sync"]["session_files"] = None
    assert Config(data, home).sync_protection == protection.ProtectionSettings()


@pytest.mark.parametrize("value", ["some", "", 3, "minimal"])
def test_config_rejects_unknown_session_files(tmp_path, value):
    with pytest.raises(ValueError, match="sync.session_files must be one of: all, essential"):
        _cfg(tmp_path / "home", tmp_path / "src", tmp_path / "dest", files=value)


def test_config_normalizes_session_files(tmp_path):
    cfg = _cfg(tmp_path / "home", tmp_path / "src", tmp_path / "dest", files=" Essential ")
    assert cfg.sync_protection.session_files == "essential"
    assert cfg.sync_protection.enabled is True
    assert cfg.sync_protection.essential_only is True


def test_settings_fingerprints_and_description():
    off = protection.ProtectionSettings()
    assert off.fingerprint() == "files=all"
    assert off.transform_fingerprint() == ""
    assert off.describe() == "redact_secrets=off, session_files=all"
    on = protection.ProtectionSettings(redact_secrets=True, session_files="essential")
    assert on.fingerprint().startswith("files=essential;redact=v")
    assert on.transform_fingerprint() != ""
    assert on.describe() == "redact_secrets=on, session_files=essential"
    assert off.fingerprint() != protection.ProtectionSettings(session_files="essential").fingerprint()


def test_essential_projection_membership():
    essential = protection.ProtectionSettings(session_files="essential")
    assert set(protection.ESSENTIAL_SESSION_FILES) == set(protection.ALLOWED_MEMBERS) | {
        "review-annotations.json"
    }
    for member in protection.ESSENTIAL_SESSION_FILES:
        assert essential.member_included(member)
        assert essential.path_included(f"session-state/{SID}/{member}")
    for member in ("files/x.txt", "rewind-file-snapshots/a.json", "checkpoints/other.md",
                   "session.db", "instructions/a.md", "new-kind.dat"):
        assert not essential.member_included(member)
        assert not essential.path_included(f"session-state/{SID}/{member}")
    assert not essential.path_included("session-store.db")
    everything = protection.ProtectionSettings()
    assert everything.member_included("new-kind.dat")
    assert everything.path_included(f"session-state/{SID}/files/x.txt")


# --- binary and other file kinds (R6) ----------------------------------------


def test_scan_stream_detects_shape_split_across_reads():
    data = b"x" * 50 + _gh().encode() + b"y" * 100
    sink = io.BytesIO()
    hit = secret_patterns.scan_stream_for_secret(
        io.BytesIO(data), sink.write, chunk_size=64, overlap=48
    )
    assert hit is True
    # Only bytes that can no longer belong to a match were released.
    assert sink.getvalue() == b"x" * 16


def test_scan_stream_passes_clean_bytes_through():
    data = (b"\x00\x01clean \xfe\xff" * 37) + b"tail"
    sink = io.BytesIO()
    hit = secret_patterns.scan_stream_for_secret(
        io.BytesIO(data), sink.write, chunk_size=64, overlap=48
    )
    assert hit is False
    assert sink.getvalue() == data


def test_binary_file_with_token_shape_is_withheld():
    outcome, _ = _file("files/blob.bin", b"\x00\x01" + _gh().encode() + b"\x00")
    assert outcome.state == protection.WITHHELD
    assert outcome.reason == protection.REASON_BINARY_SECRET


def test_clean_binary_file_is_copied_unchanged():
    data = b"\x00\x01\x02\xfe\xff" * 100
    outcome, out = _file("files/blob.bin", data)
    assert outcome.published
    assert not outcome.changed
    assert out == data


@pytest.mark.parametrize(
    ("member", "data"),
    [
        ("session.db", b"anything"),
        ("files/cache.db-wal", b"anything"),
        ("files/store.SQLITE3", b"anything"),
        ("files/blob.bin", b"SQLite format 3\x00" + b"\x00" * 64),
        ("files/state.json", b"SQLite format 3\x00" + b"\x00" * 64),
        ("files/journal.bin", b"\x37\x7f\x06\x82" + b"\x00" * 64),
    ],
    ids=["db-suffix", "wal-suffix", "sqlite3-suffix", "sqlite-magic", "json-sqlite-magic",
         "wal-magic"],
)
def test_database_files_are_withheld(member, data):
    outcome, _ = _file(member, data)
    assert outcome.state == protection.WITHHELD
    assert outcome.reason == protection.REASON_DATABASE


def test_text_file_with_token_is_redacted():
    gh = _gh()
    outcome, out = _file("checkpoints/index.md", ("# notes\nuse " + gh + " here\n").encode())
    assert outcome.published
    assert outcome.changed
    assert outcome.redacted == 1
    assert gh.encode() not in out
    assert out == ("# notes\nuse " + MARKER + " here\n").encode()


def test_json_document_with_token_is_redacted_and_stays_valid():
    gh = _gh()
    outcome, out = _file("context.json", ('{"cwd": "/w", "env": {"GH_TOKEN": "' + gh + '"}}').encode())
    assert outcome.published
    assert outcome.changed
    assert gh.encode() not in out
    assert json.loads(out) == {"cwd": "/w", "env": {"GH_TOKEN": MARKER}}


def test_clean_json_document_is_copied_unchanged():
    data = b'{ "a" : [1, 2.50] }\n'
    outcome, out = _file("origin.json", data)
    assert outcome.published
    assert not outcome.changed
    assert out == data


def test_malformed_json_document_is_deferred():
    outcome, _ = _file("context.json", b'{"cwd": ')
    assert outcome.state == protection.DEFERRED
    assert outcome.reason == protection.REASON_MALFORMED_DOCUMENT


def test_text_larger_than_a_chunk_is_streamed_without_a_size_cap(monkeypatch):
    monkeypatch.setattr(protection, "TEXT_CHUNK", 64)
    clean = b"plain text line\n" * 64
    outcome, out = _file("files/big.log", clean)
    assert outcome.published
    assert not outcome.changed
    assert out == clean
    gh = _gh()
    outcome, out = _file("files/big.log", clean + gh.encode() + b"\n" + clean)
    assert outcome.published
    assert outcome.changed
    assert gh.encode() not in out
    assert out == clean + MARKER.encode() + b"\n" + clean
    outcome, _ = _file("files/big.bin", b"SQLite format 3\x00" + clean)
    assert outcome.state == protection.WITHHELD
    assert outcome.reason == protection.REASON_DATABASE


# --- receipts: metadata only, validated on read ------------------------------


def _entries() -> dict:
    return {
        "events.jsonl": protection.published_entry(
            10, 20, 11, 21, protection.Outcome(records=3, redacted=1)
        ),
        "files/a.bin": protection.unpublished_entry(
            protection.WITHHELD, protection.REASON_BINARY_SECRET, 5, 6,
            protection.PRIOR_COPY_NONE, None,
        ),
    }


def test_receipt_round_trip():
    raw = protection.encode_receipt(SID, "files=all;redact=x", _entries())
    assert raw is not None
    raw.decode("ascii")
    data = json.loads(raw)
    assert set(data) == {"schema", "session_id", "settings", "updated_at", "files"}
    receipt = protection.decode_receipt(raw, SID)
    assert receipt is not None
    assert receipt.settings == "files=all;redact=x"
    assert receipt.files == _entries()


@pytest.mark.parametrize(
    ("key", "value"),
    [("session_id", "other"), ("schema", 2), ("files", []), ("settings", None)],
)
def test_receipt_rejects_foreign_or_invalid_header(key, value):
    data = json.loads(protection.encode_receipt(SID, "s", _entries()))
    data[key] = value
    assert protection.decode_receipt(json.dumps(data).encode(), SID) is None


@pytest.mark.parametrize("raw", [b"not json", b"[1]", b"\xff\xfe"])
def test_receipt_rejects_garbage(raw):
    assert protection.decode_receipt(raw, SID) is None


def test_receipt_size_cap(monkeypatch):
    raw = protection.encode_receipt(SID, "s", _entries())
    monkeypatch.setattr(protection, "MAX_RECEIPT_BYTES", 16)
    assert protection.decode_receipt(raw, SID) is None
    assert protection.encode_receipt(SID, "s", _entries()) is None


def test_receipt_drops_unsafe_members_and_invalid_entries():
    good = _entries()["events.jsonl"]
    deferred = protection.unpublished_entry(
        protection.DEFERRED, protection.REASON_MALFORMED_RECORD, 1, 2,
        protection.PRIOR_COPY_NONE, None,
    )
    files = {"events.jsonl": good, "checkpoints/index.md": deferred}
    for unsafe in ("../x", "/abs", "C:/x", "a\\b", "a//b", "./a", "", "a" * 1025):
        files[unsafe] = good
    for name, mutate in {
        "bool-size": lambda e: e.update(source_size=True),
        "no-dest": lambda e: e.pop("dest_size"),
        "bad-state": lambda e: e.update(state="copied"),
    }.items():
        entry = dict(good)
        mutate(entry)
        files[f"files/{name}"] = entry
    files["files/bad-reason"] = dict(deferred, reason="because")
    files["files/bad-prior"] = dict(deferred, prior_copy="maybe")
    files["files/not-a-dict"] = ["published"]
    raw = json.dumps(
        {"schema": protection.RECEIPT_SCHEMA, "session_id": SID, "settings": "s", "files": files}
    ).encode()
    receipt = protection.decode_receipt(raw, SID)
    assert receipt is not None
    assert set(receipt.files) == {"events.jsonl", "checkpoints/index.md"}


def test_reusable_entry_requires_same_settings_and_source_stat():
    receipt = protection.Receipt(settings="fp", files=_entries())
    entry = protection.reusable_entry(receipt, "fp", "events.jsonl", 10, 20)
    assert entry == _entries()["events.jsonl"]
    assert protection.reusable_entry(None, "fp", "events.jsonl", 10, 20) is None
    assert protection.reusable_entry(receipt, "other", "events.jsonl", 10, 20) is None
    assert protection.reusable_entry(receipt, "fp", "workspace.yaml", 10, 20) is None
    assert protection.reusable_entry(receipt, "fp", "events.jsonl", 11, 20) is None
    assert protection.reusable_entry(receipt, "fp", "events.jsonl", 10, 21) is None


def test_classify_prior_copy():
    published = _entries()["events.jsonl"]
    assert protection.classify_prior_copy(published, None) == protection.PRIOR_COPY_NONE
    assert protection.classify_prior_copy(published, (11, 21)) == protection.PRIOR_COPY_PROTECTED
    assert protection.classify_prior_copy(published, (12, 21)) == protection.PRIOR_COPY_UNPROTECTED
    assert protection.classify_prior_copy(None, (11, 21)) == protection.PRIOR_COPY_UNPROTECTED
    kept = protection.unpublished_entry(
        protection.DEFERRED, protection.REASON_INCOMPLETE_RECORD, 30, 40,
        protection.PRIOR_COPY_PROTECTED, (11, 21),
    )
    assert (kept["dest_size"], kept["dest_mtime_ns"]) == (11, 21)
    assert protection.classify_prior_copy(kept, (11, 21)) == protection.PRIOR_COPY_PROTECTED
    raw_copy = protection.unpublished_entry(
        protection.DEFERRED, protection.REASON_INCOMPLETE_RECORD, 30, 40,
        protection.PRIOR_COPY_UNPROTECTED, (11, 21),
    )
    assert "dest_size" not in raw_copy
    assert protection.classify_prior_copy(raw_copy, (11, 21)) == protection.PRIOR_COPY_UNPROTECTED


def test_summary_is_bounded_and_printable():
    settings = protection.ProtectionSettings(redact_secrets=True, session_files="essential")
    deferred = [{"path": f"s/{i}/events.jsonl", "reason": "malformed-record"} for i in range(15)]
    deferred[0]["path"] = "s/0/events\u2028.jsonl\x1b"
    withheld = [{"path": "x" * 400, "reason": "binary-secret-shape"}]
    summary = protection.summarize(
        settings, published=4, redacted=2, deferred=deferred, withheld=withheld, unselected=3
    )
    assert summary["settings"] == settings.fingerprint()
    assert summary["published_files"] == 4
    assert summary["unselected_files"] == 3
    assert summary["redacted_strings"] == 2
    assert summary["deferred_count"] == 15
    assert len(summary["deferred"]) == protection.MAX_SUMMARY_ENTRIES
    assert summary["deferred"][0]["path"] == "s/0/events?.jsonl?"
    assert summary["withheld_count"] == 1
    assert len(summary["withheld"][0]["path"]) == protection.MAX_SUMMARY_PATH_CHARS


# --- projected change detection (R8) -----------------------------------------


def _grow(path: Path, data: bytes = b"more\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(data)


def test_essential_change_check_ignores_unlisted_files(tmp_path):
    src = _make_source(tmp_path)
    sess = src / "session-state" / SID
    _grow(sess / "files" / "x.txt")
    essential = protection.ProtectionSettings(session_files="essential")
    tracker = ChangeTracker(
        tmp_path / "state.db", member_filter=essential.member_included, include_provenance=False
    )
    everything = ChangeTracker(tmp_path / "all.db")
    tracker.record(src, [SID])
    everything.record(src, [SID])
    assert tracker.changed_sessions(src, [SID]) == set()

    _grow(sess / "files" / "x.txt")
    _grow(sess / "new-kind.dat")
    _grow(src / "provenance" / f"{SID}.json", b"{}")
    assert tracker.changed_sessions(src, [SID]) == set()
    assert everything.changed_sessions(src, [SID]) == {SID}

    _grow(sess / "events.jsonl", b'{"ts": 2}\n')
    assert tracker.changed_sessions(src, [SID]) == {SID}


# --- streaming text redaction matches whole-text redaction --------------------


def _pem_block(terminated: bool = True) -> str:
    kind = "RSA PRIV" + "ATE KEY-----"
    block = "-----BEG" + "IN " + kind + "\nAAAA\nBBBB\n"
    return block + ("-----EN" + "D " + kind + "\n" if terminated else "")


def _fuzz_fragments() -> list[str]:
    return [
        "pass" + "word = hunter2 ", "pass" + "word:", "Authori" + "zation: " + _bearer(),
        "tok" + "en = " + _gh(), _jwt(), _aws(), _pem_block(), _pem_block(False),
        "-----BEG", "IN ", '"', "'", "\\", ":", "=", " ", "  ", "\n", "\r\n", "\t",
        "\u017f", "plain words ", "Bear" + "er", "x" * 20, "{\"key\": ", "}",
    ]


@pytest.mark.parametrize("chunk", [1, 2, 3, 5, 7, 64])
def test_streamed_text_redaction_matches_whole_text(monkeypatch, chunk):
    monkeypatch.setattr(protection, "TEXT_CHUNK", chunk)
    fragments = _fuzz_fragments()
    rng = random.Random(1000 + chunk)
    for _ in range(60):
        text = "".join(rng.choice(fragments) for _ in range(rng.randint(1, 30)))
        expected = secret_patterns.redact_sync_text(text)[0].encode("utf-8")
        outcome, out = _file("files/x.log", text.encode("utf-8"))
        assert outcome.published
        assert out == expected
        assert _gh().encode() not in out


class _FlipReader:
    """A source whose content is replaced between classification and copy."""

    def __init__(self, first: bytes, second: bytes) -> None:
        self._stream = io.BytesIO(first)
        self._second = second

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def rewind(self) -> None:
        self._stream = io.BytesIO(self._second)


@pytest.mark.parametrize(
    ("first", "second"),
    [(b"plain text line\n" * 4, b"\x00\x01binary\x00" * 8),
     (b"\x00\x01binary\x00" * 8, b"plain text line\n" * 4)],
    ids=["text-to-binary", "binary-to-text"],
)
def test_kind_flip_during_copy_is_reported_as_source_change(monkeypatch, first, second):
    monkeypatch.setattr(protection, "TEXT_CHUNK", 8)
    sink = io.BytesIO()
    with pytest.raises(protection.SourceChangedError):
        protection.transform_file("files/x.dat", _FlipReader(first, second), len(first), sink.write)


# --- integration: real local / OneDrive publication ---------------------------


def _target(tmp_path: Path, src: Path, target: str, **kwargs):
    """Config for *target* plus the root that holds ``<machine>/``."""
    if target == "onedrive":
        od_root = tmp_path / "onedrive"
        od_root.mkdir(exist_ok=True)
        cfg = _cfg(tmp_path / "home", src, tmp_path / "unused", target="onedrive",
                   onedrive_root=od_root, **kwargs)
        return cfg, od_root / "Apps" / "agent-logger" / "sessions"
    return _cfg(tmp_path / "home", src, tmp_path / "dest", **kwargs), tmp_path / "dest"


def _run(cfg, capsys, **kwargs):
    code = engine.run_sync(cfg, verbose=True, **kwargs)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _tree(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _records() -> list[dict]:
    return [
        {"type": "user", "text": 'say "hi" \\ C:\\path ' + "tok" + "en=" + _gh(), "n": 1},
        {"type": "tool", "data": {"env": {"line": "Authori" + "zation: " + _bearer()}},
         "ok": True, "none": None, "f": 1.5},
        {"type": "unicode", "text": "caf\u00e9 \u2615 " + _jwt(), "list": [3, False, "plain"]},
        {"type": "aws", "keys": ["x", _aws()], "big": 12345678901234567890},
    ]


def _rich_source(root: Path) -> Path:
    src = root / "copilot"
    sess = src / "session-state" / SID
    records = _records()
    lines = [json.dumps(records[0]), json.dumps(records[1]),
             json.dumps(records[2], ensure_ascii=False), json.dumps(records[3])]
    files = {
        "events.jsonl": ("\n".join(lines) + "\n").encode("utf-8"),
        "workspace.yaml": b"id: abc-123\ncwd: /w\n",
        "context.json": ('{"cwd": "/w", "env": {"GH_TOKEN": "' + _gh() + '"}}').encode(),
        "checkpoints/index.md": ("# index\nuse " + _gh() + "\n").encode(),
        "checkpoints/001-other.md": b"other\n",
        "files/blob.bin": b"\x00\x01" + _gh().encode() + b"\x00",
        "files/clean.bin": b"\x00\x01\x02clean\xff" * 10,
        "files/notes.txt": ("notes " + _aws() + "\n").encode(),
        "rewind-file-snapshots/a.json": b'{"a": 1}',
        "session.db": b"SQLite format 3\x00" + b"\x00" * 32,
        "instructions/a.md": b"# a\n",
    }
    for member, data in files.items():
        path = sess / member
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (src / "session-store.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 32)
    return src


def _secrets() -> list[bytes]:
    return [value.encode() for value in (_gh(), _bearer(), _jwt(), _aws())]


def _has_secret(data: bytes | str) -> bool:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return any(secret in raw for secret in _secrets())


_MODES = [(None, None), (True, None), (None, "essential"), (True, "essential")]


@pytest.mark.parametrize("target", ["local", "onedrive"])
@pytest.mark.parametrize(("redact", "files"), _MODES)
def test_publication_honors_each_opt_in(tmp_path, capsys, target, redact, files):
    src = _rich_source(tmp_path)
    sess = src / "session-state" / SID
    before = _tree(sess)
    index_before = (src / "session-store.db").read_bytes()
    cfg, base = _target(tmp_path, src, target, redact=redact, files=files)
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    after = _tree(sess)
    # Local bytes stay as they were (the pass only adds its own sidecars).
    assert all(after[name] == data for name, data in before.items())
    assert (src / "session-store.db").read_bytes() == index_before
    published = _tree(base / "machine" / "session-state" / SID)
    receipt = base / "machine" / protection.RECEIPT_DIR / f"{SID}.json"
    index_copy = base / "machine" / "session-store.db"
    if files == "essential":
        expected = {name for name in after if name in protection.ESSENTIAL_SESSION_FILES}
    else:
        expected = set(after) - ({"files/blob.bin", "session.db"} if redact else set())
    assert set(published) == expected
    assert index_copy.is_file() == (redact is None and files is None)
    if not redact:
        assert all(published[name] == after[name] for name in published)
        assert not receipt.exists()
        return
    assert receipt.is_file()
    # Published copies, receipts and sync metadata all stay secret-free.
    assert not any(_has_secret(data) for data in _tree(base).values())
    assert not _has_secret(out + err)
    lines = published["events.jsonl"].decode("utf-8").split("\n")
    assert lines[-1] == ""
    parsed = [json.loads(line) for line in lines[:-1]]
    assert [record["type"] for record in parsed] == [r["type"] for r in _records()]
    assert parsed[0]["n"] == 1 and MARKER in parsed[0]["text"]
    assert parsed[0]["text"].startswith('say "hi" \\ C:\\path ')
    assert (parsed[1]["ok"], parsed[1]["none"], parsed[1]["f"]) == (True, None, 1.5)
    assert parsed[2]["list"] == [3, False, "plain"]
    assert parsed[2]["text"].startswith("caf\u00e9 \u2615 ")
    assert parsed[3]["keys"] == ["x", MARKER]
    assert parsed[3]["big"] == 12345678901234567890
    assert json.loads(published["context.json"])["cwd"] == "/w"
    assert published["workspace.yaml"] == after["workspace.yaml"]
    if files is None:
        assert published["files/clean.bin"] == after["files/clean.bin"]
        assert published["checkpoints/001-other.md"] == after["checkpoints/001-other.md"]
        assert "withheld 3 protected file(s)" in out
        summary = read_sync_meta(base / "machine")["session_protection"]
        withheld = {item["path"] for item in summary["withheld"]}
        assert f"session-state/{SID}/files/blob.bin" in withheld


@pytest.mark.parametrize("target", ["local", "onedrive"])
def test_binary_token_split_across_scan_reads_is_withheld(tmp_path, capsys, monkeypatch, target):
    real_scan = protection.scan_stream_for_secret

    def small_reads(source, sink=None):
        return real_scan(source, sink, chunk_size=64, overlap=48)

    monkeypatch.setattr(protection, "scan_stream_for_secret", small_reads)
    src = _make_source(tmp_path)
    files = src / "session-state" / SID / "files"
    files.mkdir()
    token = _gh().encode()
    for offset in (40, 50, 63):  # the token always spans the 64-byte read boundary
        (files / f"split-{offset}.bin").write_bytes(
            b"\x00" * offset + token + b"\x00" * 100)
    (files / "clean.bin").write_bytes(b"\x00\x01" * 200)
    cfg, base = _target(tmp_path, src, target, redact=True)
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    published = _tree(base / "machine" / "session-state" / SID)
    assert not any(name.startswith("files/split-") for name in published)
    assert published["files/clean.bin"] == (files / "clean.bin").read_bytes()
    assert "withheld 3 protected file(s)" in out
    assert not _has_secret(out + err)
    assert not any(_has_secret(data) for data in _tree(base).values())


def test_essential_never_reads_or_copies_unlisted_files(tmp_path, capsys, monkeypatch):
    seen: list[str] = []
    real_protected = protected_publication._protected_copy
    real_copy = filesystem._copy_replace

    def protected(src, dst, member, **kwargs):
        seen.append(member)
        return real_protected(src, dst, member, **kwargs)

    def plain(src, dst):
        seen.append(Path(src).name)
        return real_copy(src, dst)

    monkeypatch.setattr(protected_publication, "_protected_copy", protected)
    monkeypatch.setattr(filesystem, "_copy_replace", plain)
    for redact in (None, True):
        seen.clear()
        src = _rich_source(tmp_path / f"r{redact}")
        cfg, base = _target(tmp_path / f"r{redact}", src, "local", redact=redact,
                            files="essential")
        code, _out, err = _run(cfg, capsys)
        assert code == 0, err
        assert seen
        if redact:
            assert set(seen) <= set(protection.ESSENTIAL_SESSION_FILES)
        else:
            assert set(seen) <= {Path(name).name for name in protection.ESSENTIAL_SESSION_FILES}
        assert not {"blob.bin", "clean.bin", "notes.txt", "a.json", "session.db",
                    "a.md", "001-other.md", "session-store.db"} & set(seen)


# --- integration: projected change detection ----------------------------------

_PROTECTED_MODES = [(True, None), (None, "essential"), (True, "essential")]


def _count_pushes(monkeypatch) -> list:
    calls: list = []
    real_push = LocalTarget.push

    def counting(self, source, machine, include_sessions=None, **kwargs):
        calls.append(include_sessions)
        return real_push(self, source, machine, include_sessions, **kwargs)

    monkeypatch.setattr(LocalTarget, "push", counting)
    return calls


def _count_copies(monkeypatch) -> list[str]:
    copies: list[str] = []
    real_protected = protected_publication._protected_copy
    real_copy = filesystem._copy_replace

    def protected(src, dst, member, **kwargs):
        copies.append(member)
        return real_protected(src, dst, member, **kwargs)

    def plain(src, dst):
        copies.append(Path(src).name)
        return real_copy(src, dst)

    monkeypatch.setattr(protected_publication, "_protected_copy", protected)
    monkeypatch.setattr(filesystem, "_copy_replace", plain)
    return copies


@pytest.mark.parametrize(("redact", "files"), _PROTECTED_MODES)
def test_unchanged_protected_inputs_are_not_republished(tmp_path, capsys, monkeypatch,
                                                         redact, files):
    src = _rich_source(tmp_path)
    cfg, base = _target(tmp_path, src, "local", redact=redact, files=files)
    code, _out, err = _run(cfg, capsys)
    assert code == 0, err
    published = _tree(base / "machine" / "session-state")
    receipt = base / "machine" / protection.RECEIPT_DIR / f"{SID}.json"
    assert receipt.is_file() == bool(redact)
    pushes = _count_pushes(monkeypatch)
    copies = _count_copies(monkeypatch)
    for _ in range(2):
        code, out, err = _run(cfg, capsys)
        assert code == 0, err
        assert "no session changes detected" in out
    assert pushes == []
    # Even a forced reconciliation re-derives nothing for unchanged inputs.
    code, out, err = _run(cfg, capsys, full=True)
    assert code == 0, err
    assert "full reconciliation: 1 session(s)" in out
    assert len(pushes) == 1 and copies == []
    assert _tree(base / "machine" / "session-state") == published


@pytest.mark.parametrize("redact", [None, True])
def test_essential_publishes_listed_edits_only(tmp_path, capsys, monkeypatch, redact):
    src = _rich_source(tmp_path)
    sess = src / "session-state" / SID
    cfg, base = _target(tmp_path, src, "local", redact=redact, files="essential")
    code, _out, err = _run(cfg, capsys)
    assert code == 0, err
    published = _tree(base / "machine" / "session-state")
    pushes = _count_pushes(monkeypatch)
    _grow(sess / "files" / "notes.txt")
    _grow(sess / "checkpoints" / "001-other.md")
    _grow(sess / "rewind-file-snapshots" / "b.json", b'{"b": 2}')
    (sess / "session.db").write_bytes(b"SQLite format 3\x00" + b"\x01" * 64)
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    assert "no session changes detected" in out and pushes == []
    assert _tree(base / "machine" / "session-state") == published
    _grow(sess / "events.jsonl", (json.dumps({"type": "late", "k": _gh()}) + "\n").encode())
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    assert len(pushes) == 1
    events = (_dest_session(base) / "events.jsonl").read_bytes()
    assert json.loads(events.decode().splitlines()[-1])["type"] == "late"
    assert _has_secret(events) == (redact is None)
    assert set(_tree(_dest_session(base))) <= set(protection.ESSENTIAL_SESSION_FILES)


def test_enabling_redaction_reconciles_but_leaves_old_raw_copies(tmp_path, capsys):
    src = _rich_source(tmp_path)
    cfg, base = _target(tmp_path, src, "local")
    code, _out, err = _run(cfg, capsys)
    assert code == 0, err
    dest = _dest_session(base)
    raw_blob = (dest / "files" / "blob.bin").read_bytes()
    assert _has_secret((dest / "events.jsonl").read_bytes())
    cfg, _base = _target(tmp_path, src, "local", redact=True)
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    assert "full reconciliation" in out
    assert not _has_secret((dest / "events.jsonl").read_bytes())
    assert not _has_secret((dest / "context.json").read_bytes())
    # Withheld files are not re-derived: the earlier raw copy stays and is
    # reported as such.
    assert (dest / "files" / "blob.bin").read_bytes() == raw_blob
    summary = read_sync_meta(base / "machine")["session_protection"]
    prior = {item["path"]: item["prior_copy"] for item in summary["withheld"]}
    assert prior[f"session-state/{SID}/files/blob.bin"] == protection.PRIOR_COPY_UNPROTECTED
    assert prior[f"session-state/{SID}/session.db"] == protection.PRIOR_COPY_UNPROTECTED
    assert not _has_secret(out + err)


def test_essential_reconcile_leaves_unselected_copies_and_doctor_warns(tmp_path, capsys):
    src = _rich_source(tmp_path)
    cfg, base = _target(tmp_path, src, "local")
    code, _out, err = _run(cfg, capsys)
    assert code == 0, err
    dest = _dest_session(base)
    unselected = ("files/blob.bin", "rewind-file-snapshots/a.json", "instructions/a.md")
    earlier = {name: (dest / name).read_bytes() for name in unselected}
    cfg, _base = _target(tmp_path, src, "local", files="essential")
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    assert "full reconciliation" in out
    # The reconcile replaces selected files only; it removes no copy.
    assert {name: (dest / name).read_bytes() for name in unselected} == earlier
    assert (dest / "events.jsonl").is_file()
    engine.do_doctor(cfg)
    rows = [line for line in capsys.readouterr().out.splitlines()
            if "session protection" in line]
    assert len(rows) == 1 and rows[0].startswith("  [ok ]")
    assert "warning:" in rows[0] and "destination file(s)" in rows[0]


def test_doctor_reports_a_destination_produced_under_current_settings(tmp_path, capsys):
    cfg, _base = _target(tmp_path, _rich_source(tmp_path), "local", redact=True)
    code, _out, err = _run(cfg, capsys)
    assert code == 0, err
    engine.do_doctor(cfg)
    rows = [line for line in capsys.readouterr().out.splitlines()
            if "session protection" in line]
    assert len(rows) == 1 and "warning:" not in rows[0]
    assert rows[0].endswith("no destination copies outside these settings found)")


def test_destination_change_triggers_full_protected_reconcile(tmp_path, capsys):
    src = _rich_source(tmp_path)
    cfg, base = _target(tmp_path, src, "local", redact=True)
    code, _out, err = _run(cfg, capsys)
    assert code == 0, err
    published = _tree(_dest_session(base))
    # A tampered destination copy no longer matches its receipt: the forced
    # reconciliation re-derives it instead of trusting the recorded outcome.
    (_dest_session(base) / "events.jsonl").write_bytes(b"tampered\n")
    code, _out, err = _run(cfg, capsys, full=True)
    assert code == 0, err
    assert _tree(_dest_session(base)) == published
    moved = _cfg(tmp_path / "home", src, tmp_path / "moved", redact=True)
    code, out, err = _run(moved, capsys)
    assert code == 0, err
    assert "full reconciliation: 1 session(s)" in out
    assert _tree(_dest_session(tmp_path / "moved")) == published
    assert (tmp_path / "moved" / "machine" / protection.RECEIPT_DIR / f"{SID}.json").is_file()


@pytest.mark.parametrize("target", ["local", "onedrive"])
@pytest.mark.parametrize(("redact", "files"), _MODES)
def test_dry_run_reports_effective_scope_without_output(tmp_path, capsys, target,
                                                        redact, files):
    src = _rich_source(tmp_path)
    before = _tree(src)
    cfg, base = _target(tmp_path, src, target, redact=redact, files=files)
    code, out, err = _run(cfg, capsys, dry_run=True)
    assert code == 0, err
    would = [line for line in out.splitlines() if "would push" in line]
    assert len(would) == 1
    settings = cfg.sync_protection
    if settings.enabled:
        assert would[0].endswith(f", protection: {settings.describe()})")
    else:
        assert "protection" not in out
    assert not base.exists()
    assert _tree(src) == before
    assert not _has_secret(out + err)


def _scoped_cfg(tmp_path: Path, **kwargs):
    src = tmp_path / "copilot"
    for sid, repo in (("sess-a", "dotfiles"), ("sess-b", "other-repo"), ("sess-c", None)):
        sess = src / "session-state" / sid
        (sess / "files").mkdir(parents=True)
        (sess / "events.jsonl").write_bytes((json.dumps({"k": _gh()}) + "\n").encode())
        (sess / "files" / "n.txt").write_bytes(b"n\n")
        if repo:
            (sess / "workspace.yaml").write_bytes(f"cwd: /home/u/Src/{repo}\n".encode())
    (src / "session-store.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 32)
    data = copy.deepcopy(_cfg(tmp_path / "home", src, tmp_path / "dest", **kwargs).as_dict())
    data["sync"]["repo_allowlist"] = ["dotfiles"]
    data["sync"]["repo_allowlist_fail_closed"] = True
    return Config(data, tmp_path / "home"), tmp_path / "dest"


@pytest.mark.parametrize(("redact", "files"), _PROTECTED_MODES)
def test_repo_scope_and_protection_combine_fail_closed(tmp_path, capsys, redact, files):
    cfg, dest = _scoped_cfg(tmp_path, redact=redact, files=files)
    code, out, err = _run(cfg, capsys, dry_run=True)
    assert code == 0, err
    assert f"1 session(s) match, protection: {cfg.sync_protection.describe()})" in out
    assert not dest.exists()
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    published = _tree(dest / "machine")
    sessions = {name.split("/")[1] for name in published if name.startswith("session-state/")}
    assert sessions == {"sess-a"}
    assert "session-store.db" not in published
    assert ("session-state/sess-a/files/n.txt" in published) == (files is None)
    leaked = any(_has_secret(data) for data in published.values())
    assert leaked == (not redact)


# --- refusals: remote targets, compaction, process logs, rescue ---------------


@pytest.mark.parametrize("target", ["ssh", "ssh-tunnel", "ingest"])
@pytest.mark.parametrize(("redact", "files"), _PROTECTED_MODES)
def test_remote_targets_refuse_protected_settings(tmp_path, capsys, target, redact, files):
    src = _make_source(tmp_path)
    cfg = _cfg(tmp_path / "home", src, tmp_path / "dest", redact=redact, files=files,
               target=target)
    runs = (
        lambda: engine.run_sync(cfg),
        lambda: engine.run_sync(cfg, dry_run=True),
        lambda: engine.run_push(cfg, source=str(src), machine="m"),
    )
    for run in runs:
        assert run() == 1
        err = capsys.readouterr().err
        assert "supported only by the local and onedrive targets" in err
        assert f"(configured: {target})" in err
    assert engine.do_doctor(cfg) == 1
    assert "[FAIL] session protection" in capsys.readouterr().out
    assert not (tmp_path / "dest").exists()


@pytest.mark.parametrize("target", ["ssh", "ingest"])
def test_remote_targets_are_unchanged_without_protection(tmp_path, target):
    cfg = _cfg(tmp_path / "home", _make_source(tmp_path), tmp_path / "dest", target=target)
    remote = engine.build_target(target, cfg.target_options(target))
    assert protection_engine.apply_protection(cfg, remote) is None
    assert remote.protection is None


def test_compaction_is_skipped_and_compact_hub_refused(tmp_path, capsys, monkeypatch):
    from agent_logger.sync import compact

    src = _make_source(tmp_path)
    cfg = _cfg(tmp_path / "home", src, tmp_path / "dest", files="essential", compact=True)
    assert engine.do_compact_hub(cfg, dry_run=False, verbose=False) == 1
    assert "not supported with sync.redact_secrets" in capsys.readouterr().err

    def forbidden(*_args, **_kwargs):
        raise AssertionError("compaction must not run under protection")

    monkeypatch.setattr(compact, "compact_local", forbidden)
    monkeypatch.setattr(LocalTarget, "push_archives", forbidden)
    monkeypatch.setattr(LocalTarget, "compact_backlog", forbidden)
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    assert "compaction skipped" in out


def test_redaction_keeps_unredacted_process_logs_off_the_target(tmp_path, capsys,
                                                                 monkeypatch):
    src = _make_source(tmp_path)
    (src / "logs").mkdir()
    (src / "logs" / "process-1.log").write_bytes(("t " + _gh() + "\n").encode())
    cfg = _cfg(tmp_path / "home", src, tmp_path / "dest", redact=True)
    data = cfg.as_dict()
    data["sync"].setdefault("process_logs", {})["enabled"] = True
    cfg = Config(data, tmp_path / "home")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("process logs must not sync under redaction")

    monkeypatch.setattr(engine, "sync_process_logs", forbidden)
    for dry_run in (True, False):
        code, out, err = _run(cfg, capsys, dry_run=dry_run)
        assert code == 0, err
        assert "process-log sync skipped" in out
    assert not any(_has_secret(data) for data in _tree(tmp_path / "dest").values())


def test_rescue_publication_is_refused_under_protection(tmp_path):
    src = _make_source(tmp_path)
    cfg = _cfg(tmp_path / "home", src, tmp_path / "dest", redact=True)
    with pytest.raises(RescueSourceError, match="session protection"):
        rescue.push_rescues(cfg, rescue_roots=[tmp_path / "rescue"])
    target = LocalTarget({"path": str(tmp_path / "dest")})
    with pytest.raises(RescueSourceError, match="session protection"):
        rescue_projection.push_venue(target, cfg, "container-x", [])
    # A protected target refuses even when the config was built without it.
    plain = _cfg(tmp_path / "home", src, tmp_path / "dest")
    target.protection = cfg.sync_protection
    with pytest.raises(RescueSourceError, match="session protection"):
        rescue_projection.push_venue(target, plain, "container-x", [])
    assert not (tmp_path / "dest").exists()


# --- integration: whole-file deferral of unreadable transcripts ---------------

_EVENTS_REL = f"session-state/{SID}/events.jsonl"
_DEFER_KINDS = [("incomplete", protection.REASON_INCOMPLETE_RECORD),
                ("malformed", protection.REASON_MALFORMED_RECORD)]


def _secret_line(n: int) -> bytes:
    return (json.dumps({"n": n, "text": "tok" + "en=" + _gh()}) + "\n").encode()


def _corrupt(events: Path, kind: str) -> None:
    if kind == "incomplete":
        _grow(events, b'{"n": 9, "text": "' + _gh().encode())
    else:
        events.write_bytes(
            _secret_line(1) + b"{bad " + _gh().encode() + b"}\n" + _secret_line(3)
        )


def _assert_deferred(base: Path, out: str, err: str, reason: str, prior: str) -> None:
    assert f"deferred 1 protected file(s), will retry: {_EVENTS_REL} [{reason}]" in out
    # Diagnostics name the file and a fixed reason, never its content.
    assert not _has_secret(out + err)
    assert not any(_has_secret(data) for data in _tree(base).values())
    meta = read_sync_meta(base / "machine")
    assert meta["status"] == "partial"
    assert [path.replace("\\", "/") for path in meta["deferred_files"]] == [_EVENTS_REL]
    summary = meta["session_protection"]
    assert summary["deferred_count"] == 1
    assert [(item["path"], item["reason"], item["prior_copy"])
            for item in summary["deferred"]] == [(_EVENTS_REL, reason, prior)]
    leg = meta["sync_legs"]["session-state"]
    assert (leg["status"], leg["reason"]) == ("partial", "protected-deferral")


@pytest.mark.parametrize("target", ["local", "onedrive"])
@pytest.mark.parametrize(("kind", "reason"), _DEFER_KINDS, ids=["incomplete", "malformed"])
def test_unreadable_transcript_keeps_last_published_copy(tmp_path, capsys, target, kind, reason):
    src = _make_source(tmp_path)
    events = _events(src)
    events.write_bytes(_secret_line(1) + _secret_line(2))
    cfg, base = _target(tmp_path, src, target, redact=True)
    assert _run(cfg, capsys)[0] == 0
    published = _dest_session(base) / "events.jsonl"
    good = published.read_bytes()
    assert len(good.splitlines()) == 2 and not _has_secret(good)

    _corrupt(events, kind)
    for _ in range(2):  # stays deferred, and is retried, on every ordinary pass
        code, out, err = _run(cfg, capsys)
        assert code == 0, err
        assert "no session changes detected" not in out
        assert published.read_bytes() == good
        _assert_deferred(base, out, err, reason, protection.PRIOR_COPY_PROTECTED)

    events.write_bytes(_secret_line(1) + _secret_line(2) + _secret_line(3))
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    assert "deferred" not in out
    lines = published.read_bytes().splitlines()
    assert [json.loads(line)["n"] for line in lines] == [1, 2, 3]
    assert not any(_has_secret(data) for data in _tree(base).values())
    meta = read_sync_meta(base / "machine")
    assert meta["status"] == "ok"
    assert meta["session_protection"]["deferred_count"] == 0


@pytest.mark.parametrize("target", ["local", "onedrive"])
def test_unreadable_transcript_without_prior_copy_publishes_nothing(tmp_path, capsys, target):
    src = _make_source(tmp_path)
    events = _events(src)
    events.write_bytes(_secret_line(1))
    _corrupt(events, "incomplete")
    cfg, base = _target(tmp_path, src, target, redact=True)
    published = _dest_session(base) / "events.jsonl"
    for _ in range(2):
        code, out, err = _run(cfg, capsys)
        assert code == 0, err
        assert not published.exists()
        assert (_dest_session(base) / "workspace.yaml").is_file()
        _assert_deferred(
            base, out, err, protection.REASON_INCOMPLETE_RECORD, protection.PRIOR_COPY_NONE
        )
        # A deferral never completes the full reconciliation it was part of.
        assert "full reconciliation" in out
        assert _tracker(cfg).should_full_sync(cfg.sync_change_tracking["full_sync_interval_hours"])

    events.write_bytes(_secret_line(1) + _secret_line(2))
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    assert [json.loads(line)["n"] for line in published.read_bytes().splitlines()] == [1, 2]
    assert read_sync_meta(base / "machine")["status"] == "ok"
    assert not _tracker(cfg).should_full_sync(
        cfg.sync_change_tracking["full_sync_interval_hours"]
    )


def _lock_events_once(monkeypatch, events: Path) -> list[int]:
    """The transcript's writer holds it locked for one pass."""
    original = protected_publication._protected_copy
    armed = {"events.jsonl"}

    def locked(src, dst, member, **kwargs):
        if member in armed:
            armed.discard(member)
            raise filesystem._LockedSourceFile(f"source file is locked: {member}")
        return original(src, dst, member, **kwargs)

    monkeypatch.setattr(protected_publication, "_protected_copy", locked)
    return [1, 2, 3]


def _append_during_copy(monkeypatch, events: Path) -> list[int]:
    """The transcript's writer appends a record while it is being copied."""
    original = protection.transform_file
    armed = {"events.jsonl"}

    def growing(member, reader, size, sink):
        if member in armed:
            armed.discard(member)
            _grow(events, _secret_line(4))
        return original(member, reader, size, sink)

    monkeypatch.setattr(protection, "transform_file", growing)
    return [1, 2, 3, 4]


@pytest.mark.parametrize("target", ["local", "onedrive"])
@pytest.mark.parametrize(
    "interrupt", [_lock_events_once, _append_during_copy], ids=["locked", "changed"],
)
def test_locked_or_changing_transcript_keeps_its_retry_path(
    tmp_path, capsys, monkeypatch, target, interrupt,
):
    src = _make_source(tmp_path)
    events = _events(src)
    events.write_bytes(_secret_line(1) + _secret_line(2))
    cfg, base = _target(tmp_path, src, target, redact=True)
    assert _run(cfg, capsys)[0] == 0
    published = _dest_session(base) / "events.jsonl"
    good = published.read_bytes()

    _grow(events, _secret_line(3))
    expected = interrupt(monkeypatch, events)
    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    assert "skipped 1 locked file(s), will retry" in out
    assert "protected file(s)" not in out
    assert published.read_bytes() == good
    assert not _has_secret(out + err)
    meta = read_sync_meta(base / "machine")
    assert meta["status"] == "partial"
    assert meta["session_protection"]["deferred_count"] == 0

    code, out, err = _run(cfg, capsys)
    assert code == 0, err
    assert "will retry" not in out
    assert [json.loads(line)["n"] for line in published.read_bytes().splitlines()] == expected
    assert not any(_has_secret(data) for data in _tree(base).values())
    assert read_sync_meta(base / "machine")["status"] == "ok"


def test_jsonl_transform_memory_tracks_records_not_file_size(tmp_path):
    line = _secret_line(0)
    _jsonl(line)  # warm pattern and decoder caches outside the measurement
    path = tmp_path / "events.jsonl"
    count = (2 * 1024 * 1024) // len(line) + 1
    with path.open("wb") as handle:
        for _ in range(count):
            handle.write(line)
    size = path.stat().st_size
    written = {"bytes": 0, "leaks": 0}

    def sink(data: bytes) -> None:
        written["bytes"] += len(data)
        written["leaks"] += _has_secret(data)

    tracemalloc.start()
    try:
        with path.open("rb") as handle:
            outcome = protection.transform_jsonl(protection.BoundedReader(handle, size), sink)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert outcome.published and outcome.records == count
    assert outcome.redacted >= count and written["leaks"] == 0 and written["bytes"] > 0
    # One record is held at a time, never the whole transcript.
    assert peak < size // 16
