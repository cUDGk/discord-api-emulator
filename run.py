"""Entry point: python run.py"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow `python run.py` without install.
sys.path.insert(0, str(Path(__file__).parent / "src"))

import uvicorn

from dapi_emu import config
from dapi_emu.app import create_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    app = create_app()
    print(f">> dapi-emu listening on http://{config.HOST}:{config.PORT}")
    print(f"   REST       : http://{config.HOST}:{config.PORT}/api/v{config.API_VERSION}")
    print(f"   Gateway WS : {config.GATEWAY_WS_URL}")
    print(f"   Panel      : http://{config.HOST}:{config.PORT}/panel")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")


if __name__ == "__main__":
    main()
