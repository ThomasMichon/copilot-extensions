"""The archive carries the existing bounded writer, without enrolling a caller."""
from __future__ import annotations

from pathlib import Path

from test_command_evidence import _recent, _run
from test_command_evidence import payload as command_payload

payload = command_payload


def test_archive_executes_the_existing_bounded_writer_and_settles_its_thread(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    script = tmp_path / "buffer-owner.py"
    script.write_text(
        "import threading\n"
        "from _command_evidence.process_telemetry.buffer import BufferedSink\n"
        "received=[]\n"
        "ready=threading.Event()\n"
        "release=threading.Event()\n"
        "def initialize():\n"
        "    ready.set()\n"
        "    assert release.wait(5)\n"
        "writer=BufferedSink(received.append,on_start=initialize).start()\n"
        "try:\n"
        "    assert ready.wait(5)\n"
        "    status=writer.offer({'synthetic':True})\n"
        "    assert status=='accepted',status\n"
        "    release.set()\n"
        "    assert writer.drain(.25)\n"
        "    assert received==[{'synthetic':True}]\n"
        "finally:\n"
        "    release.set()\n"
        "    writer.close()\n"
        "    assert writer.join(.25)\n",
        encoding="utf-8",
    )
    result = _run(
        payload, "run-script", "fixture-command", "fixture-source", str(script), "--",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == b""
    assert _recent(payload)["exists"] is False
