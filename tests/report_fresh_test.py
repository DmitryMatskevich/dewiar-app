"""Компьютер: отчёт разработчику — только о том, что случилось после прошлого отправленного отчёта.
Было: в каждом отчёте — журнал за несколько дней с давно исправленными ошибками (например, «'list' object has no
attribute 'strip'» трёхдневной давности), и новое терялось среди старого. Теперь после отправки ставится отметка
(report_mark.json: где кончался core.log), и следующий отчёт берёт журнал и ошибки с неё — даже если журнал с тех пор
повернули (core.log → core.log.1)."""
import os, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
tmp = Path(tempfile.mkdtemp())
os.environ.update(DEWIAR_STATE_DIR=str(tmp / "st"), DEWIAR_CONFIG_DIR=str(tmp / "cfg"))
sys.path.insert(0, str(ROOT / "core"))
from engine_answer import AnswerMixin as A  # noqa: E402
from settings import STATE_DIR  # noqa: E402
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


logs = STATE_DIR / "logs"; logs.mkdir(parents=True, exist_ok=True)
log = logs / "core.log"
log.write_text("21:14:37 ERROR   dewiar.tasks: старая беда 'list' object has no attribute 'strip'\n12:00:00 INFO    старое\n", encoding="utf-8")
ok(any("старая беда" in x for x in A._log_tail()), "без отметки — весь журнал")
A._report_mark_set(7)
with open(log, "a", encoding="utf-8") as f:
    f.write("15:33:44 ERROR   dewiar.tasks: новая беда\n15:34:00 INFO    новое\n")
tail = A._log_tail(A._report_mark())
ok(tail == ["15:33:44 ERROR   dewiar.tasks: новая беда", "15:34:00 INFO    новое"], f"после отчёта — только новое: {tail}")
# журнал повернули: core.log → core.log.1, начался новый
log.rename(logs / "core.log.1")
log.write_text("16:00:00 WARNING совсем новое\n", encoding="utf-8")
tail = A._log_tail(A._report_mark())
ok(tail == ["15:33:44 ERROR   dewiar.tasks: новая беда", "15:34:00 INFO    новое", "16:00:00 WARNING совсем новое"],
   f"журнал повернули — новое из обоих файлов, старое — нет: {tail}")
ok(A._report_mark().get("id") == 7, "отметка помнит номер прошлого отчёта")
print("ALL OK" if not fails else f"ошибок: {len(fails)}")
sys.exit(1 if fails else 0)
