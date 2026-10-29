"""Полный текстовый ход: настоящий процесс ядра, WebSocket и локальная SSE-модель.

Проверяет исходники либо --bundle. Платные модели, аккаунты и микрофон не используются.
"""
import asyncio
import json
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "core"))
from exam import Core

ANSWER = "Тестовый ответ Mac: связь работает."


class Model(BaseHTTPRequestHandler):
    hits = 0
    requests = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        if not body.get("messages"):
            self.send_error(400)
            return
        type(self).hits += 1
        type(self).requests.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in (({"role": "assistant", "content": ANSWER + f" Запрос {type(self).hits}."}, None), ({}, "stop")):
            event = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")


class QuotaExhausted(BaseHTTPRequestHandler):
    hits = 0
    def log_message(self, *args): pass
    def do_POST(self):
        type(self).hits += 1
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.send_response(429); self.send_header('Content-Type','application/json'); self.end_headers()
        self.wfile.write(b'{"error":{"message":"quota exhausted"}}')


class SilentModel(BaseHTTPRequestHandler):
    hits = 0
    def log_message(self, *args): pass
    def do_POST(self):
        type(self).hits += 1
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        time.sleep(7)  # Exceeds the configured 5-second read timeout; no response at all.


async def check():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="dewiar-chat-") as folder:
            core = Core({"kind": "openai", "model": "local-fixture", "name": "Local test",
                         "base_url": f"http://127.0.0.1:{server.server_port}/v1", "tools_mode": "native"}, folder)
            original = subprocess.Popen
            def spawn(command, *args, **kwargs):
                if "--bundle" in sys.argv and str(ROOT / "core/main.py") in command:
                    command = [str(ROOT / "dist/Dewiar.app/Contents/MacOS/Dewiar"), "--core", "--server"]
                return original(command, *args, **kwargs)
            try:
                with patch("subprocess.Popen", side_effect=spawn):
                    core.start()
                await core.connect()
                await core.call('settings_save', patch={'team': {'route': False}})
                answer, events = await core.say("Проверь связь с тестовой моделью.", timeout=25)
                if ANSWER not in answer or Model.hits < 1:
                    raise AssertionError(f"Неверный ответ: {answer!r}; запросов: {Model.hits}")
                threads = await core.call('chat_threads')
                first = threads['active']
                await core.say('Обсудим архитектуру проекта ПЕРВАЯ_КЕДР, нужен план разработки.', timeout=25)
                second = (await core.call('chat_select'))['active']
                assert second != first
                await core.say('Проверка второй беседы: ВТОРАЯ_БЕРЁЗА.', timeout=25)
                user_messages = [m.get('content', '') for m in Model.requests[-1]['messages'] if m['role'] == 'user']
                assert not any('ПЕРВАЯ_КЕДР' in str(text) for text in user_messages), 'First chat context leaked into second'
                await core.call('chat_select', chat_id=first)
                await core.say('Продолжи первый разговор.', timeout=25)
                user_messages = [m.get('content', '') for m in Model.requests[-1]['messages'] if m['role'] == 'user']
                assert any('ПЕРВАЯ_КЕДР' in str(text) for text in user_messages), 'Selected chat context was not restored'
                assert not any('ВТОРАЯ_БЕРЁЗА' in str(text) for text in user_messages), 'Second chat context leaked into first'
                first_items = (await core.call('chat_list', chat_id=first))['items']
                assert len(first_items) >= 6
                await core.call('chat_manage', dialog_id=first, action='archive')
                assert not (await core.call('chat_list', chat_id=first))['items']
                archived = (await core.call('chat_list', archived=True, chat_id=first))['items']
                assert len(archived) == len(first_items)
                assert (await core.call('chat_list', chat_id=second))['items'], 'Archiving removed another chat'
                await core.call('chat_manage', dialog_id=first, action='restore', archived=True)
                await core.call('chat_select', chat_id=first)
                assert len((await core.call('chat_list', chat_id=first))['items']) == len(first_items)
                await core.call('chat_manage', dialog_id=first, action='delete')
                assert not (await core.call('chat_list', chat_id=first))['items']
                assert (await core.call('chat_list', chat_id=second))['items']
                bad = ThreadingHTTPServer(('127.0.0.1',0), QuotaExhausted)
                threading.Thread(target=bad.serve_forever,daemon=True).start()
                try:
                    profiles = {
                        'bad':dict(core.profile,base_url=f'http://127.0.0.1:{bad.server_port}/v1',priority=1),
                        'good':dict(core.profile,priority=2),
                    }
                    await core.call('settings_save',patch={'llm':{'profiles':profiles,'auto_select':True,'active':'good'}})
                    answer, _ = await core.say('Проверь автоматическое переключение при исчерпании лимита.', timeout=25)
                    assert ANSWER in answer and QuotaExhausted.hits >= 1
                    routing = await core.call('models_routing_status')
                    assert routing['selected']['id'] == 'good', 'Fallback model not recorded'
                    assert next(x for x in routing['profiles'] if x['id']=='bad')['reason'], 'Exhausted model has no visible state'
                finally:
                    bad.shutdown(); bad.server_close()
                silent = ThreadingHTTPServer(('127.0.0.1',0), SilentModel)
                threading.Thread(target=silent.serve_forever,daemon=True).start()
                try:
                    profiles['bad'] = dict(core.profile,base_url=f'http://127.0.0.1:{silent.server_port}/v1',priority=1,timeout=5)
                    await core.call('settings_save',patch={'llm':{'profiles':profiles}})
                    answer, _ = await core.say('Проверь резервную модель, если первая совсем не отвечает.', timeout=25)
                    assert ANSWER in answer and SilentModel.hits >= 1
                    assert (await core.call('models_routing_status'))['selected']['id'] == 'good'
                finally:
                    silent.shutdown(); silent.server_close()
                print("OK: real core chat isolation, separate model contexts, archive/restore/delete and local inference")
            except Exception:
                log = Path(folder) / "core.log"
                if log.exists():
                    print(log.read_text(errors="replace")[-4000:])
                raise
            finally:
                await core.stop()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    asyncio.run(check())
