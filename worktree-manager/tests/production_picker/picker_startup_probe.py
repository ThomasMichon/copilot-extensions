"""Explicit native/headless qualification fixture, never production startup code."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET


def engine_main(root, count, batched, args):
    verb = args[2] if args[:1] == ["--project"] else args[0]
    with (root / "calls.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"verb": verb, "args": args[3:]}) + "\n")
    if verb == "picker-bootstrap":
        time.sleep(2)
        payload = {
            "project": "example", "default_live": True,
            "should_switch_cwd": False, "cwd": None,
        }
        if batched:
            payload["project_config"] = {
                "config_dir": str(root), "repo_dir": str(root), "machine": "host",
            }
        print(json.dumps(payload))
        return 0
    if verb == "get":
        print("host" if args[-1] == "machine" else str(root))
        return 0
    if verb == "picker-paths":
        print(json.dumps({"version": 1, "install_dir": str(root)}))
        return 0
    from worktree_manager import demo, demo_engine
    if verb == "list":
        original = demo.list_envelope()
        template = original["worktrees"][0]
        rows = [
            {**template, "id": f"fixture-{index}", "title": f"Fixture row {index}"}
            for index in range(count)
        ]
        demo.list_envelope = lambda: {**original, "worktrees": rows}
    return demo_engine.main(args)


def consumer_main(root, count, batched):
    from textual import events
    from worktree_manager import __main__ as cli, engine_client
    from worktree_manager.production_picker import runner
    from worktree_manager.production_picker.picker_tui import data_ssh, engine
    from worktree_manager.production_picker.picker_tui.engine_loading import PendingBindingScreen

    engine_client.set_engine_command([
        sys.executable, __file__, "engine", str(root), str(count), str(int(batched)),
    ])
    # Synthetic provider roster; the real loader still owns one current/follow
    # process, transaction parsing, current revision and UI delivery.
    data_ssh._build_sources = lambda: [
        data_ssh.Source("host", "Win", None, local=True, display_machine="host"),
    ]
    runner._start_housekeeping = lambda: None
    runner._start_anchor_heal_check = lambda project: None
    runner.housekeeping.start_picker_monitor_root = lambda: None
    engine.PickerScreen._setup_live_pivots = lambda self: None
    for name in ("_poll_update_state", "_poll_manager_update_state", "_poll_orphan_state"):
        setattr(engine.PickerScreen, name, lambda self: None)
    sys.stdin.isatty = lambda: True
    samples = []
    observed = {}
    original_refresh = PendingBindingScreen._start_binding
    original_event = engine.PickerApp.on_event

    def first_refresh(self):
        observed["frame"] = time.perf_counter()
        return original_refresh(self)

    async def on_event(self, event):
        if isinstance(event, events.Key) and event.key == "x":
            observed["ack"] = time.perf_counter()
        await original_event(self, event)

    PendingBindingScreen._start_binding = first_refresh
    engine.PickerApp.on_event = on_event

    def run(app):
        async def drive():
            async with app.run_test(size=(120, 32)) as pilot:
                for _ in range(50):
                    if "frame" in observed:
                        break
                    await pilot.pause(0.01)
                assert "frame" in observed, "pending refresh did not run"
                observed["sent"] = time.perf_counter()
                app.post_message(events.Key("x", "x"))
                deadline = time.perf_counter() + 25
                while time.perf_counter() < deadline:
                    if app.binding_error is not None:
                        raise app.binding_error
                    widgets = list(app.query(engine.PickerScreen))
                    if widgets:
                        screen = widgets[0]
                        if (
                            len(screen.data) == count
                            and screen.loader is not None
                            and screen.loader.authoritative_source_ids()
                        ):
                            rendered = "".join(ET.fromstring(app.export_screenshot()).itertext())
                            if "Fixture" in rendered:
                                break
                    await pilot.pause(0.02)
                else:
                    raise AssertionError(
                        f"current rows did not arrive: {screen.debug}, "
                        f"rows={len(screen.data)}, "
                        f"loader={screen.loader.counts() if screen.loader else None}, "
                        f"calls={(root / 'calls.jsonl').read_text()}"
                    )
                observed["rows"] = time.perf_counter()
                app.exit()
        asyncio.run(drive())

    engine.PickerApp.run = run
    for mode in ("cold-process", "warm-process"):
        observed.clear()
        before = (root / "calls.jsonl").read_text().splitlines() if (root / "calls.jsonl").exists() else []
        start = float(os.environ["PICKER_PROBE_START"]) if mode == "cold-process" else time.perf_counter()
        assert cli.main(["picker", "example"]) == 0
        calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()[len(before):]]
        samples.append({
            "cache_mode": mode, "rows": count, "batched": batched,
            "entry_to_headless_refresh_ms": round((observed["frame"] - start) * 1000, 2),
            "entry_to_current_rows_rendered_ms": round((observed["rows"] - start) * 1000, 2),
            "input_ack_ms": round((observed["ack"] - observed["sent"]) * 1000, 2),
            "calls": calls,
            "native_compositor_visibility": "unavailable: real Textual headless driver",
        })
    print(json.dumps(samples))
    return 0


if __name__ == "__main__":
    mode, directory, cardinality, batch, *rest = sys.argv[1:]
    root = Path(directory)
    count = int(cardinality)
    batched = batch == "1"
    raise SystemExit(
        engine_main(root, count, batched, rest)
        if mode == "engine" else consumer_main(root, count, batched)
    )
