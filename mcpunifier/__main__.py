"""Run the unifier: ``python -m mcpunifier``."""

import logging

import uvicorn

from mcpunifier.config import load_settings
from mcpunifier.logging import configure_logging
from mcpunifier.server import McpTrailingSlashMiddleware, create_app

logger = logging.getLogger(__name__)


def main() -> int:
    # Config is read before logging is configured, so a ConfigError surfaces as
    # a traceback on stderr. That is the clearest signal available at this
    # point, and there is nothing to route to without it.
    settings = load_settings()

    configure_logging(settings.log_level, settings.log_file)
    app = McpTrailingSlashMiddleware(create_app(settings))

    uvicorn.run(
        app,
        host=settings.listen_host,
        port=settings.listen_port,
        log_config=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
