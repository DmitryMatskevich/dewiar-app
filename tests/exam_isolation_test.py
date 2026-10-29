"""Экзамен — песочница (16.10: в «Обо мне» у владельца — «Бюджет Миши на мебель… менялось 13 раз», «Дни рождения:
Аня, Петя, мама» — фразы экзамена). Ядро экзамена запускалось с provision.json, лежащим рядом с программой
(у владельца — личный: его имя, ключ облака), применяло его само («apply_on_upgrade») — и память экзамена уходила
синхронизацией в его настоящий аккаунт. Проверяем: ядро экзамена не берёт настройки из пакета и не синхронизирует."""
import asyncio, json, os, sys, tempfile
from pathlib import Path
ROOT = Path(os.environ.get("DEWIAR_ROOT") or Path(__file__).resolve().parent.parent)
sys.path.insert(0, str(ROOT / "core"))
fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


tmp = Path(tempfile.mkdtemp())
prov = tmp / "provision.json"                 # как личный provision.json владельца рядом с программой
prov.write_text(json.dumps({"dewiar": 1, "name": "Личный", "revision": 7, "apply_on_upgrade": True, "user_name": "Миша",
                            "persona": "patrick", "sync": {"enabled": True}}, ensure_ascii=False), encoding="utf-8")
os.environ["DEWIAR_PROVISION"] = str(prov)
import exam  # noqa: E402

core = exam.Core({"kind": "openai", "base_url": "http://127.0.0.1:9/v1", "model": "m", "name": "экзамен"}, tmp / "x", log=lambda *a: None)


async def main():
    core.start()
    await core.connect()
    await core.stop()

asyncio.run(main())
st = json.loads((tmp / "x/cfg/settings.json").read_text(encoding="utf-8"))
ok(st.get("user_name", "") == "" and (st.get("provision") or {}).get("packaged_rev") in (None, 0) and st.get("persona") == "lira",
   f"ядро экзамена не применяет provision.json из пакета (имя, персонаж, ключи): имя={st.get('user_name')!r}, "
   f"provision={st.get('provision')}, персонаж={st.get('persona')}")
ok((st.get("sync") or {}).get("enabled") is False, "синхронизация в ядре экзамена выключена")
print("\nИТОГ:", "всё прошло" if not fails else f"не прошло {len(fails)}")
sys.exit(1 if fails else 0)
