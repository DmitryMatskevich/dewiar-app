"""Переписка с персонажами для студии («Чат»): что сказал или написал человек и что ответил ассистент — голосом, текстом,
из Telegram. Отдельно от памяти модели (conversation.json сворачивается и сбрасывается через 8 часов тишины) — здесь
беседы на этом компьютере (state/chat.jsonl). Архивы сжаты отдельно и загружаются только по запросу.

Записи: {"id", "ts", "role": "user"|"assistant", "text", "who"?: pid персонажа, "via"?: "message", "att"?: вложения
[{kind: image|file, name, path (во «Входящих» папки ассистента), thumb (миниатюра картинки, data:URL)}]}.
Ответ модели в одном ходе приходит одной репликой; если подряд пришло продолжение того же ответа (текст начинается с
прежнего) — запись обновляется, а не дублируется. Продиктованные для сейфа секреты маскируются, как в журнале."""
import json
import gzip
import hashlib
import threading
import time
import unicodedata
import uuid
from pathlib import Path

class ChatLog:
    def __init__(self, path):
        self.path = Path(path)
        self.archive_dir = self.path.parent / "chat-archive"
        self.current_path = self.path.parent / "chat-current.json"
        try:
            self.active = str(json.loads(self.current_path.read_text())["active"])
        except (OSError, ValueError, KeyError):
            self.active = "legacy"
        self._lock = threading.Lock()
        self.items = []
        self._load()

    def _load(self):
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                if isinstance(x, dict) and (x.get("text") or x.get("att")) and x.get("role") in ("user", "assistant"):
                    self.items.append(x)
        except OSError:
            pass
        # Retention is explicit: restoring an older chat must never silently discard it.

    def _write_all(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in self.items), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, role, text, who=None, via=None, att=None):
        """-> запись (новая или обновлённая) или None, если писать нечего. att — вложения: [{kind, name, path, thumb?}]."""
        text = (text or "").strip()
        if (not text and not att) or role not in ("user", "assistant"):
            return None
        try:
            from vault import mask
            text = mask(text) if role == "user" else text
        except Exception:  # noqa: BLE001
            pass
        now = time.time()
        with self._lock:
            last = self.items[-1] if self.items else None
            # тот же ответ дописался (или повторился) — обновляем запись, а не плодим одинаковые
            if not att and last and last.get("chat_id", "legacy") == self.active and last["role"] == role and now - last["ts"] < 180 and last.get("who") == who and \
                    (text == last["text"] or (role == "assistant" and text.startswith(last["text"]))):
                last["text"], last["ts"] = text, now
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._write_all()
                return dict(last, updated=True)
            item = {"id": uuid.uuid4().hex, "chat_id": self.active, "ts": now, "role": role, "text": text[:20000]}
            if who:
                item["who"] = who
            if via:
                item["via"] = via
            if att:
                item["att"] = att[:10]
            self.items.append(item)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
            return dict(item)

    @staticmethod
    def _groups(items):
        groups = {}
        for item in items:
            key = item.get("chat_id", "legacy")
            groups.setdefault(key, []).append(item)
        return groups

    def context_path(self, chat_id=None):
        chat_id = self.active if chat_id is None else chat_id
        if chat_id == "legacy":
            return self.path.parent / "conversation.json"
        return self.path.parent / "chat-contexts" / (hashlib.sha256(chat_id.encode()).hexdigest() + ".json")

    def select(self, chat_id=None):
        with self._lock:
            if chat_id is None:
                chat_id = uuid.uuid4().hex
            elif chat_id != self.active and chat_id not in self._groups(self.items):
                raise ValueError("Чат не найден в основной переписке")
            self.current_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.current_path.with_suffix('.tmp')
            tmp.write_text(json.dumps({'active': chat_id}), encoding='utf-8')
            tmp.replace(self.current_path)
            self.active = chat_id
            return chat_id

    def threads(self, archived=False):
        page = self.page(limit=500, archived=archived, all_items=True)
        groups = self._groups(page['items'])
        rows = []
        for key, items in groups.items():
            first = next((x for x in items if x['role'] == 'user'), items[0])
            rows.append({'id': key, 'title': (first.get('text') or 'Чат с вложениями')[:90], 'count': len(items), 'ts': items[-1]['ts']})
        if not archived and self.active not in groups:
            rows.append({'id': self.active, 'title': 'Новый чат', 'count': 0, 'ts': time.time()})
        return {'items': sorted(rows, key=lambda x: x['ts'], reverse=True), 'active': self.active}

    def _archive_path(self, dialog_id):
        return self.archive_dir / (hashlib.sha256(str(dialog_id).encode()).hexdigest() + ".json.gz")

    def _read_archive(self, path):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        items = payload.get('items') if isinstance(payload, dict) else payload
        if not isinstance(items, list) or not items or any(not isinstance(x, dict) or not x.get("id") or x.get("role") not in ("user", "assistant") for x in items):
            raise ValueError("Повреждён архив переписки: " + path.name)
        return items

    def manage(self, dialog_id, action, archived=False):
        """Move/delete a whole conversation, including its saved model context."""
        if action not in ("archive", "restore", "delete") or not dialog_id:
            raise ValueError("Неизвестное действие с перепиской")
        with self._lock:
            path = self._archive_path(dialog_id)
            if archived:
                if action == "archive":
                    raise ValueError("Переписка уже в архиве")
                if not path.exists():
                    raise ValueError("Переписка не найдена в архиве")
                rows = self._read_archive(path)
                if rows[0].get("chat_id", "legacy") != dialog_id:
                    raise ValueError("Неверный идентификатор переписки")
                if action == "restore":
                    with gzip.open(path, 'rt', encoding='utf-8') as f:
                        payload = json.load(f)
                    context = payload.get('context') if isinstance(payload, dict) else None
                    if context is not None:
                        target = self.context_path(dialog_id)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        tmp = target.with_suffix('.tmp')
                        tmp.write_text(json.dumps(context, ensure_ascii=False), encoding='utf-8')
                        tmp.replace(target)
                    old = self.items
                    ids = {x["id"] for x in old}
                    self.items = sorted(old + [x for x in rows if x["id"] not in ids], key=lambda x: (x["ts"], x["id"]))
                    try:
                        self._write_all()
                    except Exception:
                        self.items = old
                        raise
                path.unlink()  # Only after the active log was saved successfully.
                return {"count": len(rows)}
            if action == "restore":
                raise ValueError("Восстановление доступно из архива")
            rows = self._groups(self.items).get(dialog_id)
            if not rows:
                raise ValueError("Переписка не найдена — обновите ленту")
            if action == "archive":
                self.archive_dir.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                context_path = self.context_path(dialog_id)
                context = json.loads(context_path.read_text(encoding='utf-8')) if context_path.exists() else None
                with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=9) as f:
                    json.dump({'items': rows, 'context': context}, f, ensure_ascii=False)
                tmp.replace(path)  # Save the complete archive before removing anything.
            ids = {x["id"] for x in rows}
            old = self.items
            self.items = [x for x in old if x["id"] not in ids]
            try:
                self._write_all()
            except Exception:
                self.items = old
                raise  # A saved archive is retained as a recovery copy on I/O errors.
            self.context_path(dialog_id).unlink(missing_ok=True)
            return {"count": len(rows)}

    def page(self, before=None, limit=80, q="", archived=False, chat_id=None, all_items=False):
        """Последние limit реплик (до записи before, если задана) — по времени, старые сверху; q — поиск по словам."""
        with self._lock:
            if archived:
                active_ids = {x["id"] for x in self.items}
                items = []
                for path in sorted(self.archive_dir.glob("*.json.gz")):
                    rows = self._read_archive(path)
                    items.extend(dict(x, dialog_id=rows[0].get("chat_id", "legacy")) for x in rows if x["id"] not in active_ids)
                items.sort(key=lambda x: (x["ts"], x["id"]))
            else:
                items = [dict(x, dialog_id=key) for key, rows in self._groups(self.items).items() for x in rows]
            if chat_id is not None:
                items = [x for x in items if x.get('chat_id', 'legacy') == chat_id]
            total = len(items)
        if before:
            i = next((k for k, x in enumerate(items) if x["id"] == before), len(items))
            items = items[:i]
        if q:
            norm = lambda text: unicodedata.normalize("NFC", text).casefold()
            ql = norm(q)
            items = [x for x in items if ql in norm(x["text"])]
        limit = max(1, min(500, int(limit or 80)))
        return {"items": items if all_items else items[-limit:], "more": False if all_items else len(items) > limit, "total": total, "matched": len(items)}

    def clear(self):
        with self._lock:
            if self.path.exists():
                self.path.unlink()
            self.items = []
