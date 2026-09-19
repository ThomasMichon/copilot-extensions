"""Native shell command files are UTF-8/LF input, not rewriteable programs."""

import json
from types import SimpleNamespace

import pytest

from agent_bridge import __main__ as cli
from agent_bridge import native_cli, native_resources


def _arguments(tmp_path, flag="--command-file"):
    return cli.build_parser().parse_args([
        "native", "start", "--codespace", "fixture", "--owner", str(tmp_path),
        "--cwd", "/workspace", "--request-id", "request", flag, str(tmp_path / "command"),
        "--json",
    ])


@pytest.mark.parametrize("program,bom", [
    ("export VALUE=ready\nexec copilot\n", False),
    ("printf '%s\\n' '\u03bb'\nexec copilot\n", False),
    ("printf '%s\\n' '\u03bb'\nexec copilot\n", True),
    ("printf '%s' 'literal\rcarriage'\nprintf '\\r\\n'\n", False),
])
def test_native_command_file_preserves_utf8_lf_and_literal_cr(tmp_path, monkeypatch, capsys, program, bom):
    raw = (("\ufeff" if bom else "") + program).encode("utf-8")
    path = tmp_path / "command"
    path.write_bytes(raw)
    requests = []

    def start(request):
        requests.append(request)
        return {"executionId": "fixture"}

    monkeypatch.setattr(cli, "_get_client", lambda **_: SimpleNamespace(native_start=start))
    native_cli.command(_arguments(tmp_path))
    assert len(requests) == 1 and requests[0]["command"] == program
    assert path.read_bytes() == raw
    assert json.loads(capsys.readouterr().out) == {"executionId": "fixture"}


@pytest.mark.parametrize("flag", ["--command-file", "--interactive-command-file"])
@pytest.mark.parametrize("raw", [
    b"export VALUE=bad\r\nexec copilot\r\n",
    b"\xef\xbb\xbfexport VALUE=bad\r\nexec copilot\r\n",
    b"export FIRST=good\nexport SECOND=bad\r\nexec copilot\n",
])
def test_native_command_file_rejects_crlf_before_resources_or_controller(tmp_path, monkeypatch, capsys, flag, raw):
    path = tmp_path / "command"
    path.write_bytes(raw)
    args = _arguments(tmp_path, flag)
    args.host_resources_file = "must-not-read-resources.json"
    monkeypatch.setattr(cli, "_get_client", lambda **_: pytest.fail("invalid command reached controller allocation"))
    monkeypatch.setattr(native_resources, "read_json_file", lambda _: pytest.fail("invalid command reached resource loading"))
    with pytest.raises(SystemExit) as rejected:
        native_cli.command(args)
    assert rejected.value.code == 69
    error = json.loads(capsys.readouterr().out)
    assert error["error"] == "invalid_command_file"
    assert "CRLF" in error["detail"] and "LF" in error["detail"] and "save" in error["detail"].lower()
    assert path.read_bytes() == raw


def test_native_command_file_retains_invalid_utf8_error(tmp_path, monkeypatch, capsys):
    (tmp_path / "command").write_bytes(b"\xff\n")
    monkeypatch.setattr(cli, "_get_client", lambda **_: pytest.fail("invalid UTF-8 reached the controller"))
    with pytest.raises(SystemExit) as rejected:
        native_cli.command(_arguments(tmp_path))
    assert rejected.value.code == 69
    assert json.loads(capsys.readouterr().out)["error"] == "invalid_command_file"
