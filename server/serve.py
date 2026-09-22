# Clio — Copyright (C) 2026 Sean Rago
# Licensed under the GNU Affero General Public License v3.0 or later.
# See LICENSE in the project root, or <https://www.gnu.org/licenses/>.

"""Runs the Clio FastAPI app on two listeners in one process:

  - the public-facing one (whatever host/port/TLS start.sh was already passing
    to plain uvicorn — Tailscale-certified TLS on 0.0.0.0, or plain HTTP on
    127.0.0.1 behind a Cloudflare tunnel)
  - a local-only, unencrypted one, always on 127.0.0.1:LOCAL_PORT, for
    same-machine clients like the display

Both serve the same `app` object in the same process, so in-memory state
(SESSION_TOKEN, display_link, diff_manager) is identical across both — the
display doesn't need Tailscale (or a public tunnel) just to reach a
server running on the same machine.
"""

import argparse
import asyncio
import sys

import uvicorn

from server.main import app

LOCAL_HOST = "127.0.0.1"
LOCAL_PORT = 8766


async def _serve(host: str, port: int, ssl_certfile: str | None, ssl_keyfile: str | None) -> None:
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        log_level="warning",
        # main.py already redirects stdout/stderr through its own file+console
        # logger at import time (before this Config is built) — uvicorn's
        # default logging setup probes sys.stdout.isatty(), which that
        # redirect doesn't implement. Not needed anyway; skip it.
        log_config=None,
    )
    # Server.serve() wraps itself in capture_signals(), which installs a
    # SIGINT/SIGTERM handler — fine for one server, but the second one to
    # start would clobber the first's handler since both run in this same
    # process. Call the private _serve() directly to skip that; Ctrl+C then
    # falls through to Python's default handler, which asyncio.run() turns
    # into a KeyboardInterrupt in _main below.
    await uvicorn.Server(config)._serve()


async def _main(host: str, port: int, ssl_certfile: str | None, ssl_keyfile: str | None) -> None:
    await asyncio.gather(
        _serve(host, port, ssl_certfile, ssl_keyfile),
        _serve(LOCAL_HOST, LOCAL_PORT, None, None),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ssl-certfile")
    parser.add_argument("--ssl-keyfile")
    args = parser.parse_args()

    try:
        asyncio.run(_main(args.host, args.port, args.ssl_certfile, args.ssl_keyfile))
    except KeyboardInterrupt:
        pass
    sys.exit(0)
