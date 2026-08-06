"""
check_health.py -- called from the start.bat status loop every 60s.
Prints UP/DOWN for each configured terminal port.
Logs any DOWN ports to full.log with timestamp.
"""
import datetime
import os
import socket
import sys

try:
    import yaml
except ImportError:
    print('  ERROR: pyyaml not installed')
    raise SystemExit(1)

# Reuse config_helper's per-VM filter rather than re-deriving it. It sits in
# this same directory and guards its main() behind __name__, so importing it
# is side-effect free.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from config_helper import _in_group, _vm_group_filter
except ImportError:
    def _vm_group_filter():
        return None

    def _in_group(terminal, allowed):
        return True

SHARED = r'C:\Users\Docker\Desktop\Shared'
CONFIG = os.path.join(SHARED, 'config', 'config.yaml')
FULL_LOG = os.path.join(SHARED, 'logs', 'full.log')

try:
    with open(CONFIG, encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    terminals = cfg.get('terminals') or []
except Exception as e:
    print(f'  ERROR reading config.yaml: {e}')
    raise SystemExit(1)

now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
dead = []

# Only this VM's terminals. Without the filter each VM probes every port in
# config.yaml, including the ones another VM hosts, and reports them DOWN — so
# the container sits permanently "unhealthy" and a genuine failure is
# indistinguishable from the usual noise. Observed 2026-08-04: the fast VM was
# unhealthy with a 25,272-long failing streak, listing only bulk-VM ports,
# while all 12 of its own terminals were serving normally.
allowed = _vm_group_filter()
terminals = [t for t in terminals if _in_group(t, allowed)]

for t in terminals:
    port = t['port']
    broker = t['broker']
    account = t['account']
    try:
        s = socket.create_connection(('localhost', port), timeout=2)
        s.close()
        print(f'  UP    {broker}/{account} :{port}')
    except Exception as e:
        print(f'  DOWN  {broker}/{account} :{port}  ({e})')
        dead.append(t)

if dead:
    try:
        with open(FULL_LOG, 'a') as f:
            for t in dead:
                f.write(
                    f'[{now}] [monitor] PORT {t["port"]}'
                    f' ({t["broker"]}/{t["account"]}) IS DOWN\n'
                )
    except Exception:
        pass
