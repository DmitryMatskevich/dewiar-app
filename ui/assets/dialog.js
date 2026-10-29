/* Свои окна вопроса вместо confirm()/prompt() браузера (21.10, Ubuntu: «окно выглядит пугающе» — системное окно WebKit
   с заголовком «JavaScript — file:///…?token=…»: и некрасиво, и показывает адрес страницы с ключом связи).
   ask(text, opts) -> Promise<bool>; askText(label, value, opts) -> Promise<string|null>. Кнопка подтверждения
   называется действием («Удалить», «Выключить»…), опасное — красным; Enter — да, Esc и щелчок мимо — нет.
   Тема — от страницы: переменные --ink, --muted, --glass… (студия, блокнот); своя запасная палитра — если их нет. */
(function () {
  const VERB = /^(Удалить|Убрать|Очистить|Выключить|Отключить|Сбросить|Отклонить|Выйти|Восстановить|Принять|Сделать|Вернуть|Перейти)/;
  const DANGER = /^(Удалить|Убрать|Очистить|Выключить|Отключить|Сбросить|Отклонить)/;
  let css = false;
  function style() {
    if (css) return; css = true;
    const s = document.createElement("style");
    s.textContent = `
      .dwq-back { position: fixed; inset: 0; z-index: 9999; display: grid; place-items: center; padding: 20px;
        background: rgba(10,12,20,.32); -webkit-backdrop-filter: blur(3px); backdrop-filter: blur(3px); opacity: 0; transition: opacity .15s; }
      .dwq-back.on { opacity: 1; }
      .dwq { width: min(420px, 100%); border-radius: 20px; padding: 20px 20px 16px; color: var(--ink, #ECEEF4);
        background: var(--dwq-bg, #1b1c27); box-shadow: 0 24px 60px -20px rgba(0,0,0,.55), 0 0 0 1px rgba(255,255,255,.06);
        font: 14.5px/1.5 "Manrope", "Ubuntu", "Segoe UI", sans-serif; transform: translateY(6px) scale(.985); transition: transform .18s cubic-bezier(.2,.8,.2,1); }
      .dwq-back.on .dwq { transform: none; }
      .dwq p { margin: 0 0 16px; white-space: pre-line; }
      .dwq input { width: 100%; margin: 0 0 16px; padding: 10px 12px; border-radius: 12px; border: 0; outline: none; font: inherit;
        color: inherit; background: rgba(127,127,127,.14); }
      .dwq input:focus { box-shadow: 0 0 0 2px var(--accent, #8B8CF7); }
      .dwq .row { display: flex; gap: 8px; justify-content: flex-end; }
      .dwq button { font: 600 14px/1 inherit; font-family: inherit; padding: 10px 16px; border-radius: 12px; border: 0; cursor: pointer;
        color: inherit; background: rgba(127,127,127,.14); }
      .dwq button:hover { background: rgba(127,127,127,.22); }
      .dwq button.yes { color: #fff; background: var(--accent, #6a7cf0); }
      .dwq button.yes.danger { background: #E5484D; }
      .dwq button:focus-visible { outline: 2px solid var(--accent, #8B8CF7); outline-offset: 2px; }
      body.light .dwq, .st-light .dwq, html.st-light .dwq { --dwq-bg: #f4f7fc; color: var(--ink, #24324a); box-shadow: 0 24px 60px -20px rgba(20,32,60,.35), 0 0 0 1px rgba(20,32,60,.06); }
      @media (prefers-reduced-motion: reduce) { .dwq-back, .dwq { transition: none; } }`;
    document.head.appendChild(s);
  }
  function open(text, opts, input) {
    if (window.DEWIAR_AUTO_ASK) return Promise.resolve(input ? (window.DEWIAR_AUTO_ASK === true ? input.value : window.DEWIAR_AUTO_ASK) : true);  // проверки (tests/)
    style();
    return new Promise((resolve) => {
      const back = document.createElement("div"); back.className = "dwq-back";
      const box = document.createElement("div"); box.className = "dwq"; box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true");
      const p = document.createElement("p"); p.textContent = text; box.appendChild(p);
      let inp = null;
      if (input) { inp = document.createElement("input"); inp.value = input.value || ""; inp.spellcheck = false; if (opts && opts.ph) inp.placeholder = opts.ph; box.appendChild(inp); }
      const verb = (opts && opts.ok) || (String(text).match(VERB) || [])[1] || (input ? "Готово" : "Да");
      const row = document.createElement("div"); row.className = "row";
      const no = document.createElement("button"); no.textContent = (opts && opts.cancel) || "Отмена";
      const yes = document.createElement("button"); yes.textContent = verb; yes.className = "yes" + ((opts && opts.danger) ?? DANGER.test(verb) ? " danger" : "");
      if (opts && opts.only) no.style.display = "none";
      row.append(no, yes); box.appendChild(row); back.appendChild(box); document.body.appendChild(back);
      const prev = document.activeElement;
      const done = (v) => { back.classList.remove("on"); document.removeEventListener("keydown", key, true); setTimeout(() => back.remove(), 160);
        try { prev && prev.focus && prev.focus(); } catch (e) { } resolve(v); };
      const key = (e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(input ? null : false); }
        else if (e.key === "Enter" && (!inp || document.activeElement === inp || document.activeElement === yes)) { e.preventDefault(); e.stopPropagation(); done(input ? inp.value : true); } };
      document.addEventListener("keydown", key, true);
      no.onclick = () => done(input ? null : false);
      yes.onclick = () => done(input ? inp.value : true);
      back.onmousedown = (e) => { if (e.target === back) done(input ? null : false); };
      requestAnimationFrame(() => { back.classList.add("on"); (inp || yes).focus(); if (inp) inp.select(); });
    });
  }
  function messageStyle() {
    if (document.getElementById("dw-message-style")) return;
    const s = document.createElement("style"); s.id = "dw-message-style";
    s.textContent = `
      .dwm-back { position: fixed; inset: 0; z-index: 10000; display: grid; place-items: center; padding: 20px;
        background: rgba(10,12,20,.34); -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px); opacity: 0; transition: opacity .16s; }
      .dwm-back.on { opacity: 1; }
      .dwm { width: min(680px, 100%); max-height: min(78vh, 720px); overflow: auto; border-radius: 22px; padding: 24px;
        color: var(--ink, #24324a); background: var(--panel, #f4f7fc); box-shadow: 0 24px 70px -24px rgba(0,0,0,.52), 0 0 0 1px rgba(30,50,90,.12);
        font: 15px/1.55 "Manrope", "Ubuntu", "Segoe UI", sans-serif; transform: translateY(8px) scale(.985); transition: transform .18s cubic-bezier(.2,.8,.2,1); }
      .dwm-back.on .dwm { transform: none; }
      .dwm h2 { margin: 0 0 12px; font-size: 22px; line-height: 1.2; }
      .dwm .dwm-text { margin: 0 0 14px; white-space: pre-line; overflow-wrap: anywhere; }
      .dwm-steps { margin: 0 0 18px; padding-left: 24px; }
      .dwm-steps li { padding: 3px 0; }
      .dwm-note { margin: 0 0 14px; color: var(--muted, #61718c); font-size: 13px; }
      .dwm-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
      .dwm-actions button { min-height: 44px; padding: 10px 15px; border: 0; border-radius: 12px; font: 600 14px/1.2 inherit; color: inherit; background: rgba(110,130,170,.15); cursor: pointer; }
      .dwm-actions button.primary { color: #fff; background: var(--accent, #6a7cf0); }
      .dwm-actions button:focus-visible { outline: 2px solid var(--accent, #6a7cf0); outline-offset: 2px; }
      .dwm-copy-status { min-height: 20px; margin: 10px 0 0; color: var(--good, #087f5b); font-size: 13px; }
      html:not(.st-light) .dwm { color: var(--ink, #eef2ff); background: var(--panel, #1b1f2c); box-shadow: 0 24px 70px -24px rgba(0,0,0,.72), 0 0 0 1px rgba(255,255,255,.09); }
      @media (prefers-reduced-motion: reduce) { .dwm-back, .dwm { transition: none; } }
      @media (max-width: 560px) { .dwm { padding: 20px; border-radius: 18px; } .dwm-actions { justify-content: stretch; } .dwm-actions button { flex: 1 1 160px; } }`;
    document.head.appendChild(s);
  }
  function clipboard(text) {
    const fallback = () => new Promise((resolve, reject) => {
      const area = document.createElement("textarea"); area.value = text; area.style.cssText = "position:fixed;left:-9999px;top:0";
      document.body.appendChild(area); area.select();
      try { resolve(document.execCommand("copy")); } catch (e) { reject(e); } finally { area.remove(); }
    });
    return navigator.clipboard?.writeText ? navigator.clipboard.writeText(text).then(() => true).catch(fallback) : fallback();
  }
  function messagePopup(text, opts = {}) {
    if (window.DEWIAR_AUTO_ASK) return Promise.resolve(true); // deterministic UI tests do not need an overlay
    opts = opts || {};
    messageStyle();
    const title = String(opts.title || "Сообщение Dewiar");
    const steps = Array.isArray(opts.steps) ? opts.steps.map(String).filter(Boolean) : [];
    const body = String(text == null ? "" : text);
    const full = [title, body, steps.length ? "Шаги:\n" + steps.map((x, i) => `${i + 1}. ${x}`).join("\n") : ""].filter(Boolean).join("\n\n");
    return new Promise(resolve => {
      const back = document.createElement("div"); back.className = "dwm-back";
      const box = document.createElement("section"); box.className = "dwm"; box.setAttribute("role", "dialog"); box.setAttribute("aria-modal", "true"); box.setAttribute("aria-labelledby", "dwm-title");
      const heading = document.createElement("h2"); heading.id = "dwm-title"; heading.textContent = title;
      const p = document.createElement("p"); p.className = "dwm-text"; p.textContent = body;
      box.append(heading, p);
      if (steps.length) { const list = document.createElement("ol"); list.className = "dwm-steps"; steps.forEach(step => { const li = document.createElement("li"); li.textContent = step; list.append(li); }); box.append(list); }
      if (opts.note) { const note = document.createElement("p"); note.className = "dwm-note"; note.textContent = String(opts.note); box.append(note); }
      const status = document.createElement("p"); status.className = "dwm-copy-status"; status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
      const actions = document.createElement("div"); actions.className = "dwm-actions";
      const copy = document.createElement("button"); copy.type = "button"; copy.textContent = "Скопировать текст"; copy.title = "Скопировать заголовок, сообщение и все шаги";
      const close = document.createElement("button"); close.type = "button"; close.className = "primary"; close.textContent = opts.close || "Понятно";
      actions.append(copy, close); box.append(status, actions); back.append(box); document.body.append(back);
      const previous = document.activeElement;
      let finished = false;
      const done = () => { if (finished) return; finished = true; back.classList.remove("on"); document.removeEventListener("keydown", key, true); setTimeout(() => back.remove(), 160); try { previous?.focus?.(); } catch (e) {} resolve(true); };
      const key = e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(); } };
      document.addEventListener("keydown", key, true); close.onclick = done; back.onmousedown = e => { if (e.target === back) done(); };
      copy.onclick = async () => { copy.disabled = true; try { await clipboard(full); status.textContent = "Текст сообщения скопирован."; } catch (e) { status.textContent = "Не удалось скопировать. Выделите текст вручную."; } finally { copy.disabled = false; } };
      requestAnimationFrame(() => { back.classList.add("on"); close.focus(); });
    });
  }
  window.ask = (text, opts) => open(text, opts, null);
  window.askText = (label, value, opts) => open(label, opts, { value });
  window.messagePopup = messagePopup;
  window.tell = (text, opts) => messagePopup(text, opts);
})();
