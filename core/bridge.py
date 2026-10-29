"""Локальный WebSocket 127.0.0.1:8765 между ядром и интерфейсом.

Протокол — JSON:
  клиент → {"cmd": "hello", "token": "..."}      (первым сообщением, иначе разрыв)
  клиент → {"id": 7, "cmd": "persona_set", "args": {...}}
  ядро   → {"type": "hello", "data": ...}                 (ответ на верный токен — затем обычный обмен)
  ядро   → {"type": "reply", "id": 7, "ok": true, "data": ...}   или  {"ok": false, "error": "…"}
  ядро   → {"type": "state"|"level"|"heard"|"answer"|...}   (рассылка всем)

Токен обязателен: к 127.0.0.1 может подключиться любая программа или страница в браузере на этом компьютере,
а команды ядра управляют компьютером. Страница звонка «Близким» в браузере (ui/call.html) получает свой временный токен
(allow): ей доступны только команды и события звонка. Команды выполняются в пуле потоков (handler), рассылка — broadcast()
из любого потока. Ответ на «ping» — без пула, чтобы показать, чем ядро занято, даже когда пул забит.
"""

import asyncio
import hmac
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("dewiar.bridge")


def _probe_noise(rec):
    """Окно проверяет, живо ли ядро, простым подключением к порту (ui/app.py core_alive) и сразу закрывает его —
    websockets пишет на это ERROR «opening handshake failed» (19.10: 22 «сбоя» за ночь в отчётах). Это не сбой:
    такие записи не пропускаем; и обычный HTTP-запрос на порт (InvalidUpgrade — открыли адрес в браузере). Остальные
    ошибки рукопожатия остаются."""
    if rec.getMessage() != "opening handshake failed" or not rec.exc_info:
        return True
    e = rec.exc_info[1]
    chain = [e, getattr(e, "__cause__", None), getattr(e, "__context__", None)]
    return not any(type(x).__name__ in ("InvalidMessage", "InvalidUpgrade", "EOFError", "ConnectionResetError", "IncompleteReadError") for x in chain if x)


logging.getLogger("websockets.server").addFilter(_probe_noise)


class Bridge:
    def __init__(self, token, host="127.0.0.1", port=8765):
        self.token, self.host, self.port = token, host, port
        self.clients = set()
        self.seen = False          # окно хоть раз подключалось: значит, интерфейс есть (см. Engine._ui_wake)
        self.loop = None
        self.handler = None           # fn(cmd, args) -> data (вызывается в пуле потоков)
        self.ready = threading.Event()
        # Свой большой пул: в стандартном (ядер + 4) медленные команды (сеть, проверки) занимают все потоки,
        # и остальные кнопки интерфейса ждут.
        self.pool = ThreadPoolExecutor(max_workers=48, thread_name_prefix="cmd")
        self.inflight = {}            # id вызова → (команда, когда начата) — для «ping»: чем ядро занято
        self.limited = {}             # временный токен → (разрешённые команды, до когда) — страница звонка в браузере
        self.limited_ws = {}          # подключение с временным токеном → разрешённые команды

    LIMITED_EVENTS = ("close_sig", "call_end")

    def allow(self, token, cmds, seconds=3600):
        """Временный токен для страницы звонка: только эти команды и события звонка, час."""
        self.limited[token] = (set(cmds), time.time() + seconds)

    def start(self):
        """Запустить сервер в своём потоке со своим циклом asyncio; ждём готовности не дольше 5 секунд."""
        threading.Thread(target=self._run, name="bridge", daemon=True).start()
        self.ready.wait(5)

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._serve())

    async def _serve(self):
        try:
            from websockets.asyncio.server import serve
        except ImportError:                                  # websockets < 13: нового модуля asyncio.server ещё нет
            from websockets import serve
        # WebKit/macOS иногда повреждает контекст permessage-deflate при
        # параллельных событиях (особенно audio_enable: settings + status +
        # audio_mode). Канал слушает только 127.0.0.1, поэтому сжатие здесь
        # не даёт выигрыша, зато приводит к «zlib: incorrect header check».
        async with serve(self._client, self.host, self.port, max_size=16 * 1024 * 1024,
                         compression=None):   # 16 МБ: файлы и картинки из интерфейса
            log.info("Интерфейс может подключаться: ws://%s:%d", self.host, self.port)
            self.ready.set()
            await asyncio.Future()

    async def _client(self, ws, *_):
        """Одно подключение: первым сообщением (за 5 секунд) — токен, сравнение за постоянное время (hmac).
        Неверный — закрываем с кодом 4001. Каждая команда обрабатывается отдельно, не задерживая следующие."""
        try:
            first = json.loads(await asyncio.wait_for(ws.recv(), 5))
            tok = str(first.get("token", ""))
            lim = next((v for k, v in list(self.limited.items()) if hmac.compare_digest(tok, k) and v[1] > time.time()), None)
            if lim is None and not hmac.compare_digest(tok, self.token):
                await ws.close(code=4001, reason="bad token")
                return
        except Exception:  # noqa: BLE001
            return
        if lim is not None:                           # страница звонка: без снимка состояния, только своё
            try:
                await ws.send(json.dumps({"type": "hello", "data": {"limited": True}}))
                self.limited_ws[ws] = lim[0]
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if msg.get("cmd") not in lim[0]:
                        await ws.send(json.dumps({"type": "reply", "id": msg.get("id"), "ok": False, "error": "недоступно"}))
                        continue
                    asyncio.ensure_future(self._reply(ws, msg))
            except Exception:  # noqa: BLE001
                pass
            finally:
                self.limited_ws.pop(ws, None)
            return
        try:
            data = await self._call("hello", {})
            await ws.send(json.dumps({"type": "hello", "data": data}, ensure_ascii=False))
            self.clients.add(ws)
            self.seen = True
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                asyncio.ensure_future(self._reply(ws, msg))
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.clients.discard(ws)

    async def _call(self, cmd, args):
        """Выполнить команду в пуле потоков, отмечая её в inflight на время выполнения."""
        key = object()
        self.inflight[key] = (cmd, time.monotonic())
        try:
            return await self.loop.run_in_executor(self.pool, self.handler, cmd, args)
        finally:
            self.inflight.pop(key, None)

    def busy(self):
        """Ответ на «ping» прямо из цикла связи (без пула): жив ли и что выполняется дольше всех."""
        now = time.monotonic()
        slow = sorted(((now - t, c) for c, t in list(self.inflight.values())), reverse=True)
        return {"ok": True, "inflight": len(slow), "oldest": round(slow[0][0], 1) if slow else 0,
                "cmd": slow[0][1] if slow else ""}

    async def _reply(self, ws, msg):
        mid = msg.get("id")
        try:
            data = self.busy() if msg.get("cmd") == "ping" else await self._call(msg.get("cmd"), msg.get("args") or {})
            out = {"type": "reply", "id": mid, "ok": True, "data": data}
        except Exception as e:  # noqa: BLE001
            if isinstance(e, (AttributeError, TypeError, KeyError, IndexError, NameError, ZeroDivisionError, AssertionError)):
                log.error("Команда %s упала: %s", msg.get("cmd"), e, exc_info=True)    # ошибка в программе — в отчёт разработчику
            else:
                log.warning("Команда %s: %s", msg.get("cmd"), e)
            out = {"type": "reply", "id": mid, "ok": False, "error": str(e)}
        try:
            await ws.send(json.dumps(out, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            pass

    def broadcast(self, event: dict):
        """Разослать событие всем интерфейсам. Можно звать из любого потока: отправка уходит в цикл связи.
        Клиент, которому не отправилось, убирается из списка."""
        if not self.loop or not (self.clients or self.limited_ws):
            return
        text = json.dumps(event, ensure_ascii=False)
        extra = list(self.limited_ws) if event.get("type") in self.LIMITED_EVENTS else []

        async def send_all():
            for ws in list(self.clients) + extra:
                try:
                    await ws.send(text)
                except Exception:  # noqa: BLE001
                    self.clients.discard(ws)
        asyncio.run_coroutine_threadsafe(send_all(), self.loop)
