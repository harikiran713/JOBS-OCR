"""Backend entry point.

    python -m mmi --port 0 --data-dir <dir> [--parent-pid N]

Binds to 127.0.0.1 only. With ``--port 0`` the OS picks a free port, which is
printed as ``MMI_READY port=<n>`` on stdout once the server accepts requests;
the Electron main process waits for that line. The API token is read from the
``MMI_API_TOKEN`` environment variable (never from the command line, which
other users can see in the process list).
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import multiprocessing
import os
import socket
import sys
import threading
import time
from pathlib import Path


def _watch_parent(pid: int) -> None:
    import psutil

    while True:
        if not psutil.pid_exists(pid):
            logging.getLogger("mmi").warning("Parent process %s exited; shutting down backend", pid)
            os._exit(0)
        time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(prog="mmi")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=str, default=None)
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    from .config import get_paths, set_paths

    paths = set_paths(Path(args.data_dir)) if args.data_dir else get_paths()
    handler = logging.handlers.RotatingFileHandler(paths.logs / "backend.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler, logging.StreamHandler(sys.stderr)],
    )
    log = logging.getLogger("mmi")

    from .db.session import init_engine

    try:
        init_engine(paths.db, paths.backups)
    except RuntimeError as exc:
        print(f"MMI_FATAL {exc}", flush=True)
        log.error("%s", exc)
        sys.exit(2)

    import uvicorn

    from .api.app import create_app

    if args.parent_pid:
        threading.Thread(target=_watch_parent, args=(args.parent_pid,), daemon=True).start()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]

    config = uvicorn.Config(create_app(), log_level="warning", access_log=False, lifespan="on")
    server = uvicorn.Server(config)

    def announce() -> None:
        while not server.started:
            time.sleep(0.05)
        print(f"MMI_READY port={port}", flush=True)
        log.info("Backend ready on 127.0.0.1:%s (data: %s)", port, paths.root)

    threading.Thread(target=announce, daemon=True).start()
    server.run(sockets=[sock])


if __name__ == "__main__":
    multiprocessing.freeze_support()  # required for the PyInstaller-packaged backend
    main()
