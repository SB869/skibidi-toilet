"""sound daw: open Ardour straight into a session, MCP on, no startup dialogs."""

from __future__ import annotations

import argparse
import sys

from .. import daw


def add_parser(sub) -> None:
    parser = sub.add_parser("daw", help="open Ardour in a session with its MCP server on",
                            description="Launch Ardour (Flatpak) into a session from sessions/, with the MCP "
                            "HTTP server, OSC and the sound bridge enabled and the startup dialogs skipped.")
    parser.add_argument("session", nargs="?", default="friday", help="session name in sessions/ (default: %(default)s)")
    parser.add_argument("--new", action="store_true", help="create the session first")
    parser.add_argument("--rate", type=int, default=48000, help="sample rate for --new (default: %(default)s)")
    parser.add_argument("--buffer", type=int, metavar="SAMPLES",
                        help="engine buffer size, e.g. 128 for playing an instrument live (2.7 ms at 48 kHz); "
                        "kept for next time")
    parser.add_argument("--no-wait", action="store_true", help="don't wait for the MCP server to come up")
    parser.set_defaults(run=run)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    try:
        opened = daw.open_session(args.session, new=args.new, rate=args.rate, wait=not args.no_wait,
                                  buffer=args.buffer)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    for note in opened["notes"]:
        print(f"note: {note}")
    if opened["launched"]:
        print(f"opened {opened['session']} in Ardour")
    else:
        print("Ardour is already open; open the session from Session > Recent if it isn't showing.")
    if opened["outcome"] == "mcp":
        print(f"Ardour's MCP server is up at {daw.MCP_URL}")
        print(f"OSC is {'listening on' if opened['osc'] else 'not up on'} UDP {daw.OSC_PORT}")
    if opened.get("buffer"):
        print(f"engine buffer: {opened['buffer']} samples ({opened['buffer'] / args.rate * 1000:.1f} ms a cycle)")
    elif opened["outcome"] == "waiting":
        print(f"Ardour's MCP server didn't answer yet (is a dialog waiting for you?) Log: {daw.LOG}")
    return 0
