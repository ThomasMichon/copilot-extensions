"""Finite, caller-contained loopback example; never a durable daemon."""

import argparse
import json
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import version
from pathlib import Path

import example_message


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["install", "serve"], nargs="?", default="serve")
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--stop-file", type=Path)
    parser.add_argument("--lifetime", type=float, default=30)
    parser.add_argument("--fail", action="store_true")
    args = parser.parse_args()
    if not 0 < args.lifetime <= 60:
        parser.error("lifetime must be greater than zero and at most 60 seconds")
    if args.fail:
        return 17
    if args.command == "install":
        print(json.dumps({"installed": True, "message": example_message.message()}))
        return 0
    if args.ready_file is None or args.stop_file is None:
        parser.error("serve requires --ready-file and --stop-file")
    details = {
        "message": example_message.message(),
        "version": version("uv-slot-example-service"),
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
        "module": str(Path(__file__).resolve()),
        "library": str(Path(example_message.__file__).resolve()),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(details).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = 0.1
        candidate = args.ready_file.with_suffix(".new")
        candidate.write_text(
            json.dumps({**details, "port": server.server_port}), encoding="utf-8"
        )
        candidate.replace(args.ready_file)
        deadline = time.monotonic() + args.lifetime
        while time.monotonic() < deadline and not args.stop_file.exists():
            server.handle_request()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
