"""Голоса Vosk TTS в отдельном процессе (его запускает и с ним говорит tts.VoskProc).

Зачем: загрузка модели (сессии onnxruntime и словарь ударений на сотни тысяч строк) и сам синтез держат GIL Python
десятки секунд. В том же процессе ядро всё это время не отвечало: студия пустела («нет связи с ядром»), виджет
писал «загружаю слух и голос» больше минуты после каждого запуска. Здесь они никому не мешают.

Словарь ударений (миллионы строк) — на диске: после первого запуска он лежит рядом с моделью в dewiar-dic.sqlite
(сверяется по размеру и времени файла dictionary), процесс не держит его в памяти (SqlDic; раньше — 1,6 ГБ на процесс).

Обмен по stdin/stdout кадрами: 4 байта длины (big-endian) + содержимое. Запрос — JSON {"text", "speaker_id",
"speech_rate"}; ответ — JSON {"ok": true, "n": байт} и следом кадр с PCM int16 22 050 Гц, или {"ok": false, "error"}.
Первым делом worker шлёт {"ok": true, "ready": true} (модель загружена) или {"ok": false, "error"}."""
import io
import json
import os
import pickle
import struct
import sys
from pathlib import Path


def _frame_out(out, data):
    out.write(struct.pack(">I", len(data)) + data)
    out.flush()


def _frame_in(inp):
    h = inp.read(4)
    if len(h) < 4:
        return None
    n = struct.unpack(">I", h)[0]
    return inp.read(n)


class SqlDic:
    """Словарь ударений на диске (sqlite рядом с моделью) вместо словаря Python в памяти. Отчёт 11.10: процесс голосов
    Vosk занимал 1,6 ГБ — большую часть давал словарь на миллионы строк (у каждой строки в Python — сотни байт
    служебного). Синтезу нужны только «слово in dic» и dic[слово] для нескольких слов фразы — с диска это доли
    миллисекунды, а частые слова держит небольшой кэш."""

    def __init__(self, db):
        import sqlite3
        self.c = sqlite3.connect(str(db))
        self.c.execute("PRAGMA query_only = 1")
        self.cache = {}

    def get(self, w, default=None):
        if w in self.cache:
            v = self.cache[w]
        else:
            r = self.c.execute("SELECT p FROM d WHERE w = ?", (w,)).fetchone()
            v = r[0] if r else None
            if len(self.cache) > 20000:
                self.cache.clear()
            self.cache[w] = v
        return default if v is None else v

    def __contains__(self, w):
        return self.get(w) is not None

    def __getitem__(self, w):
        v = self.get(w)
        if v is None:
            raise KeyError(w)
        return v

    def __len__(self):
        return self.c.execute("SELECT COUNT(*) FROM d").fetchone()[0]


def build_dic(dic_file, db, key):
    """dictionary модели («слово вероятность фонемы») -> sqlite: на каждое слово — вариант с наибольшей вероятностью,
    как в vosk_tts.Model. Пишется во временный файл и подменяет готовый целиком: оборванная сборка не испортит кэш."""
    import sqlite3
    tmp = db.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    c = sqlite3.connect(str(tmp))
    c.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;"
                    "CREATE TABLE d (w TEXT PRIMARY KEY, prob REAL, p TEXT) WITHOUT ROWID; CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);")
    sql = "INSERT INTO d VALUES (?, ?, ?) ON CONFLICT(w) DO UPDATE SET prob = excluded.prob, p = excluded.p WHERE excluded.prob > d.prob"
    batch = []
    with open(dic_file, encoding="utf-8") as f:
        for line in f:
            items = line.split(maxsplit=2)
            if len(items) < 3:
                continue
            batch.append((items[0], float(items[1]), items[2]))
            if len(batch) >= 50000:
                c.executemany(sql, batch)
                batch.clear()
    if batch:
        c.executemany(sql, batch)
    c.execute("INSERT INTO meta VALUES ('key', ?)", (json.dumps(key),))
    c.commit()
    c.close()
    tmp.replace(db)


def _sql_dic(dic_file, db, key):
    """Готовый словарь на диске (собранный для этого же dictionary) или собрать. -> SqlDic или None (не вышло)."""
    import sqlite3
    try:
        ok = False
        if db.exists():
            try:
                c = sqlite3.connect(str(db))
                ok = json.loads(c.execute("SELECT v FROM meta WHERE k = 'key'").fetchone()[0]) == key
                c.close()
            except Exception:  # noqa: BLE001 — испорченный кэш: соберём заново
                ok = False
        if not ok:
            build_dic(dic_file, db, key)
        return SqlDic(db)
    except Exception as e:  # noqa: BLE001
        print(f"словарь ударений на диске не вышел: {e} — держу в памяти", file=sys.stderr)
        return None


def load_model(path):
    """vosk_tts.Model со словарём ударений на диске (SqlDic): модель грузится без разбора dictionary, словарь — из
    dewiar-dic.sqlite рядом с моделью (собирается один раз, сверяется по размеру и времени файла). Не вышло — как
    раньше: словарь в памяти (с кэшем dewiar-dic.pickle). Если устройство библиотеки иное (словарь читается не через
    open в её модуле), модель грузится как обычно."""
    try:                                                     # DEWIAR_ORT_THREADS — своё число потоков голосов (ortcfg)
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ortcfg
        ortcfg.limit()
    except Exception:  # noqa: BLE001
        pass
    import vosk_tts.model as vm
    from vosk_tts import Model
    path = Path(path)
    dic_file, cache = path / "dictionary", path / "dewiar-dic.pickle"
    try:
        st = dic_file.stat()
        key = [st.st_size, int(st.st_mtime)]
    except OSError:
        return Model(model_path=str(path))
    dic = _sql_dic(dic_file, path / "dewiar-dic.sqlite", key) if os.environ.get("DEWIAR_TTS_DIC") != "memory" else None
    if dic is not None:
        try:
            cache.unlink(missing_ok=True)                   # прежний кэш в памяти больше не нужен
        except OSError:
            pass
    if dic is None and cache.exists():
        try:
            with open(cache, "rb") as f:
                saved = pickle.load(f)
            if saved.get("key") == key and isinstance(saved.get("dic"), dict):
                dic = saved["dic"]
        except Exception:  # noqa: BLE001 — испорченный кэш: разберём словарь заново
            dic = None
    if dic is None:
        m = Model(model_path=str(path))
        if isinstance(getattr(m, "dic", None), dict) and m.dic:
            try:
                tmp = cache.with_suffix(".tmp")
                with open(tmp, "wb") as f:
                    pickle.dump({"key": key, "dic": m.dic}, f, protocol=pickle.HIGHEST_PROTOCOL)
                tmp.replace(cache)
            except OSError:
                pass
        return m
    real_open = open

    def fake_open(f, *a, **kw):                  # словарь не разбираем — он уже есть в кэше
        if Path(str(f)) == dic_file:
            return io.StringIO("")
        return real_open(f, *a, **kw)
    vm.open = fake_open
    try:
        m = Model(model_path=str(path))
    finally:
        del vm.open
    m.dic = dic
    return m


def main():
    out, inp = sys.stdout.buffer, sys.stdin.buffer
    sys.stdout = sys.stderr                      # печать библиотек — в журнал, а не в канал обмена
    try:
        import numpy as np
        from vosk_tts import Synth
        synth = Synth(load_model(sys.argv[1]))
    except Exception as e:  # noqa: BLE001
        _frame_out(out, json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False).encode())
        return 1
    _frame_out(out, json.dumps({"ok": True, "ready": True}).encode())
    while True:
        raw = _frame_in(inp)
        if raw is None:
            return 0
        try:
            q = json.loads(raw)
            kw = {"speaker_id": int(q.get("speaker_id") or 0)}
            if q.get("speech_rate") is not None:
                kw["speech_rate"] = float(q["speech_rate"])
            audio = np.asarray(synth.synth_audio(q.get("text") or "", **kw), dtype=np.int16).tobytes()
        except Exception as e:  # noqa: BLE001
            _frame_out(out, json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False).encode())
            continue
        _frame_out(out, json.dumps({"ok": True, "n": len(audio)}).encode())
        _frame_out(out, audio)


if __name__ == "__main__":
    sys.exit(main())
