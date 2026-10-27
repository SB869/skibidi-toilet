"""sound serve: run the web interface locally."""

from __future__ import annotations

import argparse
import threading
import webbrowser
from pathlib import Path


def add_parser(sub) -> None:
    from ..server import DEFAULT_ROOT

    parser = sub.add_parser("serve", help="open the web interface",
                            description="Run the web interface on this machine (localhost only).")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help="workspace folder: takes, reference/, tuned/ (default: %(default)s)")
    parser.add_argument("--port", type=int, default=8765, help="(default: %(default)s)")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    parser.set_defaults(run=run)


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    import uvicorn

    from ..server import DEFAULT_WEB, create_app

    app = create_app(args.root, DEFAULT_WEB)
    url = f"http://127.0.0.1:{args.port}"
    print(f"sound: {url}  (workspace {args.root.resolve()}; Ctrl-C to stop)")
    if not (DEFAULT_WEB / "index.html").exists():
        print("  the web interface isn't built yet: cd web && npm install && npm run build")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0
