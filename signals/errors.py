"""Typed errors for the signal copier."""


class SignalsError(Exception):
    """Base class for every error raised by this service."""


class ConfigError(SignalsError):
    """config.yaml or signals.yml is unreadable or invalid. Raised at startup,
    before anything connects or trades."""


class TerminalUnreachable(SignalsError):
    """A terminal did not answer. Fails that target only."""

    def __init__(self, terminal: str, reason: str) -> None:
        self.terminal = terminal
        self.reason = reason
        super().__init__(f"terminal '{terminal}' unreachable: {reason}")


class TerminalRejected(SignalsError):
    """A terminal answered with a non-2xx status."""

    def __init__(self, terminal: str, status: int, body: str) -> None:
        self.terminal = terminal
        self.status = status
        self.body = body
        super().__init__(f"terminal '{terminal}' returned HTTP {status}: {body}")


class SourceError(SignalsError):
    """A signal source could not be set up, such as a Telegram chat that
    telethon-plus cannot resolve."""
