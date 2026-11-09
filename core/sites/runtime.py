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
from .probes import specs, port_diagnostic_specs, provider_console_spec


class Runtime:
    def __init__(self, store, test=False, config_path=None):
        self.store, self.test, self.config_path = store, test, config_path
        self.stop = threading.Event()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix='site-probe')
        # Opportunistic SSH/FTP banner checks use their own tiny pool so opening
        # the Sites screen cannot consume the scheduled monitoring workers.
        self.port_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='site-port-diagnostic')
        self.lock = threading.RLock()
        self.jobs = {}
        self.active = {}
        self.port_active = {}
        self.port_last_submit = {}
        self.console_active = {}
        self.console_last_submit = {}
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

    def scan_ports(self):
        """Queue one-shot SSH/FTP and provider-panel availability diagnostics."""
        if self.local_only() or self.store.settings()['paused']:
            return {'queued': 0, 'queued_sites': 0, 'queued_consoles': 0,
                    'skipped': len(self.store.all())+len(self.store.providers()), 'reason': 'paused'}
        now = minute()
        queued = skipped = queued_sites = queued_consoles = 0
        for site in self.store.all():
            if not site['enabled'] or not port_diagnostic_specs(site):
                skipped += 1
                continue
            sid = site['id']
            with self.lock:
                # Re-opening/reloading the iframe does not launch duplicate probes.
                if sid in self.port_active or now - self.port_last_submit.get(sid, -100000) < 1:
                    skipped += 1
                    continue
                jid = uuid.uuid4().hex
                self.port_active[sid] = jid
                self.port_last_submit[sid] = now
                self.jobs[jid] = dict(id=jid, site=sid, name=site['name'], mode='port-diagnostic',
                                      state='running', started=now, probe='', results=[])
                self.port_pool.submit(self.run_port_diagnostic, site, jid)
                queued += 1
                queued_sites += 1
        for provider in self.store.providers():
            spec = provider_console_spec(provider)
            if not spec:
                skipped += 1
                continue
            pid = provider['id']
            with self.lock:
                if pid in self.console_active or now-self.console_last_submit.get(pid,-100000) < 1:
                    skipped += 1
                    continue
                jid = uuid.uuid4().hex
                self.console_active[pid] = jid
                self.console_last_submit[pid] = now
                self.jobs[jid] = dict(id=jid, site='', name=provider['name'], mode='console-diagnostic',
                                      state='running', started=now, probe='', results=[])
                self.port_pool.submit(self.run_console_diagnostic, provider, spec, jid)
                queued += 1
                queued_consoles += 1
        return {'queued': queued, 'queued_sites': queued_sites, 'queued_consoles': queued_consoles, 'skipped': skipped}

    def run_console_diagnostic(self, provider, spec, jid):
        result = dict(probe='console',state='UNKNOWN',reason='WORKER_ERROR',detail={})
        try:
            with self.lock:
                self.jobs[jid]['probe'] = 'console'
            if self.stop.is_set() or self.local_only() or self.store.settings()['paused']:
                result = dict(probe='console',state='UNKNOWN',reason='POLICY_PAUSED',detail={})
            else:
                env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
                cmd = [sys.executable, '-B', str(Path(__file__).with_name('probes.py'))]
                try:
                    proc = subprocess.run(cmd,input=json.dumps({'spec':spec,'test':self.test,
                        'config_path':str(self.config_path) if self.config_path else None}),text=True,
                        capture_output=True,timeout=12,env=env)
                    result = json.loads(proc.stdout) if proc.returncode == 0 else dict(
                        probe='console',state='UNKNOWN',reason='PROBE_ERROR',detail={})
                except subprocess.TimeoutExpired:
                    result = dict(probe='console',state='DOWN',reason='TIMEOUT',detail={})
                except (ValueError,OSError):
                    result = dict(probe='console',state='UNKNOWN',reason='WORKER_ERROR',detail={})
            if not self.stop.is_set() and not self.local_only() and not self.store.settings()['paused']:
                self.store.save_provider_diagnostic(provider['id'],result,rev=provider['rev'])
            with self.lock:
                self.jobs[jid].update(state='complete',finished=minute(),results=[result])
        except Exception as e:
            with self.lock:
                self.error = type(e).__name__ + ': ' + str(e)[:160]
                self.jobs[jid].update(state='failed',error=self.error)
        finally:
            with self.lock:
                self.console_active.pop(provider['id'],None)
                completed=[k for k,v in self.jobs.items() if v['state']!='running']
                for k in completed[:-50]:
                    self.jobs.pop(k,None)

    def run_port_diagnostic(self, site, jid):
        results = []
        try:
            for spec in port_diagnostic_specs(site):
                with self.lock:
                    self.jobs[jid]['probe'] = spec['id']
                if self.stop.is_set() or self.local_only() or self.store.settings()['paused']:
                    result = dict(probe=spec['id'], state='UNKNOWN', reason='POLICY_PAUSED', detail={})
                else:
                    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
                    cmd = [sys.executable, '-B', str(Path(__file__).with_name('probes.py'))]
                    try:
                        proc = subprocess.run(cmd, input=json.dumps({'spec': spec, 'test': self.test,
                            'config_path': str(self.config_path) if self.config_path else None}), text=True,
                            capture_output=True, timeout=12, env=env)
                        result = json.loads(proc.stdout) if proc.returncode == 0 else dict(
                            probe=spec['id'], state='UNKNOWN', reason='PROBE_ERROR', detail={})
                    except subprocess.TimeoutExpired:
                        result = dict(probe=spec['id'], state='DOWN', reason='TIMEOUT', detail={})
                    except (ValueError, OSError):
                        result = dict(probe=spec['id'], state='UNKNOWN', reason='WORKER_ERROR', detail={})
                results.append(result)
                with self.lock:
                    self.jobs[jid].update(probe=spec['id'], results=list(results))
            if not self.stop.is_set() and not self.local_only() and not self.store.settings()['paused']:
                self.store.save_port_diagnostics(site['id'], results, rev=site['rev'])
            with self.lock:
                self.jobs[jid].update(state='complete', finished=minute())
        except Exception as e:
            with self.lock:
                self.error = type(e).__name__ + ': ' + str(e)[:160]
                self.jobs[jid].update(state='failed', error=self.error)
        finally:
            with self.lock:
                self.port_active.pop(site['id'], None)
                completed = [k for k, v in self.jobs.items() if v['state'] != 'running']
                for k in completed[:-50]:
                    self.jobs.pop(k, None)

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
                        active=len(self.active)+len(self.port_active)+len(self.console_active), jobs=copy.deepcopy(list(self.jobs.values())[-50:]), error=self.error,
                        paused=self.store.settings()['paused'], policy_paused=self.local_only(),
                        database_bytes=size, autostart=(Path.home()/'Library/LaunchAgents/com.dewiar.sites-monitor.plist').exists())
