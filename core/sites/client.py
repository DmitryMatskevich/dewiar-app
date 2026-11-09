"""Lazy bridge to the independent local service; no worker in Engine threads."""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def endpoint(start=True):
    from settings import STATE_DIR, CONFIG_DIR
    root = STATE_DIR / 'sites-monitor'
    record = root / 'endpoint.json'
    def read():
        info = json.loads(record.read_text())
        token = (root / 'api-token').read_text().strip()
        base = f"http://127.0.0.1:{int(info['port'])}"
        req = urllib.request.Request(base+'/api/status',headers={'Authorization':'Bearer '+token})
        with urllib.request.urlopen(req,timeout=2) as r:
            json.load(r)
        return dict(url=base+'/#token='+token, base=base, token=token)
    try:
        return read()
    except (OSError, ValueError, KeyError):
        if not start:
            raise ValueError('Служба мониторинга не запущена')
    if getattr(sys, 'frozen', False):
        raise ValueError('Запустите отдельную службу Сайты через tools/sites_monitor.py')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    log = open(root/'service.log','a')
    subprocess.Popen([sys.executable,'-B','-m','sites.server','--root',str(root),'--config',str(CONFIG_DIR)],cwd=str(Path(__file__).resolve().parents[1]),stdout=log,stderr=log,start_new_session=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
    log.close()
    for _ in range(20):
        time.sleep(.1)
        try:
            return read()
        except (OSError, ValueError, KeyError):
            pass
    raise ValueError('Служба мониторинга не ответила; проверьте service.log')


def command(cmd, args):
    ep = endpoint(start=cmd!='sites_alert')
    if cmd == 'sites_service':
        return {'url':ep['url']}
    if cmd == 'sites_scan_ports':
        req = urllib.request.Request(ep['base']+'/api/scan-ports', data=b'{}',
            headers={'Authorization':'Bearer '+ep['token'],'Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(req,timeout=5) as r:
            return json.load(r)
    routes = {'sites_list':'/api/sites', 'sites_status':'/api/status', 'sites_incidents':'/api/incidents', 'sites_alert':'/api/alerts'}
    if cmd not in routes:
        raise ValueError('Неизвестная команда сайтов')
    req = urllib.request.Request(ep['base']+routes[cmd],headers={'Authorization':'Bearer '+ep['token']})
    with urllib.request.urlopen(req,timeout=5) as r:
        return json.load(r)


if __name__ == '__main__':
    import webbrowser
    webbrowser.open(endpoint()['url'])
