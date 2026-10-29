/* Масштаб интерфейса и размер текста — как «Внешний вид» в Obsidian (просьба 14.10: «на ноутбуке хочется уменьшить и
   шрифты, и окна»). Настройки ui.zoom (весь интерфейс: 70–150 %) и ui.text (текст ответов, чата и заметок: 85–140 %).
   apply(ui) — масштаб страницы (CSS zoom) и переменная --dw-text для текста; keys(get, set) — Ctrl + / Ctrl − / Ctrl 0
   и Ctrl + колесо; окна виджета и студии под масштаб подгоняет ui/app.py (Api.set_zoom). Один файл для студии и виджета. */
(function () {
  const STEPS = [0.7, 0.75, 0.8, 0.85, 0.9, 1, 1.1, 1.2, 1.3, 1.4, 1.5];
  const LIM = { zoom: [0.7, 1.5], text: [0.85, 1.4] };
  const num = (v, k) => { v = +v; const [a, b] = LIM[k]; return isFinite(v) && v > 0 ? Math.round(Math.min(b, Math.max(a, v)) * 100) / 100 : 1; };
  let cur = { zoom: 1, text: 1 };

  function apply(ui) {
    ui = ui || {};
    const z = num(ui.zoom, "zoom"), t = num(ui.text, "text"), r = document.documentElement;
    r.style.zoom = z === 1 ? "" : String(z);
    r.style.setProperty("--dw-text", String(t));
    r.dataset.zoom = String(z);
    cur = { zoom: z, text: t };
    return cur;
  }
  /** Следующий шаг масштаба: dir 1 — крупнее, -1 — мельче, 0 — 100 %. */
  function step(z, dir) {
    if (!dir) return 1;
    z = num(z, "zoom");
    if (dir > 0) return STEPS.find(x => x > z + 0.001) || STEPS[STEPS.length - 1];
    return [...STEPS].reverse().find(x => x < z - 0.001) || STEPS[0];
  }
  /** Ctrl + / Ctrl − / Ctrl 0 (по месту клавиши — работает и в русской раскладке) и Ctrl + колесо. set(z) — сохранить. */
  function keys(get, set) {
    const go = (dir) => { const z = step(get ? get() : cur.zoom, dir); apply(Object.assign({}, { text: cur.text }, { zoom: z })); set(z); };
    document.addEventListener("keydown", e => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const c = e.code;
      const dir = (c === "Equal" || c === "NumpadAdd" || e.key === "+") ? 1 : (c === "Minus" || c === "NumpadSubtract") ? -1
        : (c === "Digit0" || c === "Numpad0") ? 0 : null;
      if (dir === null) return;
      e.preventDefault(); e.stopPropagation(); go(dir);
    }, true);
    let wt = 0;
    document.addEventListener("wheel", e => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const now = Date.now(); if (now - wt < 120) return; wt = now;        // тачпад шлёт десятки событий — шаг за жест
      go(e.deltaY < 0 ? 1 : -1);
    }, { passive: false, capture: true });
  }
  const pct = (v) => Math.round(v * 100) + " %";
  window.DewiarZoom = { apply, step, keys, pct, STEPS, LIM, get: () => cur };
})();
