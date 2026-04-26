"""
check_health.py -- called from the start.bat status loop every 60s.
Prints UP/DOWN for each configured terminal port.
Logs any DOWN ports to full.log with timestamp.
"""
import datetime
import json
import os
import socket

SHARED = r'C:\Users\Docker\Desktop\Shared'
CONFIG = os.path.join(SHARED, 'config', 'terminals.json')
FULL_LOG = os.path.join(SHARED, 'logs', 'full.log')

try:
    with open(CONFIG) as f:
        terminals = json.load(f)
except Exception as e:
    print(f'  ERROR reading terminals.json: {e}')
    raise SystemExit(1)

now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
dead = []

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
