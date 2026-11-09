"""Bounded external probes. Runs in killable subprocesses, never imports Engine."""
import http.client
import errno
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import time
from urllib.parse import urlsplit, urljoin


CONFIG_PATH = None

def guard():
    if CONFIG_PATH:
        try:
            config = json.loads(open(CONFIG_PATH).read())
        except FileNotFoundError:
            return
        except (ValueError, OSError):
            raise PolicyError("Не удалось прочитать сетевую политику")
        if config.get("privacy", {}).get("local_only"):
            raise PolicyError("Сетевой стоп-кран включён")


class PolicyError(ValueError):
    pass


def resolve(host, port, test=False):
    guard()
    host = host.encode('idna').decode('ascii')
    ips = list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    if not ips:
        raise socket.gaierror('Нет адресов')
    for ip in ips:
        addr = ipaddress.ip_address(ip)
        if not addr.is_global and not (test and addr.is_loopback):
            raise PolicyError('Цель не является публичным адресом')
    return ips


def connect(host, port, test=False, tls=False):
    ips = resolve(host, port, test)
    # Deterministic IPv4 preference; IPv6-only is supported. Other addresses visible in DNS probe.
    ip = next((x for x in ips if ':' not in x), ips[0])
    guard()
    sock = socket.create_connection((ip, port), timeout=5)
    if tls:
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
    return sock, ip


def http_probe(url, marker='', test=False):
    origin = urlsplit(url).hostname
    redirects = []
    for _ in range(6):
        u = urlsplit(url)
        if u.scheme not in ('http', 'https') or u.username or u.password:
            raise PolicyError('Недопустимый адрес редиректа')
        host = u.hostname.encode('idna').decode('ascii')
        port = u.port or (443 if u.scheme == 'https' else 80)
        # Connect directly to validated IP. http.client must not resolve a second time.
        sock, ip = connect(host, port, test, u.scheme == 'https')
        conn = (http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection)(host, port, timeout=5)
        conn.sock = sock
        try:
            conn.request('GET', (u.path or '/') + ('?' + u.query if u.query else ''), headers={'User-Agent': 'Dewiar-SiteCheck/1.0', 'Accept-Encoding': 'identity', 'Connection': 'close'})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                target = urljoin(url, resp.getheader('Location', ''))
                if not resp.getheader('Location'):
                    return 'DOWN', 'REDIRECT_LOCATION', {'http_code': resp.status}
                # Only the configured host and its www counterpart are automatically allowed.
                th = urlsplit(target).hostname or ''
                if th.removeprefix('www.') != (origin or '').removeprefix('www.'):
                    raise PolicyError('Редирект на другой домен: ' + th)
                redirects.append(resp.status)
                url = target
                continue
            detail = {'http_code': resp.status, 'ip': ip, 'redirects': redirects}
            if u.scheme == 'https':
                cert = sock.getpeercert()
                if cert.get('notAfter'):
                    detail['tls_until'] = int(ssl.cert_time_to_seconds(cert['notAfter']) // 60)
            if not 200 <= resp.status < 300:
                return 'DOWN', 'HTTP_STATUS', detail
            if marker:
                if resp.getheader('Content-Encoding', 'identity') != 'identity':
                    return 'CONFIG_ERROR', 'CONTENT_ENCODING', detail
                body = resp.read(65536).decode('utf-8', errors='replace')
                if marker not in body:
                    return 'DOWN', 'CONTENT_MISMATCH', detail
            return 'UP', 'OK', detail
        finally:
            conn.close()
    return 'DOWN', 'REDIRECT_LIMIT', {}


def http_status_probe(url, test=False):
    """Check a provider console without logging in or following auth redirects."""
    u = urlsplit(url)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise PolicyError('Недопустимый адрес консоли')
    host = u.hostname.encode('idna').decode('ascii')
    port = u.port or (443 if u.scheme == 'https' else 80)
    sock, ip = connect(host, port, test, u.scheme == 'https')
    conn = (http.client.HTTPSConnection if u.scheme == 'https' else http.client.HTTPConnection)(host, port, timeout=5)
    conn.sock = sock
    try:
        path = (u.path or '/') + ('?' + u.query if u.query else '')
        conn.request('HEAD', path, headers={'User-Agent':'Dewiar-SiteCheck/1.0','Connection':'close'})
        response = conn.getresponse()
        detail = {'http_code':response.status, 'ip':ip}
        if response.status >= 500:
            return 'DOWN', 'HTTP_STATUS', detail
        return 'UP', 'OK', detail
    finally:
        conn.close()


def banner(sock, limit=2048):
    data = b''
    while len(data) < limit:
        part = sock.recv(min(256, limit-len(data)))
        if not part:
            break
        data += part
        if b'\n' in data:
            break
    return data


def execute(spec, test=False):
    started = time.monotonic()
    kind = spec['kind']
    try:
        if kind == 'http':
            state, reason, detail = http_probe(spec['target'], spec.get('marker', ''), test)
        elif kind == 'http_status':
            state, reason, detail = http_status_probe(spec['target'], test)
        elif kind == 'dns':
            ips = resolve(spec['target'], 443, test)
            state, reason, detail = 'UP', 'OK', {'addresses': ips}
        elif kind == 'ping':
            ips = resolve(spec['target'], 443, test)
            ip = next((x for x in ips if ':' not in x), ips[0])
            if sys.platform == 'darwin':
                args = ['/sbin/ping', '-c', '1', '-W', '2000', ip]
            else:
                args = ['ping', '-c', '1', '-W', '2', ip]
            guard()
            result = subprocess.run(args, capture_output=True, timeout=5)
            state, reason, detail = ('UP', 'OK', {'ip': ip}) if result.returncode == 0 else ('DOWN', 'ICMP_NO_REPLY', {'ip': ip})
        else:
            host, port = spec['target'], spec['port']
            sock, ip = connect(host, port, test, kind in ('imap_tls', 'smtp_tls'))
            try:
                greeting = banner(sock)
                if kind == 'ssh':
                    ok = greeting.startswith((b'SSH-2.0-', b'SSH-1.99-'))
                elif kind == 'imap_tls':
                    ok = greeting.upper().startswith(b'* OK')
                else:
                    ok = greeting.startswith((b'220', b'120'))
                if ok and kind == 'smtp_starttls':
                    sock.sendall(b'EHLO monitor.invalid\r\n')
                    data = b''
                    while len(data) < 8192:
                        chunk = sock.recv(1024)
                        if not chunk:
                            break
                        data += chunk
                        if any(x.startswith(b'250 ') for x in data.splitlines()):
                            break
                    if b'STARTTLS' not in data.upper():
                        return dict(probe=spec['id'], state='DOWN', reason='STARTTLS_MISSING', detail={'ip': ip}, latency=0)
                    sock.sendall(b'STARTTLS\r\n')
                    if not banner(sock).startswith(b'220'):
                        ok = False
                    else:
                        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
                state, reason, detail = ('UP', 'OK', {'ip': ip, 'port': port}) if ok else ('DOWN', 'BANNER_BAD', {'ip': ip, 'port': port})
            finally:
                sock.close()
    except PolicyError as e:
        state, reason, detail = 'CONFIG_ERROR', 'TARGET_POLICY', {'message': str(e)[:180]}
    except ssl.SSLCertVerificationError:
        state, reason, detail = 'DOWN', 'TLS_INVALID', {}
    except ssl.SSLError:
        state, reason, detail = 'DOWN', 'TLS_HANDSHAKE', {}
    except socket.gaierror:
        state, reason, detail = 'DOWN', 'DNS_ERROR', {}
    except (TimeoutError, subprocess.TimeoutExpired):
        state, reason, detail = 'DOWN', 'TIMEOUT', {}
    except FileNotFoundError:
        state, reason, detail = 'CONFIG_ERROR', 'CAPABILITY_MISSING', {}
    except OSError as e:
        state, reason, detail = ('UNKNOWN', 'OBSERVER_NETWORK', {}) if e.errno in (errno.ENETUNREACH, errno.ENETDOWN, errno.EADDRNOTAVAIL) else ('DOWN', 'CONNECTION_FAILED', {})
    except http.client.HTTPException:
        state, reason, detail = 'DOWN', 'HTTP_PROTOCOL', {}
    return dict(probe=spec['id'], state=state, reason=reason, detail=detail, latency=round((time.monotonic()-started)*100)*10)


def specs(site):
    host = urlsplit(site.get('url') or '').hostname or site.get('dns_host') or site.get('ssh_host') or site.get('server_ip') or site.get('mail_host') or site.get('mail_imap_host') or site.get('mail_smtp_host') or site.get('mail_mx_host')
    out = []
    if site.get('url'):
        out.append(dict(id='http', kind='http', target=site['url'], marker=site['marker']))
        net_host = '['+host+']' if ':' in host else host
        if site.get('check_http', True):
            out.append(dict(id='http_port', kind='http', target='http://'+net_host+'/', marker=''))
        if site.get('check_https', True):
            out.append(dict(id='https_port', kind='http', target='https://'+net_host+'/', marker=''))
    if site['health_url']:
        out.append(dict(id='health', kind='http', target=site['health_url'], marker=site['marker']))
    if site.get('console_url'):
        out.append(dict(id='console', kind='http', target=site['console_url'], marker=''))
    for kind in ('dns', 'ping'):
        if site[kind]:
            out.append(dict(id=kind, kind=kind, target=(site.get('dns_host') or host) if kind == 'dns' else (site.get('ssh_host') or host)))
    for kind in ('ssh', 'ftp'):
        if site[kind]:
            out.append(dict(id=kind, kind=kind, target=site['ssh_host'] or host, port=site[kind+'_port']))
    if site['mail']:
        mail_host = site.get('mail_host') or site.get('mail_imap_host') or site.get('mail_smtp_host') or site.get('mail_mx_host')
        out.append(dict(id='mail', kind=site['mail_kind'], target=mail_host, port=site['mail_port']))
    return out


def port_diagnostic_specs(site):
    """One-shot TCP/banner checks. Never attempts SSH/FTP authentication."""
    host = (site.get('ssh_host') or site.get('server_ip') or
            urlsplit(site.get('url') or '').hostname or site.get('dns_host') or
            site.get('mail_host') or site.get('mail_imap_host') or
            site.get('mail_smtp_host') or site.get('mail_mx_host'))
    if not host:
        return []
    return [
        dict(id='ssh', kind='ssh', target=host, port=site.get('ssh_port') or 22),
        dict(id='ftp', kind='ftp', target=host, port=site.get('ftp_port') or 21),
    ]


def provider_console_spec(provider):
    url = provider.get('panel_url') or ''
    if not url:
        return None
    return dict(id='console', kind='http_status', target=url)


if __name__ == '__main__':
    payload = json.load(sys.stdin)
    CONFIG_PATH = payload.get('config_path')
    print(json.dumps(execute(payload['spec'], payload.get('test', False))))
