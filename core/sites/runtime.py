"""Dedicated bounded scheduler; probes are killable subprocesses, no LLM work."""
import concurrent.futures
import copy
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from .store import minute
from .probes import specs


class Runtime:
    def __init__(self, store, test=False, config_path=None):
        self.store, self.test, self.config_path = store, test, config_path
        self.stop = threading.Event()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix='site-probe')
        self.lock = threading.RLock()
        self.jobs = {}
        self.active = {}
        self.started = time.time()
        self.tick = 0
        self.error = ''
        self.policy_paused = False
        self.policy_snapshot = None
        self.last_renewal_minute = 0

    def local_only(self):
        if self.config_path:
            try:
                return bool(json.loads(Path(self.config_path).read_text()).get('privacy', {}).get('local_only'))
            except FileNotFoundError:
                return False
            except (OSError, ValueError):
                return True
        return False

    def start(self):
        threading.Thread(target=self.loop, name='sites-scheduler', daemon=True).start()

    def submit(self, site, diagnostic=False):
        with self.lock:
            if site['id'] in self.active:
                return self.active[site['id']]
            if len(self.active) >= self.store.settings()['workers']:
                raise ValueError('Все исполнители заняты; повторите через несколько секунд')
            jid = uuid.uuid4().hex
            self.jobs[jid] = dict(id=jid, site=site['id'], name=site['name'], mode='diagnostic' if diagnostic else 'scheduled', state='running', started=minute(), probe='', results=[])
            self.active[site['id']] = jid
            self.pool.submit(self.run, site, jid, diagnostic)
            return jid

    def run(self, site, jid, diagnostic):
        results = []
        try:
            for spec in specs(site):
                with self.lock:
                    self.jobs[jid]['probe'] = spec['id']
                if self.stop.is_set():
                    break
                if self.local_only():
                    r = dict(probe=spec['id'], state='UNKNOWN', reason='POLICY_PAUSED', detail={})
                else:
                    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
                    cmd = [sys.executable, '-B', str(Path(__file__).with_name('probes.py'))]
                    try:
                        proc = subprocess.run(cmd, input=json.dumps({'spec': spec, 'test': self.test, 'config_path': str(self.config_path) if self.config_path else None}), text=True, capture_output=True, timeout=20, env=env)
                        r = json.loads(proc.stdout) if proc.returncode == 0 else dict(probe=spec['id'], state='UNKNOWN', reason='PROBE_ERROR', detail={})
                    except subprocess.TimeoutExpired:
                        r = dict(probe=spec['id'], state='DOWN', reason='TIMEOUT', detail={})
                    except (ValueError, OSError):
                        r = dict(probe=spec['id'], state='UNKNOWN', reason='WORKER_ERROR', detail={})
                results.append(r)
                with self.lock:
                    self.jobs[jid]['results'] = list(results)
            if not diagnostic and not self.stop.is_set() and not self.local_only() and not self.store.settings()['paused']:
                self.store.ingest(site, results, jid)
            with self.lock:
                self.jobs[jid].update(state='complete', finished=minute())
        except Exception as e:
            with self.lock:
                self.error = type(e).__name__ + ': ' + str(e)[:160]
                self.jobs[jid].update(state='failed', error=self.error)
        finally:
            with self.lock:
                self.active.pop(site['id'], None)
                completed = [k for k,v in self.jobs.items() if v['state'] != 'running']
                for k in completed[:-50]:
                    self.jobs.pop(k, None)

    def loop(self):
        while not self.stop.wait(1):
            self.tick = time.time()
            try:
                paused = self.local_only()
                sites = self.store.all()
                snapshot = (paused, tuple(s['id'] for s in sites))
                if snapshot != self.policy_snapshot:
                    with self.store.db() as c:
                        open_pause = c.execute("SELECT 1 FROM exclusions WHERE reason='network_policy' AND end IS NULL LIMIT 1").fetchone()
                        for site in sites:
                            self.store._exclude(c, site['id'], 'network_policy', paused, minute())
                        if open_pause and not paused:
                            c.execute('UPDATE current SET failures=0,successes=0')
                            c.execute('UPDATE sites SET due=?', (minute(),))
                    self.policy_paused = paused
                    self.policy_snapshot = snapshot
                cfg = self.store.settings()
                now = minute()
                if now != self.last_renewal_minute:
                    self.store.queue_renewal_reminders()
                    self.last_renewal_minute = now
                if cfg['paused'] or paused:
                    continue
                for site in sorted(sites, key=lambda s:s['due']):
                    if site['enabled'] and site['due'] <= minute():
                        with self.lock:
                            busy = site['id'] in self.active
                            full = len(self.active) >= cfg['workers']
                        if full:
                            break
                        if not busy:
                            self.submit(site)
            except Exception as e:
                self.error = type(e).__name__ + ': ' + str(e)[:160]
                self.stop.wait(3)

    def status(self):
        size = 0
        for path in self.store.root.glob('sites.db*'):
            try:
                size += path.stat().st_size
            except FileNotFoundError:
                pass  # SQLite can remove/checkpoint its WAL between glob and stat.
        with self.lock:
            return dict(pid=os.getpid(), uptime_seconds=int(time.time()-self.started), heartbeat=self.tick,
                        active=len(self.active), jobs=copy.deepcopy(list(self.jobs.values())[-50:]), error=self.error,
                        paused=self.store.settings()['paused'], policy_paused=self.local_only(),
                        database_bytes=size, autostart=(Path.home()/'Library/LaunchAgents/com.dewiar.sites-monitor.plist').exists())
