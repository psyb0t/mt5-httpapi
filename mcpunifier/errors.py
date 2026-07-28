"""Sentinel errors for the MCP unifier.

Typed so a tool can raise something precise and the MCP layer can turn it into
a message the caller can act on, rather than leaking an httpx exception.
"""


class UnifierError(Exception):
    """Base class for every error raised by this service."""


class UnknownTerminal(UnifierError):
    """The requested broker/account/instance is not configured.

    Carries the configured terminal list so the caller is told what it could
    have asked for instead of only being refused.
    """

    def __init__(self, terminal: str, known: list[str]) -> None:
        self.terminal = terminal
        self.known = known
        known_text = ", ".join(known) if known else "(none configured)"
        super().__init__(f"unknown terminal '{terminal}'; configured: {known_text}")


class TerminalUnreachable(UnifierError):
    """A configured terminal did not answer.

    Raised per call so one terminal being down never looks like a fault in the
    unifier and never takes the other terminals with it.
    """

    def __init__(self, terminal: str, reason: str) -> None:
        self.terminal = terminal
        self.reason = reason
        super().__init__(f"terminal '{terminal}' unreachable: {reason}")


class TerminalRejected(UnifierError):
    """A terminal answered with a non-2xx status.

    The upstream body is preserved because mt5api's own error payloads carry
    the useful part: MT5 retcodes and validation messages.
    """

    def __init__(self, terminal: str, status: int, body: str) -> None:
        self.terminal = terminal
        self.status = status
        self.body = body
        super().__init__(f"terminal '{terminal}' returned HTTP {status}: {body}")


class ConfigError(UnifierError):
    """The configuration could not be read or contains no usable terminals."""
