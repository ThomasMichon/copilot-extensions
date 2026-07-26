#!/usr/bin/env python3
"""Produce a shareable Worktree Picker screenshot (or animated walkthrough).

The elegant pipeline, packaged: **gather** real worktree data (from a directory
of ``list --json`` dumps, or live across the machine roster), **obscure** it
(identity-scrubbed, real-shaped), **render** the picker headlessly to SVG, and
**rasterize** to PNG via a Chromium-family browser -- or capture a *sequence* of
states and assemble an animated GIF.

Realizes the picker vision's obscured / shareable / state-sequence capture. This
is a maintainer/demo tool (it shells out to a headless browser), not part of the
shipped runtime; the reusable core lives in ``picker_tui.capture`` +
``picker_tui.obscure``.

Examples
--------
    # From a directory of `<project> list --json --classify` dumps:
    python tools/picker-shot.py --from-dir ./dumps --view all --out hero.png

    # Live across the roster (SSH), local-only render, raw (no obscure):
    python tools/picker-shot.py --project my-project --gather --raw --out shot.svg

    # Animated walkthrough (pivots -> selection -> menu) as a GIF:
    python tools/picker-shot.py --from-dir ./dumps --animate --out demo.gif
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

# Make the plugin importable when run from a checkout without an install.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "plugins", "agent-worktrees", "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from agent_worktrees.picker_tui import capture as pcap  # noqa: E402
from agent_worktrees.picker_tui import obscure as pobs  # noqa: E402

_ENV = {"windows": "Win", "wsl": "WSL", "linux": "Linux", "win": "Win"}
_BROWSERS = ["msedge", "chrome", "google-chrome", "chromium", "chromium-browser"]
_WIN_BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


# ---- gather -----------------------------------------------------------------

def _strip_to_json(text: str) -> str:
    i = text.find("{")
    return text[i:] if i >= 0 else text


def _group_dump(worktrees: list) -> dict[tuple[str, str], list]:
    out: dict[tuple[str, str], list] = {}
    for w in worktrees:
        machine = w.get("machine") or "machine"
        env = _ENV.get((w.get("platform") or "").lower(), "Win")
        out.setdefault((machine, env), []).append(w)
    return out


def gather_from_dir(path: str, local: str | None) -> list[tuple[str, str, bool, list]]:
    dumps: list[tuple[str, str, bool, list]] = []
    for fp in sorted(glob.glob(os.path.join(path, "*.json"))):
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for (machine, env), wts in _group_dump(data.get("worktrees", []) or []).items():
            dumps.append((machine, env, False, wts))
    return _mark_local(dumps, local)


def gather_live(repo_dir: str, project: str,
                local: str | None) -> list[tuple[str, str, bool, list]]:
    from agent_worktrees import config as cfg
    roster = cfg.load_machines_yaml(repo_dir)
    dumps: list[tuple[str, str, bool, list]] = []
    for entry in roster.values():
        for env in getattr(entry, "ssh_environments", []) or []:
            alias = env.alias
            label = _ENV.get((env.name or "").lower(), env.name)
            cmd = f"{project} list --json --classify --mux-details"
            try:
                raw = subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=12", "-o", "BatchMode=yes",
                     alias, cmd],
                    capture_output=True, text=True, timeout=90).stdout
                data = json.loads(_strip_to_json(raw))
            except Exception as exc:  # noqa: BLE001 - best effort per machine
                print(f"  ! {alias}: {exc}", file=sys.stderr)
                continue
            wts = data.get("worktrees", []) or []
            if wts:
                dumps.append((entry.display_name, label, False, wts))
                print(f"  + {entry.display_name} {label}: {len(wts)}", file=sys.stderr)
    return _mark_local(dumps, local)


def _mark_local(dumps: list, local: str | None) -> list:
    """Mark one dump as local, and reorder so the local *machine* sorts first
    (its codename becomes the primary one, e.g. "Nova")."""
    if not dumps:
        return dumps
    idx = 0
    if local:
        for i, (m, e, _l, _w) in enumerate(dumps):
            if local.lower() in f"{m} {e}".lower():
                idx = i
                break
    dumps[idx] = (dumps[idx][0], dumps[idx][1], True, dumps[idx][3])
    local_machine = dumps[idx][0]
    first = [d for d in dumps if d[0] == local_machine]
    rest = [d for d in dumps if d[0] != local_machine]
    return first + rest


# ---- rasterize --------------------------------------------------------------

def find_browser() -> str | None:
    for b in _BROWSERS:
        p = shutil.which(b)
        if p:
            return p
    for p in _WIN_BROWSERS:
        if os.path.exists(p):
            return p
    return None


def _viewbox(svg: str) -> tuple[int, int]:
    import re
    m = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg)
    if not m:
        return (1400, 1000)
    return (int(float(m.group(1))), int(float(m.group(2))))


def svg_to_png(svg: str, out_png: str, browser: str, scale: int = 2) -> None:
    w, h = _viewbox(svg)
    w2, h2 = w * scale, h * scale
    sized = svg.replace('<svg class="rich-terminal"',
                        f'<svg class="rich-terminal" width="{w2}" height="{h2}"')
    html = ("<!doctype html><html><head><meta charset='utf-8'>"
            "<style>html,body{margin:0;padding:0;background:#0c0c0c;"
            "overflow:hidden}</style></head><body>" + sized + "</body></html>")
    with tempfile.TemporaryDirectory() as td:
        wrap = os.path.join(td, "wrap.html")
        with open(wrap, "w", encoding="utf-8") as fh:
            fh.write(html)
        subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
             "--virtual-time-budget=3000", f"--screenshot={out_png}",
             f"--window-size={w2},{h2}", "file:///" + wrap.replace("\\", "/")],
            capture_output=True, timeout=90)


# ---- walkthrough (animation) ------------------------------------------------

# A scripted keyboard tour: cycle the view pivot, drop into the list, arrow
# through a few worktrees, open a row action sub-menu, then dismiss it.
WALKTHROUGH: list[list[str] | None] = [
    None, ["]"], ["["], ["down"], ["down"], ["down"],
    ["down"], ["enter"], ["down"], ["escape"],
]


def make_gif(frames_png: list[str], out_gif: str, ms: int = 900) -> bool:
    try:
        from PIL import Image
    except ImportError:
        print("! Pillow not installed; cannot assemble GIF. "
              "Install with: pip install pillow", file=sys.stderr)
        return False
    imgs = [Image.open(p).convert("RGB") for p in frames_png]
    if not imgs:
        return False
    imgs[0].save(out_gif, save_all=True, append_images=imgs[1:],
                 duration=ms, loop=0, optimize=True)
    return True


# ---- main -------------------------------------------------------------------

def _size(s: str) -> tuple[int, int]:
    w, _, h = s.lower().partition("x")
    return (int(w), int(h))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Shareable Worktree Picker screenshot")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-dir", help="Directory of `list --json` dump files")
    src.add_argument("--gather", action="store_true",
                     help="Gather live across the machine roster over SSH")
    ap.add_argument("--project", default="agent-worktrees",
                    help="Project binstub name (for --gather)")
    ap.add_argument("--repo-dir", default=os.getcwd(),
                    help="Repo dir to resolve the roster (for --gather)")
    ap.add_argument("--out", required=True, help="Output file (.svg/.png/.gif)")
    ap.add_argument("--view", choices=["all", "local"], default="all")
    ap.add_argument("--size", type=_size, default=(144, 40),
                    help="Grid WxH, e.g. 144x40")
    ap.add_argument("--cap", type=int, default=6,
                    help="Max worktrees per machine/env (readability)")
    ap.add_argument("--raw", action="store_true",
                    help="Do NOT obscure -- render the real names as-is")
    ap.add_argument("--repo", default="my-project", help="Obscured repo label")
    ap.add_argument("--branch", default="main", help="Obscured branch label")
    ap.add_argument("--titles", help="File of newline-separated demo titles")
    ap.add_argument("--local", help="Substring of the machine/env to treat as local")
    ap.add_argument("--animate", action="store_true",
                    help="Capture a scripted walkthrough and write a GIF")
    args = ap.parse_args(argv)

    print("Gathering...", file=sys.stderr)
    dumps = (gather_from_dir(args.from_dir, args.local) if args.from_dir
             else gather_live(args.repo_dir, args.project, args.local))
    if not dumps:
        print("No worktree data gathered.", file=sys.stderr)
        return 2
    n = sum(len(w) for _m, _e, _l, w in dumps)
    print(f"  {n} worktrees across {len(dumps)} machine/env(s)", file=sys.stderr)

    if args.raw:
        # A minimal pass-through source over the real (unobscured) records.
        from agent_worktrees.picker_tui import derive
        import types
        import datetime
        derive.NOW = datetime.datetime.now()
        desc, recs, local = [], [], None
        for m, e, is_local, wts in dumps:
            desc.append((f"{m} {e}", m, e, True))
            if is_local and local is None:
                local = (m, e)
            recs += [derive.norm(w, m, e) for w in wts]
        local = local or (desc[0][1], desc[0][2])
        source = types.SimpleNamespace(
            LOCAL=local, LOCAL_LABEL=f"{local[0]} \u00b7 {local[1].lower()}",
            REPO="", BRANCH="", machines=lambda: desc, bucket=derive.bucket,
            for_machine=derive.for_machine, load=lambda: recs)
    else:
        titles = None
        if args.titles:
            titles = [ln.strip() for ln in open(args.titles, encoding="utf-8")
                      if ln.strip()]
        source = pobs.obscured_source(
            dumps, repo=args.repo, branch=args.branch, titles=titles,
            per_source_cap=args.cap)

    settle = pobs.settle_seconds(dumps)
    browser = find_browser()

    if args.animate:
        print("Capturing walkthrough frames...", file=sys.stderr)
        import asyncio
        frames = asyncio.run(pcap.capture_frames_async(
            source, WALKTHROUGH, view=args.view, size=args.size, settle=settle,
            update_state="current"))
        if not browser:
            print("! No Chromium-family browser found; cannot rasterize GIF.",
                  file=sys.stderr)
            return 3
        with tempfile.TemporaryDirectory() as td:
            pngs = []
            for i, fr in enumerate(frames):
                p = os.path.join(td, f"f{i:02d}.png")
                svg_to_png(fr["svg"], p, browser)
                pngs.append(p)
            ok = make_gif(pngs, args.out)
        print(("Wrote " if ok else "Failed to write ") + args.out, file=sys.stderr)
        return 0 if ok else 3

    caps = pcap.capture(source, view=args.view, size=args.size, settle=settle,
                        update_state="current")
    ext = os.path.splitext(args.out)[1].lower()
    if ext == ".svg":
        open(args.out, "w", encoding="utf-8").write(caps["svg"])
    elif ext in (".txt", ".ansi"):
        open(args.out, "w", encoding="utf-8", newline="\n").write(
            caps["ansi" if ext == ".ansi" else "text"])
    else:  # .png (default)
        if not browser:
            print("! No Chromium-family browser found; writing SVG instead.",
                  file=sys.stderr)
            open(os.path.splitext(args.out)[0] + ".svg", "w",
                 encoding="utf-8").write(caps["svg"])
            return 3
        svg_to_png(caps["svg"], args.out, browser)
    print("Wrote " + args.out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
