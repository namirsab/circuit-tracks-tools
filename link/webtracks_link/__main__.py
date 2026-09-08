"""Run the relay: ``python -m webtracks_link`` or ``webtracks-link``."""

from __future__ import annotations

import argparse
import logging
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Web Tracks Agent Link relay")
    parser.add_argument("--host", default=os.environ.get("LINK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LINK_PORT", "8770")))
    parser.add_argument(
        "--public-url",
        default=os.environ.get("LINK_PUBLIC_URL"),
        help="Base URL MCP clients reach this relay at (behind a proxy). Defaults to the request's own host.",
    )
    parser.add_argument("--log-level", default=os.environ.get("LINK_LOG_LEVEL", "info"))
    args = parser.parse_args()
    if args.public_url:
        os.environ["LINK_PUBLIC_URL"] = args.public_url
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run("webtracks_link.server:app", host=args.host, port=args.port, log_level=args.log_level, ws="websockets")


if __name__ == "__main__":
    main()
