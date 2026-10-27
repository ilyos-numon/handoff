"""One-line demo launcher: ``uv run handoff-demo`` / ``make demo``."""

import argparse
import asyncio
import os
import socket
import struct

import uvicorn

DEFAULT_PORT = 8780
DEFAULT_DATA_DIR = "./data/demo"
APP = "handoff.main:create_app"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="handoff-demo", description="Run the handoff demo server.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", DEFAULT_PORT)))
    parser.add_argument(
        "--data-dir", default=os.environ.get("HANDOFF_DATA_DIR", DEFAULT_DATA_DIR)
    )
    parser.add_argument("--public", action="store_true", help="bind every IPv4 interface")
    return parser.parse_args(argv)


def _config(port: int, public: bool) -> uvicorn.Config:
    if public:
        host = socket.inet_ntoa(struct.pack("!I", socket.INADDR_ANY))
        return uvicorn.Config(
            APP,
            factory=True,
            host=host,
            port=port,
            workers=1,
            log_level="warning",
            proxy_headers=True,
            forwarded_allow_ips="*",
        )
    return uvicorn.Config(
        APP,
        factory=True,
        host="localhost",
        port=port,
        workers=1,
        log_level="warning",
        proxy_headers=False,
    )


async def _serve(server: uvicorn.Server, port: int) -> bool:
    serving = asyncio.ensure_future(server.serve())
    announced = False
    while not serving.done():
        if not announced and server.started:
            print(f"데모 주소: http://localhost:{port}/demo", flush=True)
            announced = True
        await asyncio.sleep(0.05)
    serving.result()
    return announced


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    os.environ.setdefault("HANDOFF_DEMO", "on")
    os.environ.setdefault("HANDOFF_AUTH", "on")
    os.environ.setdefault("HANDOFF_PROVIDER", "replay")
    os.environ["HANDOFF_DATA_DIR"] = str(args.data_dir)
    if args.public:
        os.environ.setdefault("HANDOFF_TRUSTED_PROXIES", "*")
    server = uvicorn.Server(_config(args.port, args.public))
    announced = asyncio.run(_serve(server, args.port))
    return 0 if announced else 1
