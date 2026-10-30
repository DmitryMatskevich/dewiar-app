"""Scheduled model selection and non-secret, observed quota/health state."""
import copy
import datetime as dt
import hashlib
import json
import threading
import time
import os

_lock = threading.RLock()
_health = {}
_quotas = {}
_selected = None
_loaded = False

MODEL_ROLES = ("communication", "coding", "planning", "analytics")


def roles(p):
    """Назначения профиля.

    Профили, созданные до появления очередей, остаются в «Общении». Планирование
    и программирование требуют явного назначения: случайная старая модель не
    должна начать менять проект или участвовать в совете.
    """
    raw = p.get("roles")
    if raw is None:
        return {"communication"}
    if isinstance(raw, str):
        raw = [raw]
    return {str(x) for x in (raw or []) if str(x) in MODEL_ROLES}


def _quota_path():
    from settings import STATE_DIR
    return STATE_DIR / 'model-quotas.json'


def minute(value, end=False):
    try:
        h, m = map(int, str(value).split(':'))
        if 0 <= h < 24 and 0 <= m < 60 or end and h == 24 and m == 0:
            return h * 60 + m
    except (ValueError, TypeError):
        pass
    raise ValueError('Время модели: используйте ЧЧ:ММ; 24:00 допустимо только в конце интервала')


def validate(profiles):
    for p in profiles.values():
        priority = p.get('priority', 9)
        if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 9:
            raise ValueError('Приоритет модели должен быть целым числом от 1 до 9')
        if p.get('schedule_enabled'):
            minute(p.get('schedule_start', '00:00'))
            minute(p.get('schedule_end', '24:00'), True)
        timeout = p.get('timeout', 60)
        if timeout is not None and not 5 <= float(timeout) <= 1800:
            raise ValueError('Ожидание модели: от 5 до 1800 секунд')


def in_schedule(p, now=None):
    if not p.get('schedule_enabled'):
        return True
    now = now or dt.datetime.now().astimezone()
    current = now.hour * 60 + now.minute
    try:
        start = minute(p.get('schedule_start', '00:00'))
        end = minute(p.get('schedule_end', '24:00'), True)
    except ValueError:
        return False
    return True if start == end else start <= current < end if start < end else current >= start or current < end


def key(p):
    return hashlib.sha256(json.dumps([p.get(k) for k in ('kind', 'base_url', 'model', 'api_key')]).encode()).hexdigest()


def quota(provider, value=None):
    global _loaded
    with _lock:
        if not _loaded:
            try:
                data = json.loads(_quota_path().read_text())
                if isinstance(data, dict):
                    _quotas.update({k:v for k,v in data.items() if isinstance(v, dict)})
            except (OSError, ValueError):
                pass
            _loaded = True
        if value is not None:
            _quotas[provider] = dict(value, checked_at=time.time())
            try:
                path = _quota_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix('.tmp')
                tmp.write_text(json.dumps(_quotas), encoding='utf-8')
                os.chmod(tmp, 0o600)
                tmp.replace(path)
            except OSError:
                pass
        return copy.deepcopy(_quotas.get(provider, {}))


def observe_headers(p, headers):
    values = {}
    for label, names in {
        'tokens': ('x-ratelimit-remaining-tokens', 'anthropic-ratelimit-tokens-remaining'),
        'requests': ('x-ratelimit-remaining-requests', 'anthropic-ratelimit-requests-remaining'),
    }.items():
        for name in names:
            if headers.get(name) is not None:
                try:
                    values[label] = int(headers[name])
                except (ValueError, TypeError):
                    pass
    if values:
        quota('api:' + key(p), {'remaining':values, 'source':'HTTP response headers', 'scope':'api_key'})


def refresh(llm, retry=False):
    """Only documented, read-only account endpoints. Never issue a model prompt."""
    import egress
    if egress.local_only():
        return
    if retry:
        with _lock:
            _health.clear()
    from urllib.parse import urlparse
    seen = set()
    for p in (llm.get('profiles') or {}).values():
        kind = p.get('kind', '')
        if kind.endswith('_subscription'):
            provider = kind.removesuffix('_subscription')
            if provider not in seen:
                seen.add(provider)
                try:
                    import subscriptions
                    subscriptions.read_limits(provider)
                except Exception:
                    pass  # Existing observed limits remain timestamped, never replaced by zero.
        elif urlparse(p.get('base_url') or '').hostname == 'openrouter.ai' and p.get('api_key'):
            cache_key = 'api:' + key(p)
            if cache_key in seen:
                continue
            seen.add(cache_key)
            try:
                import httpx
                r = httpx.get('https://openrouter.ai/api/v1/key', headers={'Authorization':'Bearer '+p['api_key']}, timeout=10, follow_redirects=False)
                r.raise_for_status()
                data = r.json().get('data') or {}
                budget = data.get('effective_budget') or {}
                quota(cache_key, {'budget_remaining_usd': budget.get('remaining_usd'), 'key_remaining_usd':data.get('limit_remaining'),
                                  'source':'OpenRouter /api/v1/key', 'scope':'api_key'})
            except Exception:
                pass


def blocked(p, now=None):
    now = time.time() if now is None else now
    with _lock:
        state = _health.get(key(p), {})
        if state.get('until', 0) > now:
            return state.get('reason', 'Ожидание повторной попытки')
    provider = str(p.get('kind', '')).removesuffix('_subscription')
    q = quota(provider)
    api = quota('api:' + key(p))
    if now - api.get('checked_at', 0) < 300 and any(isinstance(api.get(k), (int, float)) and api[k] <= 0
                                                  for k in ('budget_remaining_usd', 'key_remaining_usd')):
        return 'Лимит средств API исчерпан; обновите лимиты после пополнения'
    # Only a known shared bucket or an exact model bucket can exclude a model.
    for bucket in q.get('buckets', []):
        if bucket.get('id') not in ('codex', 'shared', p.get('model')):
            continue
        for w in bucket.get('windows', []):
            if w.get('remaining_percent') == 0 and (w.get('resets_at') or 0) > now:
                return 'Лимит исчерпан до ' + dt.datetime.fromtimestamp(w['resets_at']).strftime('%d.%m %H:%M')
    return ''


def candidates(llm, now=None, role=None):
    profiles = llm.get('profiles') or {}
    active = llm.get('active')
    ordered = sorted(profiles.items(), key=lambda item: (item[1].get('priority', 9), item[0] != active, item[0]))
    out = []
    for pid, p in ordered:
        if role and role not in roles(p):
            continue
        import egress
        from urllib.parse import urlparse
        host = urlparse(p.get('base_url') or '').hostname
        if (p.get('cloud') and not egress.dewiar_services_enabled()) or (egress.local_only() and not egress.local_host(host)):
            continue
        try:
            egress.check_dewiar_host(host)
        except egress.DewiarServicesDisabled:
            continue
        if p.get('routing_enabled', True) and in_schedule(p, now) and not blocked(p):
            out.append(dict(p, _profile_id=pid, timeout=p.get('timeout') or 60))
    return out


def failed(p, error):
    text = str(error).lower()
    limited = any(s in text for s in ('429', '402', 'quota', 'rate limit', 'rate_limit', 'usage limit', 'hit your limit', 'credit', 'balance', 'лимит', 'баланс', 'средств', 'денег', 'токены законч'))
    reason = 'Лимит или баланс исчерпан' if limited else 'Модель не ответила или отклонила запрос'
    until = time.time() + (300 if limited else 60)
    if limited:
        provider = str(p.get('kind','')).removesuffix('_subscription')
        resets = [w['resets_at'] for b in quota(provider).get('buckets', []) if b.get('id') in ('codex','shared',p.get('model'))
                  for w in b.get('windows', []) if w.get('remaining_percent') == 0 and (w.get('resets_at') or 0) > time.time()]
        if resets:
            until = max(resets)
    with _lock:
        _health[key(p)] = {'until': until, 'reason': reason}


def succeeded(p):
    global _selected
    with _lock:
        _health.pop(key(p), None)
        _selected = {'id': p.get('_profile_id'), 'model': p.get('model'), 'name': p.get('name'), 'at': time.time()}


def snapshot(llm, role=None):
    """Сводка доступности для выбранной очереди (или для всех очередей, если role не задан)."""
    ranked = candidates(llm, role=role)
    eligible = {p['_profile_id'] for p in ranked}
    rows = []
    for pid, p in (llm.get('profiles') or {}).items():
        provider = str(p.get('kind', '')).removesuffix('_subscription')
        reason = blocked(p)
        if not p.get('routing_enabled', True):
            reason = 'Исключена из автовыбора'
        elif not in_schedule(p):
            reason = 'Вне часов работы'
        elif not reason and pid not in eligible:
            reason = 'Отключена сетевыми настройками'
        rows.append({'id': pid, 'available': not reason, 'reason': reason, 'quota': quota(provider) if p.get('kind', '').endswith('_subscription') else quota('api:'+key(p)),
                     'priority': p.get('priority', 9)})
    with _lock:
        selected = copy.deepcopy(_selected)
    return {'automatic': bool(llm.get('auto_select')), 'role': role,
            'next': ranked[0]['_profile_id'] if ranked else None,
            'selected': selected, 'profiles': rows, 'timezone': str(dt.datetime.now().astimezone().tzinfo)}
