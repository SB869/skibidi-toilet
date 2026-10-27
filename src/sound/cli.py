"""Command-line entry points: `sound <command>` and the `autotune` shortcut."""

from __future__ import annotations

import argparse
import sys

from .commands import analyze, ardour, beat, compare, cut, daw, fx, mcp, serve, tune


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sound", description="Linux audio tools.")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for command in (tune, fx, cut, analyze, compare, beat, serve, mcp, daw, ardour):
        command.add_parser(sub)
    args = parser.parse_args(argv)
    return args.run(args, parser)


def autotune_main(argv: list[str] | None = None) -> int:
    """`autotune ...` is `sound tune ...`."""
    parser = argparse.ArgumentParser(prog="autotune", description="Pitch-correct vocal recordings.",
                                     epilog=tune.EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    tune.add_arguments(parser)
    return tune.run(parser.parse_args(argv), parser)


if __name__ == "__main__":
    sys.exit(main())
