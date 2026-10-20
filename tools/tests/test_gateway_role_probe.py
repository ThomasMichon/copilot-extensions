"""Exercise the actual HTML request assertion without optional host packages."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest


def probe_type():
    path = (Path(__file__).resolve().parents[2]
            / "tools/clean-room/gateway-role-tunnels/probe.py")
    module = ast.parse(path.read_text())
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    probe = next(node for node in module.body if isinstance(node, ast.ClassDef)
                 and node.name == "Probe")
    namespace = {"json": json, "ORIGIN": "https://example.test"}
    extracted = ast.fix_missing_locations(ast.Module(body=[future, probe], type_ignores=[]))
    exec(compile(extracted, str(path), "exec"), namespace)
    return namespace["Probe"]


@pytest.mark.parametrize("content_type,text,valid", [
    ("text/html", "<html><body>a-original</body></html>", True),
    ("application/json", '{"service":"a","instance":"a-original"}', False),
    ("text/html", '{"service":"a","instance":"a-original"}', False),
    ("text/html", "<html><body>b-original</body></html>", False),
])
def test_html_probe_requires_real_fixture_markup(content_type: str, text: str, valid: bool) -> None:
    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def text(self):
            return text

    response = Response()
    response.content_type = content_type

    class Session:
        def get(self, *args, **kwargs):
            return response

    async def scenario():
        probe = probe_type()(Session())

        async def auth(*args):
            return {}

        probe.auth = auth
        request = probe.request("/ui/a/index.html", 200, instance="a-original", html=True)
        if valid:
            assert await request == {}
        else:
            with pytest.raises(RuntimeError, match="wrong-html-response"):
                await request

    asyncio.run(scenario())
