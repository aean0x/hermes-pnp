"""mcp-proxy entrypoint: load declarative JSON config and serve."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Declarative MCP reverse proxy")
    parser.add_argument("--config", required=True, help="JSON config path")
    parser.add_argument("--listen", help="Override listen host:port")
    parser.add_argument(
        "--credentials-dir",
        default=os.environ.get("CREDENTIALS_DIRECTORY"),
        help="systemd CredentialsDirectory (or set CREDENTIALS_DIRECTORY)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s mcp-proxy %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    listen = args.listen or cfg.get("listen") or "127.0.0.1:3140"
    backends = cfg.get("backends") or {}
    if not backends:
        logging.error("config has no backends")
        return 2

    from http_proxy import serve

    httpd = serve(listen, backends, args.credentials_dir)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
