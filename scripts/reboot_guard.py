"""
reboot_guard.py -- called by reboot.bat before a scheduled reboot.

Asks every API on this VM (GET /busy) whether it is doing something a reboot
would break: a backtest queued or running, or a request in flight that
changes something (an order, a stop loss, a deployment, a file write, a
compile). While any API is busy the reboot waits, checking again every
POLL_SECONDS. Every wait is written to full.log with the terminal and the
reason.

When every API is idle the guard creates the drain flag, which makes the
APIs refuse new writes with 503 REBOOT_PENDING, waits DRAIN_SETTLE_SECONDS
and checks once more, so nothing starts in the seconds before the reboot.
Still idle: exit 0 and reboot.bat reboots. Busy again: the flag goes and the
wait goes on.

reboot.max_postpone in config.yaml (minutes, default 360; the deprecated
top-level reboot_max_postpone without a reboot: block) caps the wait, so a
job that never finishes cannot keep a wedged VM from its reboot. 0 turns the
guard off. An API that does not answer counts as idle: it is doing nothing,
and a reboot is what brings it back.

Always exits 0: the guard decides when, never whether.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

try:
    import yaml
except ImportError:
    yaml = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from config_helper import _in_group, _vm_group_filter, reboot_max_postpone
except ImportError:
    def _vm_group_filter():
        return None

    def _in_group(terminal, allowed):
        return True

    def reboot_max_postpone(cfg):
        block = cfg.get('reboot')
        if isinstance(block, dict):
            return block.get('max_postpone', DEFAULT_MAX_POSTPONE_MINUTES)
        return cfg.get('reboot_max_postpone', DEFAULT_MAX_POSTPONE_MINUTES)

SHARED = r'C:\Users\Docker\Desktop\Shared'
CONFIG = os.path.join(SHARED, 'config', 'config.yaml')
FULL_LOG = os.path.join(SHARED, 'logs', 'full.log')
DRAIN_FLAG = os.path.join(SHARED, 'reboot.draining')

DEFAULT_MAX_POSTPONE_MINUTES = 360
POLL_SECONDS = 30
DRAIN_SETTLE_SECONDS = 3
REQUEST_TIMEOUT_SECONDS = 10
# A wait that changes nothing is logged again after this long, so a long
# backtest shows up in the log without a line every POLL_SECONDS.
REPEAT_LOG_SECONDS = 300
API_HOST = '127.0.0.1'
# A write's reason ends in its age, "(12.3s)", which grows on every check.
_REASON_AGE = re.compile(r' \(\d+(?:\.\d+)?s\)$')


def same_wait(reasons):
    """The reasons without their ages: what decides whether a wait changed."""
    return [_REASON_AGE.sub('', reason) for reason in reasons]


def log(message, log_path=FULL_LOG):
    line = f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] [reboot-guard] {message}'
    print(line)
    try:
        with open(log_path, 'a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except OSError as err:
        print(f'reboot-guard: cannot write {log_path}: {err}')


def load_config(path=CONFIG):
    if yaml is None:
        raise RuntimeError('pyyaml is not installed')
    with open(path, encoding='utf-8') as handle:
        return yaml.safe_load(handle) or {}


def max_postpone_seconds(cfg):
    raw = reboot_max_postpone(cfg)
    try:
        minutes = float(raw)
    except (TypeError, ValueError):
        log(f'max_postpone={raw!r} is not a number, using {DEFAULT_MAX_POSTPONE_MINUTES}')
        minutes = DEFAULT_MAX_POSTPONE_MINUTES
    return max(0.0, minutes) * 60


def this_vm_terminals(cfg):
    allowed = _vm_group_filter()
    return [t for t in cfg.get('terminals') or [] if _in_group(t, allowed)]


def terminal_name(terminal):
    instance = terminal.get('instance') or 'default'
    return f"{terminal.get('broker')}/{terminal.get('account')}/{instance}"


def fetch_busy(port, token):
    """One API's GET /busy body, or None when it does not answer."""
    request = urllib.request.Request(f'http://{API_HOST}:{port}/busy')
    if token:
        request.add_header('Authorization', f'Bearer {token}')
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as err:
        # Answering but refusing (a wrong token, an API too old for /busy):
        # nothing to hold the reboot for, but worth seeing in the log.
        log(f'port {port} answered GET /busy with {err.code}, counting it as idle')
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def busy_reasons(terminals, token, fetch=fetch_busy):
    """Every reason any API on this VM gives for holding the reboot."""
    reasons = []
    for terminal in terminals:
        body = fetch(terminal['port'], token)
        if not body or not body.get('busy'):
            continue
        for reason in body.get('reasons') or ['busy']:
            reasons.append(f'{terminal_name(terminal)}: {reason}')
    return reasons


def raise_drain_flag(path=DRAIN_FLAG):
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(f'{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n')


def drop_drain_flag(path=DRAIN_FLAG):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def wait_until_idle(
    terminals,
    token,
    max_postpone,
    fetch=fetch_busy,
    sleep=time.sleep,
    clock=time.monotonic,
    drain_flag=DRAIN_FLAG,
):
    """Block until no API is busy, or max_postpone seconds have passed.

    Returns why the reboot may go ahead: 'idle' or 'max_postpone'. Leaves the
    drain flag up on 'idle' so no write starts before the reboot.
    """
    started = clock()
    last_reasons = None
    last_logged = None
    while True:
        reasons = busy_reasons(terminals, token, fetch)
        if not reasons:
            raise_drain_flag(drain_flag)
            sleep(DRAIN_SETTLE_SECONDS)
            reasons = busy_reasons(terminals, token, fetch)
            if not reasons:
                return 'idle'
            drop_drain_flag(drain_flag)

        waited = clock() - started
        if waited >= max_postpone:
            log(f'reboot no longer postponed after {waited / 60:.0f} min '
                f'(reason=max_postpone), still busy: {"; ".join(reasons)}')
            return 'max_postpone'

        now = clock()
        wait = same_wait(reasons)
        if wait != last_reasons or last_logged is None or now - last_logged >= REPEAT_LOG_SECONDS:
            log(f'reboot postponed (reason=busy, waited {waited / 60:.0f} min): {"; ".join(reasons)}')
            last_reasons = wait
            last_logged = now
        sleep(POLL_SECONDS)


def main():
    try:
        cfg = load_config()
    except (OSError, RuntimeError, ValueError) as err:
        log(f'cannot read config.yaml ({err}), rebooting without checking (reason=no_config)')
        return 0
    max_postpone = max_postpone_seconds(cfg)
    if max_postpone == 0:
        log('guard off (max_postpone=0), rebooting now')
        return 0
    terminals = this_vm_terminals(cfg)
    outcome = wait_until_idle(terminals, cfg.get('api_token') or '', max_postpone)
    if outcome == 'idle':
        log(f'all {len(terminals)} API(s) idle, rebooting (reason=idle)')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as err:  # noqa: BLE001 - never block a reboot on a guard bug
        log(f'guard failed ({type(err).__name__}: {err}), rebooting anyway (reason=guard_error)')
        sys.exit(0)
