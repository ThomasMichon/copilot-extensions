from __future__ import annotations

from agent_mcp.transports.sse import parse_sse_events


def test_single_event():
    events = parse_sse_events("event: message\ndata: {\"a\":1}\n\n")
    assert len(events) == 1
    assert events[0].event == "message"
    assert events[0].data == '{"a":1}'


def test_multiple_events():
    body = "data: one\n\ndata: two\n\n"
    events = parse_sse_events(body)
    assert [e.data for e in events] == ["one", "two"]


def test_multiline_data_joined():
    events = parse_sse_events("data: a\ndata: b\n\n")
    assert events[0].data == "a\nb"


def test_comment_lines_ignored():
    events = parse_sse_events(": keep-alive\ndata: x\n\n")
    assert [e.data for e in events] == ["x"]


def test_trailing_event_without_blank_line():
    events = parse_sse_events("data: last")
    assert [e.data for e in events] == ["last"]


def test_crlf_normalized():
    events = parse_sse_events("data: x\r\n\r\n")
    assert events[0].data == "x"


def test_default_event_type_is_message():
    events = parse_sse_events("data: x\n\n")
    assert events[0].event == "message"
