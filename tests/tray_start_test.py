"""Запуск и трей (тестировщик на Windows, 14.10: «Лиру свернули в трей, перезагрузили компьютер без автозапуска,
запустили сами — открылась свёрнутой»). Убранный в трей виджет остаётся в трее только при автозапуске (флаг
--autostart в Run реестра Windows и /etc/xdg/autostart, перезапуск после обновления) или в первые 3 минуты после
включения (старые установки без флага); запустил сам — виджет на экране."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ui"))
import app as A  # noqa: E402

fails = []


def ok(c, m):
    print(("✅ " if c else "❌ ") + m)
    if not c:
        fails.append(m)


tray, plain = {"hud_tray": True}, {}
ok(A.start_in_tray(tray, NS(settings=False, autostart=False), uptime=3600) is False, "убран в трей, запустил сам через час после включения — виджет на экране")
ok(A.start_in_tray(tray, NS(settings=False, autostart=True), uptime=3600) is True, "автозапуск (--autostart) — остаётся в трее")
ok(A.start_in_tray(tray, NS(settings=False, autostart=False), uptime=40) is True, "старая установка без флага: запуск в первые минуты после включения — в трее")
ok(A.start_in_tray(plain, NS(settings=False, autostart=True), uptime=40) is False, "в трей не убирали — показываем всегда")
ok(A.start_in_tray(tray, NS(settings=True, autostart=True), uptime=40) is False, "открыли студию — не прячем")
ok(A.start_in_tray(tray, NS(settings=False, autostart=False), uptime=None) is False, "время работы системы не узнали — показываем")
up = A.uptime_s()
ok(up is None or up > 0, f"время работы системы читается: {up}")
src = (ROOT / "ui/app.py").read_text(encoding="utf-8")
ok(src.count("start_in_tray(uis, args)") == 2 and 'uis.get("hud_tray") and not args.settings' not in src, "Linux и Windows — одно правило")
ok(src.count('"--autostart"') >= 3, "перезапуск после обновления — как автозапуск (убранный в трей остаётся в трее)")
ok('"${APPPY}" --autostart' in (ROOT / "packaging/windows/dewiar.nsi").read_text(encoding="utf-8"), "Windows: автозапуск с флагом")
ok("Exec=/usr/bin/dewiar --autostart" in (ROOT / "packaging/desktop/dewiar-autostart.desktop").read_text(encoding="utf-8"), "Linux: автозапуск с флагом")
print("\nИТОГ:", "всё прошло" if not fails else f"провалов {len(fails)}")
sys.exit(1 if fails else 0)
