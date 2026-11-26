"""Run the HealthCore MCP server (Streamable HTTP) with uvicorn.

    python -m mcps.healthcore_mcp

MCP_HOST (default 127.0.0.1) and MCP_PORT (default 8765); the MCP endpoint is /mcp.
"""

import logging
import os

import uvicorn

from .server import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(create_app(), host=os.environ.get("MCP_HOST", "127.0.0.1"), port=int(os.environ.get("MCP_PORT", "8765")))


if __name__ == "__main__":
    main()
