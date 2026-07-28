"""Terminal routing table, read from the same config.yaml nginx is generated from.

Reading the one file that already drives nginx means the unifier cannot route
somewhere nginx does not, and adding a terminal stays a single-file edit.

Resolution happens once at startup and is never re-probed: a terminal that is
down is still a *configured* terminal, and discovering that costs one failed
call rather than a service that refuses to start.
"""

import logging
import os
from dataclasses import dataclass
from typing import Any

import yaml

from mcpunifier.constants import (
    DEFAULT_INSTANCE,
    DEFAULT_LISTEN_HOST,
    DEFAULT_LISTEN_PORT,
    DEFAULT_LOG_FILE,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MT5_HOST,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ENV_API_TOKEN,
    ENV_LISTEN_HOST,
    ENV_LISTEN_PORT,
    ENV_LOG_FILE,
    ENV_LOG_LEVEL,
    ENV_MT5_HOST,
    ENV_REQUEST_TIMEOUT,
)
from mcpunifier.errors import ConfigError

logger = logging.getLogger(__name__)

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_PACKAGE_DIR)
CONFIG_PATH = os.path.join(_BASE_DIR, "config", "config.yaml")


@dataclass(frozen=True)
class Terminal:
    """One MT5 terminal: a broker/account/instance triple behind its own port."""

    broker: str
    account: str
    instance: str
    port: int
    mode: str

    @property
    def key(self) -> str:
        """The routing key, matching the nginx path segment for this terminal."""
        return f"{self.broker}/{self.account}/{self.instance}"

    def base_url(self, host: str) -> str:
        """Base URL of this terminal's own mt5api process."""
        return f"http://{host}:{self.port}"


@dataclass(frozen=True)
class Settings:
    """Everything the service needs, resolved once at startup."""

    terminals: dict[str, Terminal]
    mt5_host: str
    api_token: str
    request_timeout: float
    listen_host: str
    listen_port: int
    log_level: str
    log_file: str

    def terminal_keys(self) -> list[str]:
        """Configured routing keys, sorted for stable output."""
        return sorted(self.terminals)


def _normalize_instance(value: Any) -> str:
    if value in (None, ""):
        return DEFAULT_INSTANCE
    return str(value).strip() or DEFAULT_INSTANCE


def _load_yaml(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except OSError as err:
        raise ConfigError(f"cannot read config at {path}") from err
    except yaml.YAMLError as err:
        raise ConfigError(f"config at {path} is not valid YAML") from err


def _build_terminals(raw: list[Any]) -> dict[str, Terminal]:
    terminals: dict[str, Terminal] = {}
    skipped = 0

    for entry in raw:
        if not isinstance(entry, dict):
            skipped += 1
            continue

        broker = str(entry.get("broker", "")).strip()
        account = str(entry.get("account", "")).strip()
        port = entry.get("port")
        if not broker or not account or not port:
            logger.warning(
                "skipping terminal with incomplete definition",
                extra={"reason": "missing_broker_account_or_port"},
            )
            skipped += 1
            continue

        terminal = Terminal(
            broker=broker,
            account=account,
            instance=_normalize_instance(entry.get("instance")),
            port=int(port),
            mode=str(entry.get("mode", "") or "unknown"),
        )
        terminals[terminal.key] = terminal
        logger.debug(
            "terminal registered",
            extra={
                "terminal": terminal.key,
                "port": terminal.port,
                "mode": terminal.mode,
            },
        )

    logger.info(
        "terminal routing table built",
        extra={"registered": len(terminals), "skipped": skipped},
    )
    return terminals


def load_settings(path: str = CONFIG_PATH) -> Settings:
    """Read config.yaml plus env overrides into a resolved Settings.

    Raises ConfigError when the file is unreadable or defines no usable
    terminal — a unifier with nothing to route to would only fail later, one
    confusing tool call at a time.
    """
    raw = _load_yaml(path)
    terminals = _build_terminals(raw.get("terminals") or [])
    if not terminals:
        raise ConfigError(f"no usable terminals defined in {path}")

    return Settings(
        terminals=terminals,
        mt5_host=os.environ.get(ENV_MT5_HOST, DEFAULT_MT5_HOST),
        api_token=os.environ.get(ENV_API_TOKEN, "")
        or str(raw.get("api_token", "") or ""),
        request_timeout=float(
            os.environ.get(ENV_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT_SECONDS)
        ),
        listen_host=os.environ.get(ENV_LISTEN_HOST, DEFAULT_LISTEN_HOST),
        listen_port=int(os.environ.get(ENV_LISTEN_PORT, DEFAULT_LISTEN_PORT)),
        log_level=os.environ.get(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL),
        log_file=os.environ.get(ENV_LOG_FILE, DEFAULT_LOG_FILE),
    )


def resolve(settings: Settings, broker: str, account: str, instance: str) -> Terminal:
    """Look up one terminal, or raise UnknownTerminal listing the valid keys."""
    from mcpunifier.errors import UnknownTerminal

    key = f"{broker.strip()}/{account.strip()}/{_normalize_instance(instance)}"
    terminal = settings.terminals.get(key)
    if terminal is None:
        raise UnknownTerminal(key, settings.terminal_keys())
    return terminal
