"""Official local subscription runtimes. No API-key bridge or credential copying.

Codex has a dedicated CLI home (vendor-owned login state, outside Dewiar backups).
Claude uses its own credential store with settings, hooks, MCP and tools disabled.
Only text is returned; Dewiar's existing guarded tool protocol handles actions.
"""
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time

KINDS = {'codex_subscription', 'claude_subscription'}


def is_subscription(p):
    return p.get('kind') in KINDS


def _check_network():
    import egress
    if egress.local_only():
        raise RuntimeError('Включён режим «только локально». Подписки отправляют запросы в OpenAI / Anthropic. Разрешите внешние подключения в «Безопасность».')


def executable(provider):
    if provider not in ('codex', 'claude'):
        raise ValueError('Неизвестная подписка')
    for path in [shutil.which(provider), str(Path.home() / '.local/bin' / provider),
                 '/opt/homebrew/bin/' + provider, '/usr/local/bin/' + provider]:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise RuntimeError(f'Не установлен официальный CLI {provider}. Установите его и повторите подключение.')


def _env(provider):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('ANTHROPIC_', 'CLAUDE_CODE_', 'OPENAI_', 'CODEX_'))
           and k not in ('CLAUDECODE',)}
    if provider == 'codex':
        home = Path.home() / '.codex' / 'dewiar-subscription'
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        env['CODEX_HOME'] = str(home)
    return env


class Session:
    def __init__(self, provider, extra=()):
        _check_network()
        cli = executable(provider)
        self.provider = provider
        self.tmp = tempfile.TemporaryDirectory(prefix='dewiar-subscription-')
        self.q = queue.Queue()
        self.seq = 0
        self.pending = []
        self.closed = False
        if provider == 'codex':
            cmd = [cli, 'app-server', '-c', 'model_provider="openai"',
                   '-c', 'cli_auth_credentials_store="file"', '-c', 'web_search="disabled"',
                   '-c', 'project_doc_max_bytes=0',
                   '-c', 'permissions.dewiar_text.filesystem={":workspace_roots"="read"}',
                   '-c', 'permissions.dewiar_text.network.enabled=false',
                   '-c', 'default_permissions="dewiar_text"']
            for feature in ('shell_tool', 'unified_exec', 'apps', 'browser_use', 'computer_use',
                            'plugins', 'hooks', 'image_generation', 'view_image', 'multi_agent', 'sleep_tool', 'tool_suggest'):
                cmd += ['-c', f'features.{feature}=false']
        else:
            cmd = [cli, '--safe-mode', '-p', '--input-format', 'stream-json', '--output-format',
                   'stream-json', '--verbose', '--include-partial-messages', '--tools', '',
                   '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '']
        try:
            self.proc = subprocess.Popen(cmd + list(extra), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                                         cwd=self.tmp.name, env=_env(provider), start_new_session=True)
        except Exception:
            self.tmp.cleanup()
            raise
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            for line in self.proc.stdout:
                try:
                    self.q.put(json.loads(line))
                except ValueError:
                    self.q.put({'_invalid': True})
        finally:
            self.q.put({'_eof': True})

    def send(self, obj):
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + '\n')
        self.proc.stdin.flush()

    def event(self, deadline, cancel=None):
        _check_network()
        if cancel and cancel.is_set():
            raise InterruptedError("Запрос отменён")
        if self.pending:
            return self.pending.pop(0)
        while time.monotonic() < deadline:
            _check_network()  # Also stop if privacy changes while a request is running.
            if cancel and cancel.is_set():
                raise InterruptedError('Запрос отменён')
            try:
                obj = self.q.get(timeout=min(.1, max(.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if obj.get('_eof') or obj.get('_invalid'):
                raise RuntimeError('CLI завершился или вернул неподдерживаемый ответ. Проверьте вход и версию CLI.')
            if obj.get('type') == 'control_request':
                raise RuntimeError('Claude Code запросил управление инструментом; выполнение остановлено.')
            # Never approve or execute tools requested by the runtime.
            if 'method' in obj and 'id' in obj:
                self.send({'id': obj['id'], 'error': {'code': -32601, 'message': 'Tools disabled in Dewiar model adapter'}})
                raise RuntimeError('CLI запросил действие вместо текстового ответа; выполнение остановлено.')
            return obj
        raise TimeoutError('CLI не ответил вовремя. Проверьте вход, сеть и лимит подписки.')

    def rpc(self, method, params=None, timeout=25):
        self.seq += 1
        req = self.seq
        self.send({'id': req, 'method': method, 'params': params or {}})
        deadline = min(time.monotonic() + timeout, getattr(self, 'deadline', float('inf')))
        pending = self.pending
        self.pending = []
        while True:
            obj = self.event(deadline)
            if obj.get('id') == req:
                self.pending = pending + self.pending
                if obj.get('error'):
                    raise RuntimeError('CLI отклонил запрос: ' + str(obj['error'].get('message', 'ошибка'))[:400])
                return obj.get('result') or {}
            pending.append(obj)

    def initialize(self):
        if self.provider == 'codex':
            self.rpc('initialize', {'clientInfo': {'name': 'dewiar', 'version': '0.1'},
                                    'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized'})
            return {}
        self.send({'type': 'control_request', 'request_id': 'init',
                   'request': {'subtype': 'initialize', 'hooks': {}}})
        deadline = min(time.monotonic() + 25, getattr(self, 'deadline', float('inf')))
        while True:
            obj = self.event(deadline)
            if obj.get('type') == 'control_response':
                r = obj.get('response', {})
                if r.get('subtype') != 'success':
                    raise RuntimeError('Claude Code не поддерживает безопасный режим подключения. Обновите CLI.')
                return r.get('response') or {}

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=3)
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.tmp.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _claude_info(init):
    account = init.get('account') or {}
    plan = account.get('subscriptionType') or ''
    # A CLI installation or API account is not proof of a subscription.
    ok = any(x in plan.lower() for x in ('max', 'pro', 'team', 'enterprise'))
    models = [{'id': m['value'], 'name': m.get('displayName', m['value']), 'efforts': []}
              for m in init.get('models', []) if m.get('value')]
    return {'authenticated': ok, 'plan': plan, 'models': models}


def _codex_limits(result):
    import modelrouter
    buckets = []
    snapshots = result.get('rateLimitsByLimitId') or {'codex': result.get('rateLimits') or {}}
    for key, data in snapshots.items():
        if not isinstance(data, dict):
            continue
        windows = []
        for name in ('primary', 'secondary'):
            window = data.get(name) or {}
            used = window.get('usedPercent')
            if isinstance(used, (int, float)):
                windows.append({'name': name, 'remaining_percent': max(0, min(100, 100-used)),
                                'minutes': window.get('windowDurationMins'), 'resets_at': window.get('resetsAt')})
        credit = data.get('credits') or {}
        buckets.append({'id': key, 'name': data.get('limitName') or key, 'windows': windows,
                        'credit_balance': credit.get('balance'), 'unlimited': credit.get('unlimited', False)})
    return modelrouter.quota('codex', {'buckets': buckets, 'source':'Codex app-server', 'scope':'account'})


def _claude_limits(info):
    import modelrouter
    buckets = []
    unified = info.get('unifiedWindows') or {info.get('rateLimitType') or 'shared': info}
    for name, window in unified.items():
        used = window.get('utilization')
        rejected = info.get('status') == 'rejected' and (not info.get('unifiedWindows') or name == info.get('rateLimitType'))
        remaining = max(0, min(100, 100-used*100)) if isinstance(used, (int, float)) else (0 if rejected else None)
        buckets.append({'id':'shared' if name in ('shared','five_hour','seven_day') else name, 'name':name,
                        'windows':[{'name': name, 'remaining_percent':remaining, 'resets_at':window.get('resetsAt')}],
                        'status':info.get('status')})
    return modelrouter.quota('claude', {'buckets': buckets, 'source':'Claude Code rate_limit_event', 'scope':'account'})


def read_limits(provider):
    if provider == 'claude':
        import modelrouter
        return modelrouter.quota('claude')
    if provider != 'codex':
        raise ValueError('Неизвестная подписка')
    with Session('codex') as session:
        session.initialize()
        return _codex_limits(session.rpc('account/rateLimits/read', {}, timeout=12))


def status(provider):
    path = executable(provider)
    with Session(provider) as session:
        init = session.initialize()
        if provider == 'claude':
            import modelrouter
            return dict(_claude_info(init), installed=True, provider=provider, limits=modelrouter.quota('claude'))
        account = session.rpc('account/read', {'refreshToken': False}).get('account') or {}
        models, cursor = [], None
        while True:
            result = session.rpc('model/list', {'limit': 100, 'includeHidden': False, 'cursor': cursor})
            models += [{'id': m.get('model') or m['id'], 'name': m.get('displayName', m['id']),
                        'efforts': [e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])]}
                       for m in result.get('data', [])]
            cursor = result.get('nextCursor')
            if not cursor:
                break
        limits = {}
        if account.get('type') == 'chatgpt':
            try:
                limits = _codex_limits(session.rpc('account/rateLimits/read', {}, timeout=10))
            except Exception:
                import modelrouter
                limits = modelrouter.quota('codex')
        return {'installed': bool(path), 'provider': provider, 'authenticated': account.get('type') == 'chatgpt',
                'plan': account.get('planType', ''), 'models': models, 'limits': limits}


_logins = {}
_login_lock = threading.Lock()


def _await_codex_login(session, login_id):
    deadline = time.monotonic() + 600
    while True:
        obj = session.event(deadline)
        if obj.get('method') != 'account/login/completed':
            continue
        params = obj.get('params') or {}
        if params.get('loginId') != login_id:
            continue
        if not params.get('success'):
            raise RuntimeError('Codex отклонил вход. Повторите авторизацию.')
        account = session.rpc('account/read', {'refreshToken': False}).get('account') or {}
        if account.get('type') != 'chatgpt':
            raise RuntimeError('Вход не подтверждён Codex. Повторите авторизацию.')
        return {'success': True}


def login(provider, on_complete=None):
    """Codex OAuth lives in official app-server until callback; no tokens returned to UI."""
    _check_network()
    if provider == 'claude':
        executable(provider)
        return {'terminal_login': True}
    if provider != 'codex':
        raise ValueError('Неизвестная подписка')
    with _login_lock:
        if provider in _logins:
            return {'url': _logins[provider][1]}
        session = Session(provider)
        try:
            session.initialize()
            result = session.rpc('account/login/start', {'type': 'chatgpt'})
            url = result.get('authUrl', '')
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname not in ('auth.openai.com', 'chatgpt.com'):
                raise RuntimeError('CLI вернул неподдерживаемый адрес авторизации')
            _logins[provider] = (session, url)
        except Exception:
            session.close()
            raise
    def wait_login():
        try:
            outcome = _await_codex_login(session, result.get('loginId'))
        except TimeoutError:
            outcome = {'success': False, 'error': 'Время входа истекло. Нажмите «Войти» ещё раз.'}
        except Exception:
            outcome = {'success': False, 'error': 'Codex не подтвердил вход. Проверьте локальный режим и повторите авторизацию.'}
        finally:
            session.close()
            with _login_lock:
                _logins.pop(provider, None)
        if on_complete:
            on_complete(provider, outcome)
    threading.Thread(target=wait_login, daemon=True).start()
    return {'url': url}


def _transcript(messages):
    parts = []
    for m in messages:
        content = m.get('content')
        if isinstance(content, list):
            if any(c.get('type') != 'text' for c in content):
                raise RuntimeError('Подписка пока принимает только текст; выберите другой профиль для изображений.')
            content = '\n'.join(c.get('text', '') for c in content)
        parts.append(json.dumps({'role': m.get('role'), 'content': content, 'tool_calls': m.get('tool_calls'),
                                 'tool_call_id': m.get('tool_call_id')}, ensure_ascii=False))
    return 'История разговора в JSONL. Ответь на последний запрос с учётом истории:\n' + '\n'.join(parts)


def stream(p, system, messages, cancel=None):
    provider = p['kind'].split('_')[0]
    prompt = _transcript(messages)
    model = p.get('model')
    if not model:
        raise RuntimeError('Выберите модель подписки в разделе «Модели».')
    extra = ['--model', model, '--system-prompt', system] if provider == 'claude' else []
    with Session(provider, extra) as session:
        session.deadline = p.get('_routing_deadline') or time.monotonic() + float(p.get('timeout') or 60)
        init = session.initialize()
        if provider == 'codex':
            account = session.rpc('account/read', {'refreshToken': False}).get('account') or {}
            if account.get('type') != 'chatgpt':
                raise RuntimeError('Войдите в подписку Codex через «Модели» → «Войти». API-ключи здесь не используются.')
            thread = session.rpc('thread/start', {'model': model, 'cwd': session.tmp.name, 'ephemeral': True,
                                 'permissions': 'dewiar_text', 'approvalPolicy': 'never', 'baseInstructions': system,
                                 'developerInstructions': 'Return text only. Never use built-in tools. Actions may only be requested as text tool_call tags.'})
            params = {'threadId': thread['thread']['id'], 'input': [{'type': 'text', 'text': prompt}],
                      'permissions': 'dewiar_text'}
            if p.get('reasoning_effort'):
                params['effort'] = p['reasoning_effort']
            session.rpc('turn/start', params)
        else:
            if not _claude_info(init)['authenticated']:
                raise RuntimeError('В Claude Code не подтверждён вход по подписке. Выполните /login в официальном CLI.')
            session.send({'type': 'user', 'message': {'role': 'user', 'content': prompt}})
        deadline = session.deadline
        emitted = False
        while True:
            obj = session.event(deadline, cancel)
            if provider == 'codex':
                method, data = obj.get('method'), obj.get('params', {})
                if method == 'account/rateLimits/updated':
                    _codex_limits(data)
                if method == 'item/agentMessage/delta':
                    emitted = True
                    yield data.get('delta', '')
                elif method == 'turn/completed':
                    turn = data.get('turn') or {}
                    if turn.get('status') != 'completed':
                        raise RuntimeError('Codex: ' + str((turn.get('error') or {}).get('message') or turn.get('status'))[:400])
                    if not emitted:
                        raise RuntimeError('Codex завершил запрос без текстового ответа')
                    return
                elif method == 'error':
                    raise RuntimeError('Codex: ' + str((data.get('error') or {}).get('message', 'ошибка'))[:400])
            else:
                if obj.get('type') == 'rate_limit_event':
                    info = obj.get('rate_limit_info') or {}
                    _claude_limits(info)
                    if info.get('status') == 'rejected':
                        raise RuntimeError('Claude Code: лимит подписки исчерпан')
                if obj.get('type') == 'stream_event':
                    event = obj.get('event', {})
                    delta = event.get('delta', {})
                    if event.get('type') == 'content_block_delta' and delta.get('type') == 'text_delta':
                        emitted = True
                        yield delta.get('text', '')
                elif obj.get('type') == 'result':
                    if obj.get('is_error'):
                        error = str(obj.get('result') or obj.get('errors') or 'ошибка')
                        if any(x in error.lower() for x in ('oauth', 'authentication_error', 'please run /login')):
                            raise RuntimeError('Вход Claude Code истёк или отклонён. Нажмите «Войти», завершите /login в Terminal и повторите проверку.')
                        raise RuntimeError('Claude Code: ' + error[:400])
                    if not emitted:
                        if not obj.get('result'):
                            raise RuntimeError('Claude Code завершил запрос без текстового ответа')
                        yield obj['result']
                    return
