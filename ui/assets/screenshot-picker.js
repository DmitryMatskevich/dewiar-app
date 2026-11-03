/* Shared screenshot menu, original-image viewer and pixel-accurate region editor. */
(() => {
  const make = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
  const button = (text, fn) => { const b = make('button', text); b.type = 'button'; b.onclick = fn; return b; };
  function modal(title) {
    const d = make('dialog', null, 'ss-dialog'), heading = make('h2', title), close = button('×', () => d.close());
    close.className = 'ss-close'; close.setAttribute('aria-label', 'Отменить'); close.title = 'Отменить';
    const bar = make('div', null, 'ss-dialog-bar'); bar.append(heading, close); d.append(bar); document.body.append(d);
    d.addEventListener('close', () => d.remove(), {once: true}); d.showModal(); return d;
  }
  async function openImage(value) {
    const native = window.api?.();
    if (native?.open_screenshot_window) return native.open_screenshot_window('view', value.id);
    const d = modal('Снимок экрана'); d.classList.add('ss-large');
    await viewer(d, value.id);
  }
  function loadImage(url, alt) {
    return new Promise((resolve, reject) => {
      if (typeof url !== 'string' || !url.startsWith('data:image/')) { reject(Error('Предпросмотр недоступен')); return; }
      const img = make('img'); img.alt = alt;
      img.onload = () => resolve(img); img.onerror = () => reject(Error('Не удалось загрузить изображение'));
      img.src = url;
    });
  }
  async function imageViewer(host, load, {refreshable = false, alt = 'Исходный снимок экрана'} = {}) {
    const status = make('p', 'Загружаю изображение…', 'ss-info'), stage = make('div', null, 'ss-view-stage');
    const actions = make('div', null, 'ss-view-actions');
    let img = null;
    const zoom = button('Показать 1:1', () => {
      if (!img) return;
      const actual = img.classList.toggle('ss-actual'); zoom.textContent = actual ? 'Вместить в окно' : 'Показать 1:1';
      zoom.setAttribute('aria-pressed', String(actual));
    });
    zoom.setAttribute('aria-pressed', 'false'); zoom.disabled = true;
    const refresh = button('Обновить', update); actions.append(zoom); if (refreshable) actions.append(refresh);
    status.setAttribute('role', 'status'); host.append(actions, status, stage);
    async function update() {
      refresh.disabled = true; zoom.disabled = true; status.textContent = 'Загружаю изображение…';
      try {
        const next = await loadImage(await load(), alt);
        if (!host.isConnected) return;
        next.className = 'ss-full-image'; img = next; stage.replaceChildren(img);
        zoom.textContent = 'Показать 1:1'; zoom.setAttribute('aria-pressed', 'false'); status.textContent = '';
      } catch (e) {
        // Never present an old frame as the current content of a closed/hidden window.
        stage.replaceChildren(); img = null; status.textContent = e.message || String(e);
      } finally { refresh.disabled = false; zoom.disabled = !img; }
    }
    await update();
  }
  async function viewer(host, id) {
    await imageViewer(host, () => core.call('screenshot_image', {id}));
  }
  async function windowViewer(host, windowId) {
    await imageViewer(host, () => core.call('screenshot_window_preview', {window_id:windowId, full_size:true}),
      {refreshable:true, alt:'Предпросмотр окна приложения'});
  }
  async function openWindowImage(w, anchor) {
    const native = window.api?.();
    if (native?.open_screenshot_window) return native.open_screenshot_window('window', String(w.window_id));
    const d = modal(w.title ? `${w.name} — ${w.title}` : w.name); d.classList.add('ss-large');
    d.addEventListener('close', () => anchor?.focus(), {once:true});
    await windowViewer(d, w.window_id);
  }
  async function menu(anchor, done, failed) {
    const d = modal('Снимок экрана'); d.classList.add('ss-source-menu');
    const status = make('p', '', 'ss-info'); status.setAttribute('role', 'status');
    const list = make('div', null, 'ss-source-list'), sourceActions = make('div', null, 'ss-source-actions');
    const filter = make('input', null, 'ss-window-filter'); filter.type = 'search'; filter.placeholder = 'Фильтр по названию приложения';
    filter.setAttribute('aria-label', 'Фильтр по названию приложения');
    const capture = async args => {
      d.close(); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      try { done?.(await core.call('screenshot_attach', args)); } catch (e) { failed?.(e); }
    };
    const full = button('Весь', () => capture({source: 'screen'})); full.dataset.ssSource = 'screen';
    const part = button('Часть', async () => {
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
    const heading = make('h3', 'Окна приложений'), refresh = button('Обновить список', () => load());
    sourceActions.append(full, part); d.append(sourceActions, heading, filter, refresh, status, list);
    let windows = [], selected = null, observer = null, activePreviews = 0;
    const previewQueue = [];
    function drainPreviews() {
      while (activePreviews < 2 && previewQueue.length) {
        const {w, resolve, reject} = previewQueue.shift();
        if (!d.open || !windows.includes(w)) { resolve(null); continue; }
        activePreviews++;
        core.call('screenshot_window_preview', {window_id:w.window_id}).then(url => {
          w.preview = url; resolve(url);
        }, reject).finally(() => { activePreviews--; drainPreviews(); });
      }
    }
    function previewUrl(w) {
      if (w.preview) return Promise.resolve(w.preview);
      if (!w.previewPromise) w.previewPromise = new Promise((resolve, reject) => {
        previewQueue.push({w, resolve, reject}); drainPreviews();
      }).finally(() => { w.previewPromise = null; });
      return w.previewPromise;
    }
    async function showThumbnail(w, tile) {
      try {
        const url = await previewUrl(w); if (!url || !tile.isConnected || !d.open) return;
        const image = await loadImage(url, `Окно ${w.name}`); image.className = 'ss-window-preview';
        if (tile.isConnected && d.open) tile.replaceChildren(image);
      } catch (error) {
        w.preview = null;
        if (tile.isConnected) {
          tile.replaceChildren(make('span', 'Нет снимка', 'ss-preview-placeholder'));
          tile.title = (error.message || String(error)) + '. Нажмите для повторного предпросмотра.';
        }
      }
    }
    filter.addEventListener('input', render);
    function render() {
      observer?.disconnect();
      const q = filter.value.trim().toLocaleLowerCase('ru'); list.replaceChildren();
      observer = typeof IntersectionObserver === 'function' ? new IntersectionObserver(entries => {
        for (const entry of entries) if (entry.isIntersecting) { observer.unobserve(entry.target); entry.target.loadPreview(); }
      }, {root:d, rootMargin:'120px'}) : null;
      const visible = windows.filter(w => !q || `${w.name} ${w.title || ''}`.toLocaleLowerCase('ru').includes(q));
      for (const w of visible) {
        const row = make('article', null, 'ss-window-row'), header = make('div', null, 'ss-window-header');
        row.dataset.selected = String(selected === w.window_id);
        const preview = button('', async () => {
          selected = w.window_id; render();
          const anchor = list.querySelector(`[data-window-id="${w.window_id}"] .ss-window-preview-button`);
          try { await openWindowImage(w, anchor); } catch (error) { status.textContent = error.message || String(error); }
        });
        preview.className = 'ss-window-preview-button'; preview.title = 'Открыть большой предпросмотр';
        preview.setAttribute('aria-label', `Открыть большой предпросмотр: ${w.name}${w.title ? ' — ' + w.title : ''}`);
        preview.append(make('span', 'Загрузка…', 'ss-preview-placeholder'));
        preview.loadPreview = () => showThumbnail(w, preview);
        const name = make('span', w.name, 'ss-window-name'), detail = make('span', w.title || 'Окно приложения', 'ss-window-title');
        const select = button('', () => { selected = w.window_id; render(); });
        select.className = 'ss-window-select'; select.setAttribute('aria-pressed', String(selected === w.window_id));
        const label = make('span', null, 'ss-window-label'); label.append(name, detail); select.append(label);
        header.append(preview, select);
        row.append(header);
        if (selected === w.window_id) {
          const actions = make('div', null, 'ss-window-actions');
          const take = button('Сделать снимок', () => capture({source:'application', window_id:w.window_id}));
          take.className = 'ss-window-capture';
          actions.append(take);
          row.append(actions);
        }
        row.dataset.windowId = w.window_id; list.append(row);
        if (observer) observer.observe(preview); else preview.loadPreview();
      }
      if (!visible.length && windows.length) status.textContent = 'По этому фильтру окна не найдены.';
      else if (!windows.length && !status.textContent) status.textContent = 'Других окон нет. Откройте приложение и обновите список.';
    }
    async function load() {
      refresh.disabled = true; status.textContent = 'Загружаю окна…'; list.replaceChildren();
      try {
        const result = await core.call('screenshot_sources'); if (!d.open) return;
        windows = result.windows || []; status.textContent = result.message || ''; render();
      } catch (e) { status.textContent = e.message || String(e); }
      finally { refresh.disabled = false; }
    }
    d.addEventListener('close', () => { observer?.disconnect(); drainPreviews(); windows = []; anchor?.focus(); }, {once: true}); full.focus(); load();
  }
  async function selectRegion(done, failed) {
    try {
      const preview = await core.call('screenshot_prepare');
      const editor = modal('Выберите часть экрана'); editor.classList.add('ss-large');
      const sx = Number(preview.scale_x) || 1, sy = Number(preview.scale_y) || 1;
      await regionEditor(editor, preview.id, () => editor.close(), null, region => done?.({
        x:Math.round(region.x*sx), y:Math.round(region.y*sy),
        width:Math.max(2,Math.round(region.width*sx)), height:Math.max(2,Math.round(region.height*sy))
      }));
    } catch (e) { failed?.(e); }
  }
  async function regionEditor(host, id, close, done, onSelect) {
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
          if (onSelect) {
            const selected = {...rect};
            await core.call('screenshot_draft_cancel', {id});
            finished = true; onSelect(selected); close(); return;
          }
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
  window.DewiarCapture = {menu, openImage, viewer, windowViewer, regionEditor, selectRegion};
})();
