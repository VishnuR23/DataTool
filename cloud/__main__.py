"""Run the hosted panel: ``python -m cloud`` (or the ``datatool-cloud`` script).

Reads ``CloudSettings`` from the environment / ``.env`` (prefix ``DATATOOL_CLOUD_``),
ensures the panel's schema exists, and serves over uvicorn. The panel is a separate
service from the agent daemon (``datatool daemon``) with its own database and port.
"""

from __future__ import annotations

import uvicorn

from cloud.app import build_serving_app
from cloud.config import get_cloud_settings


def main() -> None:
    settings = get_cloud_settings()
    app = build_serving_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
