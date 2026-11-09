"""Explicit, bounded lookups of public DNS and RDAP data. No automatic claims about the actual hoster."""
import ipaddress
import json
import socket
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .probes import PolicyError, guard, resolve
from .store import network_host


def _https_json(url):
    """Fetch a small public JSON response, validating every HTTPS redirect destination."""
    client = httpx.Client(timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=False,
                          trust_env=False, headers={"Accept": "application/rdap+json, application/json",
                                                    "User-Agent": "Dewiar-SiteDiscovery/1.0"})
    try:
        for _ in range(4):
            guard()
            u = urlsplit(url)
            if u.scheme != "https" or not u.hostname or u.username or u.password or u.port not in (None, 443):
                raise PolicyError("Недопустимый адрес RDAP")
            resolve(u.hostname, 443)
            try:
                response = client.get(url)
            except httpx.RequestError:
                # Public RDAP/ASN services are optional enrichment. Keep useful
                # DNS results when an auxiliary registry is temporarily unavailable.
                return None
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    break
                url = urljoin(url, location)
                continue
            if response.status_code >= 400:
                return None
            if len(response.content) > 1_000_000:
                raise ValueError("Публичный ответ слишком большой")
            try:
                return response.json()
            except (ValueError, json.JSONDecodeError):
                return None
        return None
    finally:
        client.close()


_rdap = _https_json


def _dns(domain, qtype):
    url='https://dns.google/resolve?name='+quote(domain,safe='.')+'&type='+qtype
    data=_https_json(url) or {}
    return [str(x.get('data','')).strip() for x in data.get('Answer',[]) if x.get('data')]


def _provider(host):
    h=(host or '').lower().rstrip('.')
    def matches(*domains):
        return any(h==domain or h.endswith('.'+domain) for domain in domains)
    if matches('beget.com','beget.ru','beget.pro'):
        return {'name':'Beget','url':'https://beget.com','panel':'https://cp.beget.com','panel_name':'Панель Beget'}
    if matches('timeweb.ru','timeweb.org','timeweb.com'):
        return {'name':'Timeweb','url':'https://timeweb.com','panel':'https://hosting.timeweb.ru','panel_name':'Панель Timeweb'}
    if matches('firstvds.ru'):
        return {'name':'FirstVDS','url':'https://firstvds.ru','panel':'https://my.firstvds.ru','panel_name':'BILLmanager; доступ к панелям сервера из кабинета'}
    if matches('nic.ru','nic.com'):
        return {'name':'RU-CENTER / NIC.RU','url':'https://www.nic.ru','panel':'https://www.nic.ru/manager/','panel_name':'Кабинет RU-CENTER'}
    if matches('sweb.ru','spaceweb.ru'):
        return {'name':'SpaceWeb','url':'https://sweb.ru','panel':'https://mcp.sweb.ru/main/auth/','panel_name':'Панель SpaceWeb'}
    if matches('domaindiscover.com'):
        return {'name':'DomainDiscover','url':'https://www.domaindiscover.com','panel':'','panel_name':'DNS delegated to DomainDiscover'}
    return None


def _org(entities, role):
    for entity in entities or []:
        if role not in entity.get("roles", []):
            continue
        for item in entity.get("vcardArray", [None, []])[1]:
            if len(item) > 3 and item[0] == "org" and item[3]:
                value = item[3]
                return " ".join(map(str, value)) if isinstance(value, list) else str(value)
    return ""


def _date(data, action):
    for event in data.get("events", []):
        if event.get("eventAction", "").lower() == action:
            return str(event.get("eventDate", ""))[:10]
    return ""


def discover(value):
    """Return concise public signals for a domain; caller initiated, no writes or saved credentials."""
    host = network_host(str(value), "домен").strip(".")
    try:
        ipaddress.ip_address(host)
        raise ValueError("Введите доменное имя, а не IP-адрес")
    except ValueError as e:
        if str(e) == "Введите доменное имя, а не IP-адрес":
            raise
    if "." not in host:
        raise ValueError("Введите полное доменное имя")
    guard()
    try:
        addresses = resolve(host, 443)
    except socket.gaierror:
        addresses = []
    domain = _rdap("https://rdap.org/domain/" + quote(host, safe=".")) or {}
    nameservers = sorted({str(x.get("ldhName", "")).lower().rstrip(".") for x in domain.get("nameservers", []) if x.get("ldhName")})
    if not nameservers:
        nameservers=sorted({x.lower().rstrip('.') for x in _dns(host,'NS')})
    mx_records=_dns(host,'MX')
    mx_hosts=sorted({x.split()[-1].lower().rstrip('.') for x in mx_records if x.split()})
    registrar = _org(domain.get("entities"), "registrar")
    networks = []
    for ip in addresses[:4]:
        data = _rdap("https://rdap.org/ip/" + quote(ip, safe=":")) or {}
        org = _org(data.get("entities"), "registrant") or _org(data.get("entities"), "technical")
        routing=_https_json('https://stat.ripe.net/data/network-info/data.json?resource='+quote(ip,safe='')) or {}
        route_data=routing.get('data') or {}; asn=(route_data.get('asns') or [''])[0]
        overview=_https_json('https://stat.ripe.net/data/as-overview/data.json?resource=AS'+quote(str(asn),safe='')) or {} if asn else {}
        holder=(overview.get('data') or {}).get('holder','')
        prefix=route_data.get('prefix','')
        networks.append({"ip": ip, "network": str(data.get("name") or data.get("handle") or "")[:120],
                         "organization": org[:160], "country": str(data.get("country") or "")[:2],
                         "range": prefix or (str(data.get("startAddress", ""))[:45] + ("–" + str(data.get("endAddress", ""))[:45] if data.get("endAddress") else "")),
                         "asn":str(asn),"operator":str(holder or org or data.get('name') or '')[:160]})
    expiration = _date(domain, "expiration")
    dns_provider=next((_provider(ns) for ns in nameservers if _provider(ns)),None)
    mail_provider=next((_provider(mx) for mx in mx_hosts if _provider(mx)),None)
    host_candidate=None
    for net in networks:
        normalized=''.join(ch for ch in (net.get('operator') or '').lower() if ch.isalnum())
        if 'jsciot' in normalized or 'rujsciot' in normalized:
            host_candidate=_provider('firstvds.ru') | {'evidence':'Владелец сети/ASN; не подтверждает договор именно на FirstVDS','confidence':'средняя'}
            break
        if 'timeweb' in normalized:
            host_candidate=_provider('timeweb.ru') | {'evidence':'Владелец сети/ASN; проверьте договор','confidence':'средняя'}
            break
        if 'beget' in normalized:
            host_candidate=_provider('beget.com') | {'evidence':'Владелец сети/ASN; проверьте договор','confidence':'средняя'}
            break
    return {"domain": host, "addresses": addresses, "nameservers": nameservers,"mx_hosts":mx_hosts,"registrar": registrar[:160],
            "domain_expires": expiration, "networks": networks,"dns_provider_candidate":dns_provider,"hosting_provider_candidate":host_candidate,
            "mail_provider_candidate":mail_provider,
            "sources": [{"label": "RDAP domain", "url": "https://rdap.org/domain/" + quote(host, safe=".")},
                        {"label": "DNS/IP", "url": "https://dns.google/resolve?name=" + quote(host)},
                        {"label":"RIPEstat","url":"https://stat.ripe.net/"+(quote(addresses[0],safe='') if addresses else '')}],
            "note": "Сетевая организация по ASN, DNS-провайдер и продавец хостинга могут быть разными. Проверьте кандидата по договору/кабинету."}
