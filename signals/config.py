"""Settings from config/config.yaml and the rules in config/signals.yml.

Everything is checked once at startup and any mistake stops the service with
a ConfigError naming it. A copier that starts with a typo in a regex, a
sizing mode or a target's terminal would only show it later as a trade that
never happened, or one that happened on the wrong account.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import yaml

from signals.constants import (
    DEFAULT_DEVIATION,
    DEFAULT_HEARTBEAT_PATH,
    DEFAULT_INSTANCE,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_SIGNAL_AGE_SECONDS,
    DEFAULT_MT5_URL,
    DEFAULT_POLL_SECONDS,
    DEFAULT_STATE_PATH,
    DEFAULT_TELETHON_URL,
    ENV_API_TOKEN,
    ENV_HEARTBEAT_PATH,
    ENV_LOG_LEVEL,
    ENV_MT5_URL,
    ENV_STATE_PATH,
    ENV_TELETHON_AUTH_KEY,
    ENV_TELETHON_URL,
    MAX_LEGS,
    MIN_POLL_SECONDS,
    NETTING_TP_CHOICES,
    NETTING_TP_FIRST,
    SIDES,
    SIZING_FIXED,
    SIZING_MODES,
    SIZING_MULTIPLIER,
    SIZING_RISK_PERCENT,
    SOURCE_NAME_RE,
    SOURCE_TYPE_TELEGRAM,
    SOURCE_TYPES,
)
from signals.errors import ConfigError

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_BASE_DIR = os.path.dirname(_PACKAGE_DIR)
CONFIG_PATH = os.path.join(_BASE_DIR, "config", "config.yaml")
SIGNALS_PATH = os.path.join(_BASE_DIR, "config", "signals.yml")

_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class Parser:
    """Compiled regexes for one message format."""

    name: str
    open: re.Pattern[str]
    sl: re.Pattern[str] | None
    tp: re.Pattern[str] | None
    lots: re.Pattern[str] | None
    side_map: dict[str, str]


@dataclass(frozen=True)
class Source:
    """One feed of signal messages."""

    name: str
    type: str
    parser: str
    magic: int
    chat: str = ""
    poll_seconds: float = DEFAULT_POLL_SECONDS
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    text_field: str = ""
    id_field: str = ""


@dataclass(frozen=True)
class Sizing:
    mode: str
    lots: Decimal = Decimal(0)
    factor: Decimal = Decimal(0)
    default_lots: Decimal | None = None
    percent: Decimal = Decimal(0)


@dataclass(frozen=True)
class Limits:
    """Caps on open signals for one target. None means no cap."""

    max_open_per_symbol: int | None = None
    max_open_per_source: int | None = None
    max_open_per_source_symbol: int | None = None
    count_manual: bool = False


@dataclass(frozen=True)
class Target:
    """One account that copies one source."""

    source: str
    broker: str
    account: str
    instance: str
    sizing: Sizing
    limits: Limits
    tp_split: tuple[Decimal, ...] = ()
    max_lots: Decimal | None = None
    netting_tp: str = NETTING_TP_FIRST
    symbol_suffix: str = ""
    symbols: dict[str, str] = field(default_factory=dict)
    require_sl: bool = True
    max_entry_slippage_points: Decimal | None = None
    deviation: int = DEFAULT_DEVIATION
    type_filling: str = ""
    dry_run: bool = False

    @property
    def terminal(self) -> str:
        return f"{self.broker}/{self.account}/{self.instance}"


@dataclass(frozen=True)
class Rules:
    """Everything in signals.yml."""

    sources: dict[str, Source]
    parsers: dict[str, Parser]
    symbols: dict[str, str]
    targets: tuple[Target, ...]
    dry_run: bool
    max_signal_age_seconds: float

    def targets_for(self, source: str) -> list[Target]:
        return [target for target in self.targets if target.source == source]

    def magics(self) -> frozenset[int]:
        return frozenset(source.magic for source in self.sources.values())

    def has_type(self, source_type: str) -> bool:
        return any(source.type == source_type for source in self.sources.values())


@dataclass(frozen=True)
class Settings:
    """Runtime settings, resolved once at startup."""

    rules: Rules
    mt5_url: str
    api_token: str
    telethon_url: str
    telethon_auth_key: str
    state_path: str
    heartbeat_path: str
    log_level: str


def _load_yaml(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except OSError as err:
        raise ConfigError(f"cannot read {path}") from err
    except yaml.YAMLError as err:
        raise ConfigError(f"{path} is not valid YAML") from err
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must hold a mapping at the top level")
    return data


def _mapping(raw: Any, where: str) -> dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a mapping")
    return raw


def _decimal(raw: Any, where: str, positive: bool = True) -> Decimal:
    if isinstance(raw, bool):
        raise ConfigError(f"{where} must be a number")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError) as err:
        raise ConfigError(f"{where} must be a number") from err
    if not value.is_finite() or (positive and value <= 0):
        raise ConfigError(f"{where} must be a number above 0")
    return value


def _optional_decimal(raw: Any, where: str) -> Decimal | None:
    if raw is None:
        return None
    return _decimal(raw, where)


def _positive_int(raw: Any, where: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ConfigError(f"{where} must be a whole number above 0")
    return raw


def _optional_int(raw: Any, where: str) -> int | None:
    if raw is None:
        return None
    return _positive_int(raw, where)


def _string(raw: Any, where: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{where} must be a non-empty string")
    return raw.strip()


def _bool(raw: Any, where: str, default: bool) -> bool:
    if raw is None:
        return default
    if not isinstance(raw, bool):
        raise ConfigError(f"{where} must be true or false")
    return raw


def _expand_env(value: str, where: str) -> str:
    """Replace ${NAME} with the environment variable, so header secrets stay
    out of signals.yml."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise ConfigError(f"{where} refers to ${{{name}}}, which is not set")
        return os.environ[name]

    return _ENV_REF_RE.sub(_sub, value)


def _compile(raw: Any, where: str, required_groups: set[str]) -> re.Pattern[str]:
    pattern = _string(raw, where)
    try:
        compiled = re.compile(pattern)
    except re.error as err:
        raise ConfigError(f"{where} is not a valid regex: {err}") from err
    missing = required_groups - set(compiled.groupindex)
    if missing:
        groups = ", ".join(sorted(missing))
        raise ConfigError(f"{where} needs the named group(s) {groups}, as in (?P<name>...)")
    return compiled


def _optional_compile(raw: Any, where: str, required_groups: set[str]) -> re.Pattern[str] | None:
    if raw is None:
        return None
    return _compile(raw, where, required_groups)


def _build_parser(name: str, raw: Any) -> Parser:
    where = f"parsers.{name}"
    block = _mapping(raw, where)
    side_map_raw = _mapping(block.get("side_map"), f"{where}.side_map")
    side_map = {"buy": "buy", "sell": "sell"}
    for word, side in side_map_raw.items():
        side_text = str(side).strip().lower()
        if side_text not in SIDES:
            raise ConfigError(f"{where}.side_map.{word} must be buy or sell")
        side_map[str(word).strip().lower()] = side_text
    return Parser(
        name=name,
        open=_compile(block.get("open"), f"{where}.open", {"side", "symbol"}),
        sl=_optional_compile(block.get("sl"), f"{where}.sl", {"price"}),
        tp=_optional_compile(block.get("tp"), f"{where}.tp", {"price"}),
        lots=_optional_compile(block.get("lots"), f"{where}.lots", {"lots"}),
        side_map=side_map,
    )


def _build_source(name: str, raw: Any, parsers: dict[str, Parser]) -> Source:
    where = f"sources.{name}"
    if not SOURCE_NAME_RE.match(name):
        raise ConfigError(
            f"{where}: a source name is 1 to 16 letters, digits or '._-' "
            "(it becomes part of every order's client_order_id)"
        )
    block = _mapping(raw, where)
    source_type = str(block.get("type", "")).strip().lower()
    if source_type not in SOURCE_TYPES:
        types = ", ".join(sorted(SOURCE_TYPES))
        raise ConfigError(f"{where}.type must be one of {types}")
    parser = _string(block.get("parser"), f"{where}.parser")
    if parser not in parsers:
        raise ConfigError(f"{where}.parser names '{parser}', which parsers does not define")
    magic = _positive_int(block.get("magic"), f"{where}.magic")

    if source_type == SOURCE_TYPE_TELEGRAM:
        poll_seconds = float(
            _decimal(block.get("poll_seconds", DEFAULT_POLL_SECONDS), f"{where}.poll_seconds")
        )
        if poll_seconds < MIN_POLL_SECONDS:
            raise ConfigError(f"{where}.poll_seconds must be at least {MIN_POLL_SECONDS:g}")
        return Source(
            name=name,
            type=source_type,
            parser=parser,
            magic=magic,
            chat=_string(str(block.get("chat") or ""), f"{where}.chat"),
            poll_seconds=poll_seconds,
        )

    url = _string(block.get("url"), f"{where}.url")
    if not url.startswith(("ws://", "wss://")):
        raise ConfigError(f"{where}.url must start with ws:// or wss://")
    headers = {
        str(key): _expand_env(str(value), f"{where}.headers.{key}")
        for key, value in _mapping(block.get("headers"), f"{where}.headers").items()
    }
    return Source(
        name=name,
        type=source_type,
        parser=parser,
        magic=magic,
        url=_expand_env(url, f"{where}.url"),
        headers=headers,
        text_field=str(block.get("text_field", "") or "").strip(),
        id_field=str(block.get("id_field", "") or "").strip(),
    )


def _build_sizing(raw: Any, where: str) -> Sizing:
    block = _mapping(raw, where)
    mode = str(block.get("mode", "")).strip().lower()
    if mode not in SIZING_MODES:
        modes = ", ".join(sorted(SIZING_MODES))
        raise ConfigError(f"{where}.mode must be one of {modes}")
    if mode == SIZING_FIXED:
        return Sizing(mode=mode, lots=_decimal(block.get("lots"), f"{where}.lots"))
    if mode == SIZING_MULTIPLIER:
        return Sizing(
            mode=mode,
            factor=_decimal(block.get("factor"), f"{where}.factor"),
            default_lots=_optional_decimal(block.get("default_lots"), f"{where}.default_lots"),
        )
    percent = _decimal(block.get("percent"), f"{where}.percent")
    if percent > 100:
        raise ConfigError(f"{where}.percent must be at most 100")
    return Sizing(mode=SIZING_RISK_PERCENT, percent=percent)


def _build_limits(raw: Any, where: str) -> Limits:
    block = _mapping(raw, where)
    return Limits(
        max_open_per_symbol=_optional_int(
            block.get("max_open_per_symbol"), f"{where}.max_open_per_symbol"
        ),
        max_open_per_source=_optional_int(
            block.get("max_open_per_source"), f"{where}.max_open_per_source"
        ),
        max_open_per_source_symbol=_optional_int(
            block.get("max_open_per_source_symbol"), f"{where}.max_open_per_source_symbol"
        ),
        count_manual=_bool(block.get("count_manual"), f"{where}.count_manual", False),
    )


def _build_tp_split(raw: Any, where: str) -> tuple[Decimal, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{where} must be a list of percentages such as [50, 30, 20]")
    if len(raw) > MAX_LEGS:
        raise ConfigError(f"{where} has more than {MAX_LEGS} entries")
    return tuple(_decimal(item, f"{where}[{index}]") for index, item in enumerate(raw))


def _symbol_map(raw: Any, where: str) -> dict[str, str]:
    return {
        str(key).strip().upper(): _string(str(value), f"{where}.{key}")
        for key, value in _mapping(raw, where).items()
    }


def _build_target(
    index: int,
    raw: Any,
    sources: dict[str, Source],
    terminals: set[str],
) -> Target:
    where = f"targets[{index}]"
    block = _mapping(raw, where)
    source = _string(block.get("source"), f"{where}.source")
    if source not in sources:
        raise ConfigError(f"{where}.source names '{source}', which sources does not define")
    broker = _string(block.get("broker"), f"{where}.broker")
    account = _string(block.get("account"), f"{where}.account")
    instance = str(block.get("instance") or DEFAULT_INSTANCE).strip() or DEFAULT_INSTANCE
    key = f"{broker}/{account}/{instance}"
    if key not in terminals:
        known = ", ".join(sorted(terminals)) or "(none)"
        raise ConfigError(
            f"{where} names terminal {key}, which config.yaml does not define; configured: {known}"
        )

    netting_tp = str(block.get("netting_tp", NETTING_TP_FIRST)).strip().lower()
    if netting_tp not in NETTING_TP_CHOICES:
        raise ConfigError(f"{where}.netting_tp must be first or last")

    deviation = block.get("deviation", DEFAULT_DEVIATION)
    if isinstance(deviation, bool) or not isinstance(deviation, int) or deviation < 0:
        raise ConfigError(f"{where}.deviation must be a whole number of points, 0 or more")

    target = Target(
        source=source,
        broker=broker,
        account=account,
        instance=instance,
        sizing=_build_sizing(block.get("sizing"), f"{where}.sizing"),
        limits=_build_limits(block.get("limits"), f"{where}.limits"),
        tp_split=_build_tp_split(block.get("tp_split"), f"{where}.tp_split"),
        max_lots=_optional_decimal(block.get("max_lots"), f"{where}.max_lots"),
        netting_tp=netting_tp,
        symbol_suffix=str(block.get("symbol_suffix", "") or ""),
        symbols=_symbol_map(block.get("symbols"), f"{where}.symbols"),
        require_sl=_bool(block.get("require_sl"), f"{where}.require_sl", True),
        max_entry_slippage_points=_optional_decimal(
            block.get("max_entry_slippage_points"), f"{where}.max_entry_slippage_points"
        ),
        deviation=deviation,
        type_filling=str(block.get("type_filling", "") or "").strip().upper(),
        dry_run=_bool(block.get("dry_run"), f"{where}.dry_run", False),
    )
    if target.sizing.mode == SIZING_RISK_PERCENT and not target.require_sl:
        raise ConfigError(f"{where}: sizing mode risk_percent needs require_sl: true")
    return target


def load_rules(path: str, terminals: set[str]) -> Rules:
    """Read and check signals.yml against the configured terminal keys."""
    raw = _load_yaml(path)
    parsers = {
        str(name): _build_parser(str(name), block)
        for name, block in _mapping(raw.get("parsers"), "parsers").items()
    }
    sources = {
        str(name): _build_source(str(name), block, parsers)
        for name, block in _mapping(raw.get("sources"), "sources").items()
    }
    if not sources:
        raise ConfigError(f"{path} defines no sources")

    magics: dict[int, str] = {}
    for source in sources.values():
        if source.magic in magics:
            raise ConfigError(
                f"sources.{source.name}.magic {source.magic} is already used by "
                f"sources.{magics[source.magic]}; each source needs its own"
            )
        magics[source.magic] = source.name

    targets_raw = raw.get("targets") or []
    if not isinstance(targets_raw, list):
        raise ConfigError("targets must be a list")
    targets = tuple(
        _build_target(index, block, sources, terminals)
        for index, block in enumerate(targets_raw)
    )
    if not targets:
        raise ConfigError(f"{path} defines no targets")

    max_age = _decimal(
        raw.get("max_signal_age_seconds", DEFAULT_MAX_SIGNAL_AGE_SECONDS),
        "max_signal_age_seconds",
    )
    return Rules(
        sources=sources,
        parsers=parsers,
        symbols=_symbol_map(raw.get("symbols"), "symbols"),
        targets=targets,
        dry_run=_bool(raw.get("dry_run"), "dry_run", False),
        max_signal_age_seconds=float(max_age),
    )


def terminal_keys(config: dict[str, Any]) -> set[str]:
    """broker/account/instance of every terminal in config.yaml."""
    keys: set[str] = set()
    for entry in config.get("terminals") or []:
        if not isinstance(entry, dict):
            continue
        broker = str(entry.get("broker", "")).strip()
        account = str(entry.get("account", "")).strip()
        if not broker or not account:
            continue
        instance = str(entry.get("instance") or DEFAULT_INSTANCE).strip() or DEFAULT_INSTANCE
        keys.add(f"{broker}/{account}/{instance}")
    return keys


def load_settings(config_path: str = CONFIG_PATH, signals_path: str = SIGNALS_PATH) -> Settings:
    """Read config.yaml, signals.yml and the environment into Settings."""
    config = _load_yaml(config_path)
    rules = load_rules(signals_path, terminal_keys(config))
    telethon_auth_key = os.environ.get(ENV_TELETHON_AUTH_KEY, "")
    if not telethon_auth_key:
        telegram = _mapping(config.get("telegram"), "telegram")
        telethon_auth_key = str(telegram.get("auth_key", "") or "")
    return Settings(
        rules=rules,
        mt5_url=os.environ.get(ENV_MT5_URL, DEFAULT_MT5_URL).rstrip("/"),
        api_token=os.environ.get(ENV_API_TOKEN, "") or str(config.get("api_token", "") or ""),
        telethon_url=os.environ.get(ENV_TELETHON_URL, DEFAULT_TELETHON_URL).rstrip("/"),
        telethon_auth_key=telethon_auth_key,
        state_path=os.environ.get(ENV_STATE_PATH, DEFAULT_STATE_PATH),
        heartbeat_path=os.environ.get(ENV_HEARTBEAT_PATH, DEFAULT_HEARTBEAT_PATH),
        log_level=os.environ.get(ENV_LOG_LEVEL, DEFAULT_LOG_LEVEL),
    )
