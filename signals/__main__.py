"""Entry point: ``python -m signals``."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from signals.config import Settings, load_settings
from signals.constants import HEARTBEAT_SECONDS, SOURCE_TYPE_TELEGRAM, SOURCE_TYPE_WEBSOCKET
from signals.engine import Engine
from signals.errors import ConfigError
from signals.logging import configure_logging
from signals.sources.telegram import TelegramHub
from signals.sources.websocket import WebsocketSource
from signals.state import State
from signals.terminal import TerminalClient

logger = logging.getLogger("signals")

EXIT_CONFIG_ERROR = 2


async def _heartbeat(path: str) -> None:
    """Touch the file the container healthcheck reads, while the loop runs."""
    heartbeat = Path(path)
    while True:
        heartbeat.touch()
        await asyncio.sleep(HEARTBEAT_SECONDS)


async def run(settings: Settings) -> None:
    rules = settings.rules
    state = State(settings.state_path)
    client = TerminalClient(settings.mt5_url, settings.api_token)
    engine = Engine(rules, client, state)

    tasks = [_heartbeat(settings.heartbeat_path)]
    telegram_sources = [s for s in rules.sources.values() if s.type == SOURCE_TYPE_TELEGRAM]
    hub = None
    if telegram_sources:
        hub = TelegramHub(settings, telegram_sources, state, engine.handle)
        tasks.append(hub.run())
    for source in rules.sources.values():
        if source.type == SOURCE_TYPE_WEBSOCKET:
            tasks.append(WebsocketSource(source, engine.handle).run())

    logger.info(
        "signal copier started",
        extra={
            "sources": sorted(rules.sources),
            "targets": [f"{t.source}->{t.terminal}" for t in rules.targets],
            "dry_run": rules.dry_run,
        },
    )
    try:
        await asyncio.gather(*tasks)
    finally:
        if hub is not None:
            await hub.aclose()
        await client.aclose()
        state.close()


def main() -> int:
    configure_logging("info")
    try:
        settings = load_settings()
    except ConfigError as err:
        logger.error("invalid configuration, not starting", extra={"reason": str(err)})
        return EXIT_CONFIG_ERROR
    configure_logging(settings.log_level)
    asyncio.run(run(settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
