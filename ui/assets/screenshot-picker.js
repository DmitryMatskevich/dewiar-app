/* Shared screenshot menu, original-image viewer and pixel-accurate region editor. */
(() => {
  const make = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
  const button = (text, fn) => { const b = make('button', text); b.type = 'button'; b.onclick = fn; return b; };
  function modal(title) {
    const d = make('dialog', null, 'ss-dialog'), heading = make('h2', title), close = button('Закрыть', () => d.close());
    const bar = make('div', null, 'ss-dialog-bar'); bar.append(heading, close); d.append(bar); document.body.append(d);
    d.addEventListener('close', () => d.remove(), {once: true}); d.showModal(); return d;
  }
  async function openImage(value) {
    const native = window.api?.();
    if (native?.open_screenshot_window) return native.open_screenshot_window('view', value.id);
    const d = modal('Снимок экрана'); d.classList.add('ss-large');
    await viewer(d, value.id);
  }
  async function viewer(host, id) {
    const status = make('p', 'Загружаю исходный снимок…', 'ss-info'), stage = make('div', null, 'ss-view-stage');
    status.setAttribute('role', 'status'); host.append(status, stage);
    try {
      const url = await core.call('screenshot_image', {id});
      if (!host.isConnected) return;
      const img = make('img', null, 'ss-full-image'); img.alt = 'Исходный снимок экрана'; img.src = url;
      const zoom = button('Показать 1:1', () => { const actual = img.classList.toggle('ss-actual'); zoom.textContent = actual ? 'Вместить в окно' : 'Показать 1:1'; zoom.setAttribute('aria-pressed', String(actual)); });
      zoom.setAttribute('aria-pressed', 'false'); host.insertBefore(zoom, stage); stage.append(img); status.textContent = '';
      img.onerror = () => { status.textContent = 'Не удалось открыть изображение'; };
    } catch (e) { status.textContent = e.message || String(e); }
  }
  async function menu(anchor, done, failed) {
    const d = modal('Снимок экрана'); d.classList.add('ss-source-menu');
    const status = make('p', '', 'ss-info'); status.setAttribute('role', 'status');
    const list = make('div', null, 'ss-source-list');
    const capture = async args => {
      d.close(); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      try { done?.(await core.call('screenshot_attach', args)); } catch (e) { failed?.(e); }
    };
    const full = button('Весь экран', () => capture({source: 'screen'})); full.dataset.ssSource = 'screen';
    const part = button('Часть экрана…', async () => {
      d.close();
      try {
        const preview = await core.call('screenshot_prepare');
        const native = window.api?.();
        if (native?.open_screenshot_window) {
          try { await native.open_screenshot_window('region', preview.id); }
          catch (e) { await core.call('screenshot_draft_cancel', {id: preview.id}); throw e; }
        } else {
          const editor = modal('Выберите часть экрана'); editor.classList.add('ss-large');
          await regionEditor(editor, preview.id, () => editor.close(), done);
        }
      } catch (e) { failed?.(e); }
    }); part.dataset.ssSource = 'region';
    const heading = make('h3', 'Видимые окна приложений'), refresh = button('Обновить список', () => load());
    d.append(full, part, heading, refresh, status, list);
    async function load() {
      refresh.disabled = true; status.textContent = 'Загружаю окна…'; list.replaceChildren();
      try {
        const result = await core.call('screenshot_sources'); if (!d.open) return;
        (result.windows || []).forEach(w => {
          const b = button(w.name + (w.title ? ' — ' + w.title : ''), () => capture({source: 'application', window_id: w.window_id}));
          b.dataset.windowId = w.window_id; list.append(b);
        });
        status.textContent = result.message || (list.children.length ? 'Выберите окно для снимка.' : 'Других видимых окон нет. Откройте приложение и обновите список.');
      } catch (e) { status.textContent = e.message || String(e); }
      finally { refresh.disabled = false; }
    }
    d.addEventListener('close', () => anchor?.focus(), {once: true}); full.focus(); load();
  }
  async function regionEditor(host, id, close, done) {
    const status = make('p', 'Загружаю предварительное изображение…', 'ss-info'); status.setAttribute('role', 'status'); host.append(status);
    let finished = false, cleaned = false, rect = null, image = null, observer = null;
    const cleanup = () => { if (cleaned) return; cleaned = true; observer?.disconnect(); window.removeEventListener('pagehide', cleanup); host.removeEventListener('close', cleanup); if (!finished) core.call('screenshot_draft_cancel', {id}).catch(() => {}); };
    host.addEventListener('close', cleanup, {once: true}); window.addEventListener('pagehide', cleanup, {once: true});
    try {
      const preview = await core.call('screenshot_draft', {id});
      if (!host.isConnected) { cleanup(); return; }
      image = new Image(); image.src = preview.url;
      await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = () => reject(Error('Не удалось загрузить снимок')); });
      if (!host.isConnected) { cleanup(); return; }
      const width = image.naturalWidth, height = image.naturalHeight;
      rect = {x: Math.round(width*.1), y: Math.round(height*.1), width: Math.round(width*.8), height: Math.round(height*.8)};
      status.textContent = 'Выделите область мышью. Тяните её середину для перемещения, углы — для изменения размера. Стрелки перемещают, Shift ускоряет. Сохранится только выбранная область предварительного снимка.';
      const stage = make('div', null, 'ss-region-stage'), canvas = make('canvas'); canvas.width = width; canvas.height = height; canvas.tabIndex = 0;
      canvas.setAttribute('aria-label', 'Область снимка: выделите мышью или уточните координаты в полях ниже');
      const fields = make('div', null, 'ss-region-fields'), inputs = {};
      for (const [key, label] of [['x','Слева'], ['y','Сверху'], ['width','Ширина'], ['height','Высота']]) {
        const wrap = make('label', label), input = make('input'); input.type = 'number'; input.step = 1; input.min = key === 'x' || key === 'y' ? 0 : 2;
        input.onchange = () => { rect[key] = Number(input.value); constrain(); draw(); }; inputs[key] = input; wrap.append(input); fields.append(wrap);
      }
      const confirm = button('Сделать снимок', async () => {
        if (confirm.disabled) return; confirm.disabled = true; cancel.disabled = true;
        try {
          const result = await core.call('screenshot_attach', {draft_id: id, region: {...rect}});
          finished = true; done?.(result); close();
        } catch (e) { status.textContent = e.message || String(e); confirm.disabled = false; cancel.disabled = false; }
      }); confirm.className = 'ss-primary'; confirm.dataset.ssConfirm = 'region';
      const cancel = button('Отмена', () => { cleanup(); close(); });
      const actions = make('div', null, 'ss-region-actions'); actions.append(cancel, confirm); stage.append(canvas); host.append(stage, fields, actions);
      function constrain() {
        rect.x = Math.max(0, Math.min(width-2, Math.round(rect.x) || 0)); rect.y = Math.max(0, Math.min(height-2, Math.round(rect.y) || 0));
        rect.width = Math.max(2, Math.min(width-rect.x, Math.round(rect.width) || 2)); rect.height = Math.max(2, Math.min(height-rect.y, Math.round(rect.height) || 2));
      }
      function draw() {
        const ctx = canvas.getContext('2d'), {x,y,width:w,height:h} = rect;
        ctx.drawImage(image, 0, 0); ctx.fillStyle = 'rgba(0,0,0,.5)'; ctx.fillRect(0,0,width,height);
        ctx.drawImage(image,x,y,w,h,x,y,w,h); const scale = width / (canvas.getBoundingClientRect().width || width);
        ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 2*scale; ctx.strokeRect(x,y,w,h); ctx.fillStyle = '#ffffff';
        for (const [cx,cy] of [[x,y],[x+w,y],[x,y+h],[x+w,y+h]]) ctx.fillRect(cx-5*scale,cy-5*scale,10*scale,10*scale);
        for (const key of Object.keys(inputs)) inputs[key].value = rect[key];
      }
      const point = e => { const box = canvas.getBoundingClientRect(); return {x:Math.max(0,Math.min(width,(e.clientX-box.x)*width/box.width)),y:Math.max(0,Math.min(height,(e.clientY-box.y)*height/box.height))}; };
      let drag = null;
      canvas.onpointerdown = e => {
        if (e.button !== 0 || confirm.disabled) return;
        e.preventDefault(); canvas.focus(); canvas.setPointerCapture(e.pointerId); const p = point(e), r = rect, tolerance = 12*width/canvas.getBoundingClientRect().width;
        const corners = [[r.x,r.y,'nw'],[r.x+r.width,r.y,'ne'],[r.x,r.y+r.height,'sw'],[r.x+r.width,r.y+r.height,'se']];
        const corner = corners.find(([x,y]) => Math.abs(p.x-x)<tolerance && Math.abs(p.y-y)<tolerance);
        drag = {start:p, rect:{...r}, mode:corner?.[2] || (p.x>r.x && p.x<r.x+r.width && p.y>r.y && p.y<r.y+r.height ? 'move' : 'new')};
      };
      canvas.onpointermove = e => {
        if (!drag) return; const p = point(e), s = drag.start, old = drag.rect;
        if (drag.mode === 'move') { rect.x = Math.max(0,Math.min(width-old.width,old.x+p.x-s.x)); rect.y = Math.max(0,Math.min(height-old.height,old.y+p.y-s.y)); }
        else {
          const anchor = drag.mode === 'new' ? s : {x:drag.mode.includes('w') ? old.x+old.width : old.x, y:drag.mode.includes('n') ? old.y+old.height : old.y};
          rect = {x:Math.min(anchor.x,p.x),y:Math.min(anchor.y,p.y),width:Math.abs(anchor.x-p.x),height:Math.abs(anchor.y-p.y)};
        }
        constrain(); draw();
      };
      canvas.onpointerup = canvas.onpointercancel = () => { drag = null; };
      canvas.onkeydown = e => {
        if (!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key) || confirm.disabled) return;
        e.preventDefault(); const step = e.shiftKey ? 10 : 1;
        if (e.key==='ArrowLeft') rect.x-=step; if (e.key==='ArrowRight') rect.x+=step; if (e.key==='ArrowUp') rect.y-=step; if (e.key==='ArrowDown') rect.y+=step;
        rect.x = Math.min(rect.x,width-rect.width); rect.y = Math.min(rect.y,height-rect.height); constrain(); draw();
      };
      observer = new ResizeObserver(draw); observer.observe(stage); draw(); canvas.focus();
    } catch (e) { status.textContent = e.message || String(e); }
    return cleanup;
  }
  window.DewiarCapture = {menu, openImage, viewer, regionEditor};
})();
