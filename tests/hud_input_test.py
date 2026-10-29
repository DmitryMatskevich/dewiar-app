"""Контракт строки ввода виджета: клавиатура, диктовка и выбор микрофона."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
src = (ROOT / "ui" / "hud.html").read_text(encoding="utf-8")
help_src = (ROOT / "ui" / "assets" / "help.js").read_text(encoding="utf-8")
fails = []


def ok(value, message):
    print(("✅ " if value else "❌ ") + message)
    if not value:
        fails.append(message)


ok('title="Enter — новая строка · Ctrl+Enter — отправить"' in src, "Enter оставляет перенос, отправка описана через Ctrl+Enter")
ok('e.key === "Enter" && (e.ctrlKey || e.metaKey)' in src, "Ctrl/⌘+Enter отправляет сообщение")
ok('#ask { display: block;' in src, "окно чата агента открыто постоянно")
ok('M12 19V5M6 11l6-6 6 6' in src, "стрелка отправки направлена вверх")
ok(src.index('id="b-voice-inline"') < src.index('id="a-mic"'), "динамик находится слева от микрофона")
ok(src.index('id="dict-level"') < src.index('id="a-mic"'), "индикатор уровня находится слева от микрофона")
for ident in ("b-mic", "b-type", "b-team"):
    ok(f'id="{ident}" hidden' in src, f"старая кнопка {ident} убрана из панели опций")
ok('e.key === "Enter" && !e.shiftKey' not in src, "старое поведение Shift+Enter не осталось")
for ident in ("a-mic", "a-mic-pick", "dict-level", "b-voice-inline"):
    ok(f'id="{ident}"' in src, f"элемент {ident} присутствует")
for ident in ("dict-tools", "dict-cancel", "dict-done"):
    ok(f'id="{ident}"' not in src, f"лишний элемент {ident} не показывается")
for command in ("dictation_start", 'core.call("devices")'):
    ok(command in src, f"подключена команда {command}")
ok('$("ask-model").onclick' in src and 'open_settings("brain")' in src, "кнопка модели открывает вкладку «Модели»")
ok('id="b-model"' not in src and 'id="bubble"' in src and '$("bubble").onclick' in src,
   "верхняя отдельная иконка мозга убрана, состояние остаётся отдельным индикатором")
ok('say.textContent = "Текст"' in src and 'Текстовый режим' not in src,
   "индикатор текстового режима называется «Текст»")
ok('$("bubble").onclick = () => { $("bubble").title' in src and '$("bubble").onclick = () => { const a = api(); if (a) a.open_settings("brain")' not in src,
   "индикатор «Текст» не выбирает модель")
ok('role="dialog" aria-modal="true" aria-label="Выбор помощника"' in src and 'id="team-list"' in src and 'id="team-close"' in src,
   "выбор помощника открывается модальным плавающим окном")
ok('$("team-close").onclick' in src and 'e.key === "Escape" && $("team").classList.contains("show")' in src,
   "окно помощников закрывается крестиком и клавишей Esc")
ok('const panel = $("team"), box = $("team-list")' in src and 'panel?.classList.remove("show")' in src,
   "выбор помощника закрывает модальное окно после выбора")
ok('id="a-chat"' not in src and 'id="b-chat"' in src and '$("b-chat").onclick' in src,
   "кнопка чата находится в нижней панели, а не в строке ввода")
ok(src.index('id="b-notes"') < src.index('id="b-chat"') < src.index('id="b-set"') and 'id="b-chat" hidden' not in src,
   "кнопка чата видна сразу справа от «Заметок»")
ok('id="a-stop"' in src and 'core.call("stop")' in src and '#ask.running #a-stop' in src,
   "кнопка «Стоп» появляется во время ответа и отменяет текущую работу")
ok('Я ${name}, жду ваше задание' in src and 'Я ${name}, говорите, я слушаю' in src,
   "placeholder объясняет ожидание задания и продолжение голосового диалога")
ok('addEventListener("resize", () => { orbScale(); requestAnimationFrame(fitAsk); })' in src and 'const available = Math.max(120' in src,
   "высота поля пересчитывается при изменении ширины и ограничена доступным окном")
ok('new ResizeObserver(entries =>' in src and '.observe($("askbox"))' in src,
   "изменение ширины карточки через уголок тоже пересчитывает высоту поля")
ok('t.style.height = "24px"' in src and 't.scrollHeight' in src and 't.classList.toggle("scroll"' in src,
   "поле растёт по переносам, затем прокручивается внутри")
ok('#ask { margin-top: auto; }' in src and '#mid::after { content: ""; display: none; }' in src,
   "строка ввода закреплена над нижней панелью без пустого места под ней")
ok('id="b-orb"' in src and 'setOrbVisible(!orbShown, true)' in src, "верхняя кнопка скрывает и возвращает большой образ")
ok('chat-open' in src and '#card.chat-open #cap' in src, "ответ и чат растягиваются при расширении окна")
ok('box.classList.toggle("show")' in src and 'function tapOrb()' in src, "круг помощника открывает выбор персонажа")
ok("Ctrl+Enter (или ⌘+Enter)" in help_src and "выбрать устройство macOS" in help_src
   and "автоматически добавляется" in help_src and "слева от микрофона" in help_src,
   "справка объясняет новую клавиатуру, автоматическую диктовку и уровень записи")

if fails:
    raise SystemExit(f"Ошибок: {len(fails)}")
print("OK: строка ввода агента")
