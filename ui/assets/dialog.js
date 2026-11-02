/* Shared modal shell for confirmations, messages and Help. Content stays local/text-safe. */
(() => {
  const base = document.currentScript?.src;
  if (!document.getElementById('dw-dialog-style')) {
    const css = document.createElement('link'); css.id = 'dw-dialog-style'; css.rel = 'stylesheet';
    css.href = base ? new URL('dialog.css', base).href : 'assets/dialog.css'; document.head.append(css);
  }
  let serial = 0;
  const make = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
  const button = (label, cls) => { const b = make('button', label, 'dw-button' + (cls ? ' ' + cls : '')); b.type = 'button'; b.title = label; return b; };
  async function clipboard(text, host) {
    if (navigator.clipboard?.writeText) {
      try { await navigator.clipboard.writeText(text); return; } catch (_) {}
    }
    const input = make('textarea'); input.value = text; input.className = 'dw-clipboard'; host.append(input); input.select();
    try { if (!document.execCommand('copy')) throw Error('copy'); } finally { input.remove(); }
  }
  function mount(opts = {}) {
    const root = opts.root || make('dialog'); root.classList.add('dw-dialog');
    if (opts.backClass) root.classList.add(opts.backClass);
    root.id ||= 'dw-dialog-' + (++serial); root.hidden = true; root.setAttribute('aria-modal', 'true');
    const panel = make('div', null, 'dw-panel' + (opts.panelClass ? ' ' + opts.panelClass : ''));
    const header = make('header', null, 'dw-header'), title = make('h2', opts.title || 'Сообщение Dewiar', 'dw-title');
    title.id = opts.headingId || root.id + '-title'; root.setAttribute('aria-labelledby', title.id);
    const x = button('', 'dw-close'); x.id = opts.closeId || root.id + '-close'; x.setAttribute('aria-label', 'Закрыть окно'); x.title = 'Закрыть окно (Esc)';
    x.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';
    const body = make('div', null, 'dw-body'); if (opts.content) body.append(opts.content);
    const footer = make('footer', null, 'dw-footer'), status = make('p', '', 'dw-status dwm-copy-status'); status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
    const actions = make('div', null, 'dw-actions'); footer.append(status, actions); header.append(title, x); panel.append(header, body, footer); root.replaceChildren(panel); document.body.append(root);
    let previous = null, finishing = false, resolve;
    const promise = new Promise(r => { resolve = r; });
    const view = {root, panel, body, footer, actions, status, title, closeButton: x, promise,
      setTitle(text) { title.textContent = text; },
      show() {
        if (root.open) return view;
        finishing = false; previous = document.activeElement; root.hidden = false; root.classList.add('open', 'on'); root.showModal();
        (opts.initialFocus?.() || x).focus(); return view;
      },
      close(value = opts.cancelValue) {
        if (finishing || !root.open) return; finishing = true; root.close();
        root.hidden = true; root.classList.remove('open', 'on'); if (!opts.persistent) root.remove();
        if (previous?.isConnected) previous.focus(); opts.onClose?.(value); resolve(value);
      },
      addAction(label, handler, cls) { const b = button(label, cls); b.onclick = handler; actions.append(b); return b; }
    };
    x.onclick = () => view.close();
    root.addEventListener('cancel', e => { e.preventDefault(); e.stopPropagation(); view.close(); });
    root.addEventListener('keydown', e => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); view.close(); }
      if (e.key === 'Tab') {
        const controls = [...panel.querySelectorAll('button,input,select,textarea,a[href],summary,[tabindex="0"]')].filter(n => !n.disabled && n.getClientRects().length);
        const first = controls[0], last = controls.at(-1);
        if (e.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) { e.preventDefault(); last?.focus(); }
        else if (!e.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) { e.preventDefault(); first?.focus(); }
      }
    });
    root.addEventListener('mousedown', e => { if (e.target === root && opts.backdropClose !== false) view.close(); });
    if (opts.copyText) view.addAction('Скопировать текст', async () => {
      const b = actions.firstElementChild; b.disabled = true;
      try { await clipboard(String(opts.copyText()), body); status.textContent = 'Текст скопирован.'; }
      catch (_) { status.textContent = 'Не удалось скопировать. Выделите текст вручную.'; }
      finally { b.disabled = false; if (root.open) b.focus(); }
    }, 'dw-copy');
    return view;
  }
  const VERB = /^(Удалить|Убрать|Очистить|Выключить|Отключить|Сбросить|Отклонить|Выйти|Восстановить|Принять|Сделать|Вернуть|Перейти)/;
  const DANGER = /^(Удалить|Убрать|Очистить|Выключить|Отключить|Сбросить|Отклонить)/;
  function ask(text, opts = {}, input = null) {
    opts ||= {};
    if (window.DEWIAR_AUTO_ASK) return Promise.resolve(input ? (window.DEWIAR_AUTO_ASK === true ? input.value : window.DEWIAR_AUTO_ASK) : true);
    const content = make('div'), question = make('p', text); question.id = 'dw-question-' + (++serial); content.append(question);
    let field = null, yes;
    if (input) { field = make('input'); field.type = 'text'; field.value = input.value || ''; field.spellcheck = false; field.placeholder = opts.ph || ''; field.setAttribute('aria-labelledby', question.id); content.append(field); }
    const view = mount({title: opts.title || (input ? 'Введите значение' : 'Подтверждение'), content, backClass: 'dwq-back', panelClass: 'dwq', cancelValue: input ? null : false, initialFocus: () => field || yes});
    const verb = opts.ok || (String(text).match(VERB) || [])[1] || (input ? 'Готово' : 'Да');
    if (!opts.only) view.addAction(opts.cancel || 'Отмена', () => view.close(input ? null : false));
    yes = view.addAction(verb, () => view.close(input ? field.value : true), 'dw-primary yes' + ((opts.danger ?? DANGER.test(verb)) ? ' danger' : ''));
    view.root.addEventListener('keydown', e => { if (e.key === 'Enter' && ((field && document.activeElement === field) || document.activeElement === yes)) { e.preventDefault(); e.stopPropagation(); view.close(input ? field.value : true); } });
    view.show(); if (field) field.select(); return view.promise;
  }
  function messagePopup(text, opts = {}) {
    if (window.DEWIAR_AUTO_ASK) return Promise.resolve(true);
    opts ||= {}; const title = String(opts.title || 'Сообщение Dewiar'), content = make('div'), p = make('p', String(text ?? ''), 'dwm-text'); content.append(p);
    const steps = Array.isArray(opts.steps) ? opts.steps.map(String).filter(Boolean) : [];
    if (steps.length) { const ol = make('ol', null, 'dwm-steps'); steps.forEach(step => ol.append(make('li', step))); content.append(ol); }
    if (opts.note) content.append(make('p', String(opts.note), 'dw-note dwm-note'));
    const view = mount({title, content, backClass: 'dwm-back', panelClass: 'dwm', cancelValue: true, copyText: () => [title, String(text ?? ''), ...steps.map((s, i) => `${i+1}. ${s}`), opts.note || ''].filter(Boolean).join('\n\n')});
    view.actions.classList.add('dwm-actions'); view.addAction(opts.close || 'Понятно', () => view.close(true), 'dw-primary primary'); view.show(); return view.promise;
  }
  window.DewiarDialogs = {mount};
  window.ask = (text, opts) => ask(text, opts);
  window.askText = (text, value, opts) => ask(text, opts, {value});
  window.messagePopup = messagePopup; window.tell = messagePopup;
})();
