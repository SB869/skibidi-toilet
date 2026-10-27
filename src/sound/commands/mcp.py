"""sound mcp: serve the engine to an MCP client (Claude Code etc.) over stdio."""

from __future__ import annotations

import argparse
from pathlib import Path


def add_parser(sub) -> None:
    from ..server import DEFAULT_ROOT

    parser = sub.add_parser("mcp", help="serve the engine as MCP tools (stdio)",
                            description="Expose analyze / tune / produce / compare / timing as MCP tools over "
                            "stdio, for Claude Code or any MCP client.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="library folder: takes, reference/, tuned/ (default: %(default)s)")
    parser.set_defaults(run=run)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    from ..mcp_server import main
    main(args.root)
    return 0
