"""Размышления модели не попадают в ответ и в озвучку: <think>…</think>, рассуждение без открывающего тега
(…</think> ответ — Qwen3/DeepSeek-R1 с тегом в шаблоне), формат gpt-oss (analysis… assistantfinal …),
куски тегов на стыках; обычный ответ проходит как есть. Код и команды вслух не читаются. Итог задачи — по-русски."""
import os, sys
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
from llm import CodeFilter, SentenceSplitter, ThinkStripper  # noqa: E402
ok = lambda c, m: print(("✅ " if c else "❌ ") + m)


def strip(chunks):
    t = ThinkStripper()
    return "".join(t.feed(c) for c in chunks) + t.finish(), t.dropped


def chunked(s, n=7):
    return [s[i:i + n] for i in range(0, len(s), n)]


reason = ("Okay, the user is asking why the monitors connected through a USB-C hub stopped working after a hard reboot. "
          "Let me think about DisplayLink, evdi and kernel updates. I should answer in Russian and be brief.\n")
out, d = strip(chunked(reason + "</think>\n\nСкорее всего, не собрался драйвер DisplayLink под новое ядро."))
ok(out == "Скорее всего, не собрался драйвер DisplayLink под новое ядро." and d > 100, f"рассуждение без <think> вырезано: {out!r}")
out, _ = strip(chunked("<think>\nнадо ответить коротко\n</think>\n\nГотово, включил свет.", 3))
ok(out.strip() == "Готово, включил свет.", f"<think>…</think> кусками: {out!r}")
out, _ = strip(chunked("analysisThe user wants the weather. We should answer.assistantfinalСейчас плюс двенадцать и солнечно."))
ok(out == "Сейчас плюс двенадцать и солнечно.", f"gpt-oss: {out!r}")
out, _ = strip(["<|channel|>analysis<|message|>Need to answer.<|end|><|start|>assistant<|channel|>final<|message|>Привет!"])
ok(out == "Привет!", f"gpt-oss со служебными метками: {out!r}")
out, d = strip(chunked("Пользователь спрашивает про мониторы, надо коротко ответить. </think>Проверьте драйвер."))
ok(out == "Проверьте драйвер.", f"рассуждение по-русски: {out!r}")
normal = "Привет! Сейчас посмотрю, что с мониторами. Сначала выполните команду lsusb и пришлите вывод."
out, d = strip(chunked(normal))
ok(out == normal and d == 0, "обычный ответ проходит без потерь")
for s in ("Готово.", "Хорошо, включаю свет в спальне.", "Okay"):
    ok(strip([s])[0] == s, f"короткий ответ «{s}» не теряется")
out, d = strip(chunked("Okay, so the user wants me to fix the monitors. Let me analyze the hub and kernel situation. " * 8))
ok(out == "" and d > 400, "одно рассуждение без ответа — ответа нет (ядро повторит запрос без размышлений)")
t = ThinkStripper(); first = t.feed("Привет! Это длинный ответ, который начинается сразу и идёт дальше без всяких размышлений. ")
ok(first.startswith("Привет!"), "обычный ответ начинает звучать сразу, не дожидаясь конца")

# код вслух не читаем
cf, sp = CodeFilter(), SentenceSplitter()
text = "Проверьте так.\n```bash\nlsusb\nsudo dkms autoinstall\n```\nПотом перезагрузите компьютер.\n"
spoken = []
for c in chunked(text, 5):
    spoken += sp.feed(cf.feed(c))
spoken += sp.feed(cf.flush()) + sp.flush()
said = " ".join(spoken)
ok("dkms" not in said and "lsusb" not in said and "на экране" in said and "перезагрузите" in said, f"код вслух не читается: {said!r}")
cf = CodeFilter()
said = cf.feed("Выполните:\nsudo apt -f install\nИ всё.") + cf.flush()
ok("apt" not in said and "И всё." in said, "строка-команда вслух не читается")

# итог задачи по-русски
import tasks as T  # noqa: E402


class Host:
    def llm_profile(self):
        return {"base_url": "http://127.0.0.1:9/v1", "model": "x"}


r = T.TaskManager.__new__(T.TaskManager); r.host = Host()
import llm as L  # noqa: E402
T_stream = L.stream_chat
L.stream_chat = lambda p, sys_, msgs, cancel=None: iter(["Готово: проверил хаб и драйвер."])
eng = "Done. I checked the USB-C hub and the DisplayLink driver, everything is installed and the monitors should work now."
ok(r._russian(eng) == "Готово: проверил хаб и драйвер.", "итог по-английски — переводится на русский")
ok(r._russian("Готово: всё проверил, мониторы работают.") == "Готово: всё проверил, мониторы работают.", "русский итог не трогаем")
L.stream_chat = T_stream

# своё эхо из колонок не принимаем за вопрос
import collections, time as _t  # noqa: E402
import engine as E  # noqa: E402
e = E.Engine.__new__(E.Engine)
e._said = collections.deque(maxlen=40); e.state = "speaking"; e._spoke_until = 0.0
e._said.append((_t.monotonic(), "Миша, посмотрела. Увы, трек всё ещё на паузе. Нажми жёлтую кнопку в плеере."))
ok(e._is_self_echo("Миша посмотрела. Увы, трек всё ещё на паузе. Жёлтая кнопка."), "своя фраза из колонок — эхо, не вопрос")
ok(not e._is_self_echo("Включи радио джаз пожалуйста сейчас"), "новый вопрос во время ответа — не эхо")
ok(not e._is_self_echo("да давай"), "короткое «да, давай» — не эхо")
e.state, e._spoke_until = "idle", _t.monotonic() - 60
ok(not e._is_self_echo("Миша посмотрела. Увы, трек всё ещё на паузе."), "через минуту после ответа — уже не эхо")

# вызовы функций текстом в разных форматах (Ollama отдаёт «<tool_call>…» в content — без разбора это «пустой ответ»)
import json as _json  # noqa: E402
from llm import _Collector, parse_tool_call  # noqa: E402
col = _Collector()
pieces = ["<tool", "_call>yandex", "_music\n<arg_key>query</arg_key>\n<arg_value>Элла</arg_value>\n</tool_call>"]
txt = "".join(col.feed_line(_json.dumps({"model": "chat", "message": {"role": "assistant", "content": p}, "done": False})) for p in pieces)
txt += col.feed_line(_json.dumps({"model": "chat", "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"}))
txt += col.flush()
ok(txt.strip() == "" and col.tool_calls() and col.tool_calls()[0]["name"] == "yandex_music" and col.tool_calls()[0]["args"] == {"query": "Элла"},
   f"GLM-формат в тексте Ollama — это вызов функции, а не пустой ответ ({col.tool_calls()})")
ok(parse_tool_call('\n<function=run_command>\n<parameter=command>\nlsusb\n</parameter>\n</function>\n') == {"name": "run_command", "args": {"command": "lsusb"}},
   "Qwen3-Coder: <function=…><parameter=…>")
ok(parse_tool_call('{"name": "play_radio", "arguments": {"query": "джаз"}}') == {"name": "play_radio", "args": {"query": "джаз"}}, "Hermes/Qwen: JSON")
ok(parse_tool_call('media_control {"action": "play"}') == {"name": "media_control", "args": {"action": "play"}}, "имя и JSON")
ok(parse_tool_call("ну и что") is None, "обычный текст — не вызов")
col = _Collector()
t = "".join(col.feed_line("data: " + _json.dumps({"choices": [{"delta": {"content": p}}]})) for p in
            ["Let me reconstruct where I am. The plan was created. I need to run diagnostics.\n", "</think>", "Сейчас проверю.",
             "<tool_call>\n{\"name\": \"run_command\", \"arguments\": {\"command\": \"lsusb\"}}\n</tool_call>"]) + col.flush()
ok(t.strip() == "Сейчас проверю." and col.tool_calls()[0]["args"] == {"command": "lsusb"}, f"рассуждение + ответ + вызов: {t!r}")

# речь: числа словами, латиница русскими буквами (Vosk TTS не читает «1», «USB» — фраза пропадает)
from speechnorm import number_words, speech_norm  # noqa: E402
ok(number_words(2026) == "две тысячи двадцать шесть" and number_words(1001) == "тысяча один" and number_words(21) == "двадцать один",
   "числа словами")
sn = speech_norm("Ядро 6.17, порт USB 3, в 16:30 — 45%, минус -5")
ok(not any(ch.isdigit() for ch in sn) and "ю-эс-би" in sn and "шестнадцать тридцать" in sn and "сорок пять процентов" in sn,
   f"фраза для синтеза без цифр и латиницы: {sn}")
ok(not any("a" <= ch.lower() <= "z" for ch in speech_norm("Linux, Ubuntu, sudo modprobe, Wi-Fi, ID 17e9:6006")), "латиница → русские буквы")

# пароль — не в команду и не в журнал
import tools as TT  # noqa: E402
import engine as EE  # noqa: E402
ok(TT.PASS_IN_CMD.search("echo 'Secr3t' | sudo -S modprobe -r x") and not TT.PASS_IN_CMD.search("sudo modprobe -r x"),
   "пароль в команде (echo … | sudo -S) не выполняется")
ok(TT.SUDO_AT.sub(r"\1sudo -A ", "cd /x; sudo ls && grep sudo log") == "cd /x; sudo -A ls && grep sudo log", "sudo → окно пароля системы (sudo -A)")
ok("Kq7zTest" not in EE.redact("пароль администратора в терминале Kq7zTest99") and
   "Secr3t" not in EE.redact('{"command": "echo \'Secr3t\' | sudo -S true"}'), "пароль в журнале скрыт")
ok(EE._spoken_question("Выполнить команду: echo \"=== USB ===\"; lsusb; dkms status 2>/dev/null?") == "Выполнить команду? Подробности — на экране.",
   "длинную команду вслух не диктуем")


def _run(pieces):
    c = _Collector(); out = ""
    for p_ in pieces:
        out += c.feed_obj({"message": {"role": "assistant", "content": p_}, "done": False})
    return out + c.flush(), c.tags.calls()


ok(_run(["Сравнение: 5 <", "<", "<tool"])[0] == "Сравнение: 5 <<<tool", "конец ответа, похожий на начало тега, не теряется")
ok(_run(["<thi", "nk>", "Думаю", "</th", "ink>", "Готово, ", "открыла."])[0] == "Готово, открыла.", "теги <think> разрезаны на куски")
ok(_run(list("<think>Пользователь здоровается.</think>Привет!"))[0] == "Привет!", "<think>…</think> по буквам")
