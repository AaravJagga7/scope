"""Entry point: ``python -m storefront [--port 8010]``."""

from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Bazaaro storefront + SCOPE-X Constraint Lab")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    uvicorn.run("storefront.app:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
