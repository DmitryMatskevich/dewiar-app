(() => {
  const qs = new URLSearchParams(location.search), taskId = qs.get('task') || '', pathParam = qs.get('path') || '', docKey = qs.get('doc') || '';
  const $ = (selector, root = document) => root.querySelector(selector);
  const status = (text, bad = false) => { $('#status').textContent = text; $('#status').style.color = bad ? 'var(--error)' : ''; };
  const dirtyLabel = (dirty) => { $('#dirty').textContent = dirty ? 'Не сохранено' : 'Сохранено'; $('#dirty').classList.toggle('unsaved', dirty); };
  const notice = (text) => { const el = $('#notice'); el.textContent = text; el.hidden = !text; };
  const safeUrl = value => !/^\s*javascript:/i.test(value || '');
  let sourceHtml = '', basePath = pathParam, revision = '', editor = null, dirty = false, mode = 'visual', codeDirty = false, sourceDirty = false, busy = false, currentTitle = 'HTML';
  const idPrefix = 'dewiar-' + Math.random().toString(36).slice(2, 8) + '-';

  function parseSafe(value) {
    const doc = new DOMParser().parseFromString(String(value || ''), 'text/html');
    doc.querySelectorAll('script,iframe,object,embed,applet,frame,frameset').forEach(node => node.remove());
    doc.querySelectorAll('base').forEach(node => {
      try { const url = new URL(node.getAttribute('href') || '', location.href); if (!/^https?:$/.test(url.protocol) || url.username || url.password) node.remove(); }
      catch (_) { node.remove(); }
    });
    doc.querySelectorAll('meta[http-equiv]').forEach(node => { if (/refresh/i.test(node.getAttribute('http-equiv') || '')) node.remove(); });
    for (const el of doc.querySelectorAll('*')) {
      for (const attr of [...el.attributes]) {
        const key = attr.name.toLowerCase(), val = attr.value;
        if (key.startsWith('on') || key === 'srcdoc' || key === 'formaction' || ((key === 'href' || key === 'src' || key === 'xlink:href') && !safeUrl(val))) el.removeAttribute(attr.name);
      }
    }
    for (const style of doc.querySelectorAll('style')) {
      style.textContent = style.textContent.replace(/@import[^;]+;?/gi, '').replace(/expression\s*\([^)]*\)/gi, '');
    }
    return doc;
  }
  function splitDocument(value) {
    const doc = parseSafe(value);
    return {doc, body: doc.body ? doc.body.innerHTML : String(value || ''), styles: [...doc.querySelectorAll('style')].filter(s => !s.id).map(s => s.textContent).join('\n'), external: [...doc.querySelectorAll('link[rel~="stylesheet"][href]')].map(x => x.href).filter(Boolean)};
  }
  function stripEditorIds(root) { root.querySelectorAll('[data-dewiar-editor-id]').forEach(el => el.removeAttribute('data-dewiar-editor-id')); }
  function serializeVisual() {
    if (!editor) return sourceHtml;
    const shell = parseSafe(sourceHtml);
    const body = shell.body;
    const fragment = new DOMParser().parseFromString('<!doctype html><body>' + editor.getHtml() + '</body>', 'text/html');
    stripEditorIds(fragment);
    body.replaceChildren(...[...fragment.body.childNodes].map(n => document.importNode(n, true)));
    shell.querySelector('#dewiar-editor-generated-style')?.remove();
    if (editor.getCss().trim()) {
      const style = shell.createElement('style'); style.id = 'dewiar-editor-generated-style'; style.textContent = editor.getCss(); shell.head.appendChild(style);
    }
    return '<!doctype html>\n' + shell.documentElement.outerHTML;
  }
  function markDirty(value = true) { dirty = value; dirtyLabel(dirty); }
  function updateIds() {
    if (!editor) return;
    const walk = component => {
      if (component?.is?.('textnode') || !component?.get?.('tagName')) return;
      if (!component || component.is('wrapper')) { component?.components?.().forEach(walk); return; }
      if (!component.get('dewiarEditorId')) component.set('dewiarEditorId', idPrefix + Math.random().toString(36).slice(2, 10), {silent:true});
      component.components?.().forEach(walk);
    };
    editor.getWrapper().components().forEach(walk);
  }
  let idsPending = false;
  function scheduleIds() {
    if (idsPending) return;
    idsPending = true;
    setTimeout(() => { idsPending = false; updateIds(); }, 0);
  }
  function setupEditor(value) {
    const parts = splitDocument(value);
    if (!editor) {
      editor = grapesjs.init({
        container: '#gjs', fromElement: false, height: '100%', width: 'auto', storageManager: false,
        panels: {defaults: []}, showOffsets: true, noticeOnUnload: false,
        canvas: {styles: parts.external},
        blockManager: {appendTo: '#blocks', blocks: [
          {id:'site-header', label:'Шапка сайта', category:'Структура сайта', content:'<header style="display:flex;align-items:center;justify-content:space-between;gap:24px;padding:20px 28px;border-bottom:1px solid #d9e0ea;background:#fff"><strong style="font-size:22px;color:#22324d">LANMASTER</strong><nav style="display:flex;gap:20px;flex-wrap:wrap"><a href="#catalog" style="color:#34445f;text-decoration:none">Каталог</a><a href="#support" style="color:#34445f;text-decoration:none">Поддержка</a><a href="#contacts" style="color:#34445f;text-decoration:none">Контакты</a></nav></header>'},
          {id:'site-nav', label:'Меню навигации', category:'Структура сайта', content:'<nav aria-label="Основное меню" style="display:flex;gap:8px;flex-wrap:wrap;padding:12px 18px;background:#f1f4f8"><a href="#section-1" style="padding:8px 12px;color:#30415c;text-decoration:none">Раздел 1</a><a href="#section-2" style="padding:8px 12px;color:#30415c;text-decoration:none">Раздел 2</a><a href="#section-3" style="padding:8px 12px;color:#30415c;text-decoration:none">Раздел 3</a></nav>'},
          {id:'section', label:'Секция', category:'Структура', content:'<section style="padding:32px"><h2>Заголовок секции</h2><p>Краткое описание</p></section>'},
          {id:'heading', label:'Заголовок', category:'Текст', content:'<h2>Новый заголовок</h2>'},
          {id:'paragraph', label:'Текст', category:'Текст', content:'<p>Добавьте текст</p>'},
          {id:'button', label:'Кнопка', category:'Элементы', content:'<a href="#" style="display:inline-block;padding:12px 18px;background:#4357cf;color:#fff;border-radius:8px;text-decoration:none">Новая кнопка</a>'},
          {id:'image', label:'Изображение', category:'Элементы', content:{type:'image', style:{width:'100%'}}},
          {id:'image-text-left', label:'Картинка слева + текст', category:'Элементы', content:'<section style="display:flow-root;padding:20px"><figure style="float:left;width:34%;margin:0 20px 12px 0;padding:12px;border-radius:12px;background:#edf1f7"><div role="img" aria-label="Изображение" style="height:150px;display:grid;place-items:center;border-radius:8px;background:linear-gradient(135deg,#dce5f1,#f8fafc);color:#65748d">Изображение</div><figcaption style="padding-top:8px;color:#65748d;font-size:13px">Подпись к изображению</figcaption></figure><h2>Текст обтекает изображение</h2><p>Поставьте курсор в этот абзац и замените текст. Изображение можно заменить своим файлом; текст будет идти справа от него и продолжится ниже.</p><p>Дополнительный абзац помогает увидеть, как меняется ширина строк.</p></section>'},
          {id:'image-text-right', label:'Картинка справа + текст', category:'Элементы', content:'<section style="display:flow-root;padding:20px"><figure style="float:right;width:34%;margin:0 0 12px 20px;padding:12px;border-radius:12px;background:#edf1f7"><div role="img" aria-label="Изображение" style="height:150px;display:grid;place-items:center;border-radius:8px;background:linear-gradient(135deg,#dce5f1,#f8fafc);color:#65748d">Изображение</div><figcaption style="padding-top:8px;color:#65748d;font-size:13px">Подпись к изображению</figcaption></figure><h2>Текст обтекает изображение</h2><p>Поставьте курсор в этот абзац и замените текст. Изображение можно заменить своим файлом; текст будет идти слева от него и продолжится ниже.</p><p>Дополнительный абзац помогает увидеть, как меняется ширина строк.</p></section>'},
          {id:'product-card', label:'Карточка товара', category:'Каталог', content:'<article class="product-card" style="display:grid;grid-template-columns:minmax(140px,34%) 1fr;gap:18px;padding:18px;border:1px solid #d7dfeb;border-radius:14px;background:#fff;box-shadow:0 5px 16px rgba(30,48,78,.08)"><div role="img" aria-label="Фотография товара" style="min-height:160px;display:grid;place-items:center;border-radius:10px;background:linear-gradient(135deg,#e6edf6,#f6f8fb);color:#65748d">Фото товара</div><div><small style="color:#71809a">Артикул · LAN-0001</small><h3 style="margin:8px 0;color:#24334d">Название товара</h3><p style="margin:0 0 12px;color:#53627a">Краткое описание, серия, материал и основные характеристики товара.</p><strong style="display:block;margin-bottom:12px;font-size:20px;color:#24334d">Цена по запросу</strong><a href="#" style="display:inline-block;padding:9px 14px;border-radius:8px;background:#4357cf;color:#fff;text-decoration:none">Подробнее</a></div></article>'},
          {id:'product-grid', label:'Сетка товаров · 2 карточки', category:'Каталог', content:'<section style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px"><article class="product-card" style="padding:16px;border:1px solid #d7dfeb;border-radius:12px;background:#fff"><div role="img" style="height:130px;display:grid;place-items:center;background:#edf1f7;color:#65748d">Фото товара</div><h3>Товар 1</h3><p>Краткое описание и характеристики.</p><strong>Цена по запросу</strong></article><article class="product-card" style="padding:16px;border:1px solid #d7dfeb;border-radius:12px;background:#fff"><div role="img" style="height:130px;display:grid;place-items:center;background:#edf1f7;color:#65748d">Фото товара</div><h3>Товар 2</h3><p>Краткое описание и характеристики.</p><strong>Цена по запросу</strong></article></section>'},
          {id:'data-table', label:'Таблица характеристик', category:'Каталог', content:'<table style="width:100%;border-collapse:collapse"><thead><tr><th style="padding:10px;text-align:left;border-bottom:2px solid #cbd5e1">Параметр</th><th style="padding:10px;text-align:left;border-bottom:2px solid #cbd5e1">Значение</th></tr></thead><tbody><tr><td style="padding:10px;border-bottom:1px solid #dbe2ec">Категория</td><td style="padding:10px;border-bottom:1px solid #dbe2ec">6</td></tr><tr><td style="padding:10px;border-bottom:1px solid #dbe2ec">Количество портов</td><td style="padding:10px;border-bottom:1px solid #dbe2ec">24</td></tr></tbody></table>'},
          {id:'columns', label:'Две колонки', category:'Структура', content:'<div style="display:flex;gap:20px"><div style="flex:1"><h3>Колонка 1</h3><p>Текст</p></div><div style="flex:1"><h3>Колонка 2</h3><p>Текст</p></div></div>'}
          ,{id:'site-footer', label:'Подвал сайта', category:'Структура сайта', content:'<footer style="padding:28px;background:#24334d;color:#fff"><div style="display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap"><strong>LANMASTER</strong><nav style="display:flex;gap:16px;flex-wrap:wrap"><a href="#about" style="color:#dce4f2">О компании</a><a href="#support" style="color:#dce4f2">Поддержка</a><a href="#contacts" style="color:#dce4f2">Контакты</a></nav></div><p style="margin:16px 0 0;color:#bac6d8">© 2026. Информация о компании, документах и условиях работы.</p></footer>'}
        ]},
        layerManager: {appendTo:'#layers'},
        styleManager: {appendTo:'#styles', sectors:[
          {name:'Размер и расположение', open:true, properties:[{property:'width',type:'integer',units:['px','%','rem','vw'],default:'auto'},{property:'height',type:'integer',units:['px','%','rem','vh'],default:'auto'},{property:'display'},{property:'flex-direction'},{property:'justify-content'},{property:'align-items'}]},
          {name:'Оформление', open:true, properties:[{property:'color',type:'color'},{property:'background-color',type:'color'},{property:'font-size',type:'integer',units:['px','rem','em']},{property:'font-weight',type:'select',options:[{id:'400',label:'Обычный'},{id:'500',label:'Средний'},{id:'600',label:'Полужирный'},{id:'700',label:'Жирный'}]},{property:'text-align',type:'radio',options:[{id:'left',label:'Слева'},{id:'center',label:'По центру'},{id:'right',label:'Справа'}]},{property:'margin',type:'composite',properties:[{property:'margin-top'},{property:'margin-right'},{property:'margin-bottom'},{property:'margin-left'}]},{property:'padding',type:'composite',properties:[{property:'padding-top'},{property:'padding-right'},{property:'padding-bottom'},{property:'padding-left'}]},{property:'border-radius',type:'integer',units:['px','%','rem']}]}
        ]}
      });
      editor.on('component:selected', component => {
        $('#stage-label').textContent = component ? `Выбран: ${component.getName?.() || component.get('tagName')}` : 'Выберите блок на странице';
        enableCrossWindowDrag(component);
      });
      editor.on('component:update component:styleUpdate', () => { if (mode !== 'loading') markDirty(true); });
      editor.on('component:add', () => { scheduleIds(); if (mode !== 'loading') markDirty(true); });
      editor.on('component:remove', () => { if (mode !== 'loading') markDirty(true); });
      editor.on('undo redo', () => markDirty(true));
      editor.on('load', () => { updateIds(); installCanvasBase(parts.doc); });
      editor.on('run:core:undo run:core:redo', () => markDirty(true));
      editor.setComponents(parts.body);
      editor.setStyle(parts.styles);
      updateIds();
    } else {
      editor.DomComponents.clear(); editor.CssComposer.clear(); editor.setComponents(parts.body); editor.setStyle(parts.styles); updateIds();
    }
    if (parts.external.length) editor.Canvas.getConfig().styles = parts.external;
    installCanvasBase(parts.doc);
    sourceHtml = String(value || '');
  }
  function installCanvasBase(doc) {
    try {
      const head=editor?.Canvas?.getDocument?.()?.head; if(!head)return;
      const base=doc.querySelector('base[href]'); if(!base)return;
      const url=new URL(base.getAttribute('href'),location.href);
      if(!/^https?:$/.test(url.protocol)||url.username||url.password)return;
      let node=head.querySelector('base'); if(!node){node=head.ownerDocument.createElement('base');head.prepend(node);} node.href=url.href;
    } catch(_) {}
  }
  function enableCrossWindowDrag(component) {
    const el=component?.getEl?.(); if(!el||component.is?.('wrapper')||el.__dewiarDragReady)return;
    el.__dewiarDragReady=true; el.setAttribute('draggable','true');
    el.addEventListener('dragstart',event=>{
      const html=safeFragment(component.toHTML()),css=editor.getCss({component})||'';
      event.dataTransfer?.setData('text/html',html);
      event.dataTransfer?.setData('application/x-dewiar-html',JSON.stringify({html,css,baseUrl:splitDocument(sourceHtml).doc.querySelector('base[href]')?.href||''}));
      if(event.dataTransfer)event.dataTransfer.effectAllowed='copy';
    });
  }
  function safeFragment(html) {
    const doc = parseSafe('<!doctype html><body>' + String(html || '') + '</body>');
    doc.querySelectorAll('body *').forEach(el => { if (el.tagName === 'BODY') return; });
    return doc.body.innerHTML;
  }
  function currentHtml() { return mode === 'code' ? $('#source').value : serializeVisual(); }
  function setMode(next) {
    if (next === mode) return;
    if (mode === 'code') { sourceHtml = $('#source').value; sourceDirty = sourceHtml !== loadedOriginal; setupEditor(sourceHtml); }
    else if (mode === 'visual') { sourceHtml = serializeVisual(); $('#source').value = sourceHtml; }
    mode = next;
    for (const b of document.querySelectorAll('.mode')) { const on = b.dataset.mode === mode; b.classList.toggle('active',on); b.setAttribute('aria-pressed',String(on)); }
    $('#gjs').hidden = mode !== 'visual'; $('#source').hidden = mode !== 'code'; $('#preview').hidden = mode !== 'preview';
    if (mode === 'code') $('#source').value = sourceHtml;
    if (mode === 'preview') $('#preview').srcdoc = safeDocument(sourceHtml);
    $('#stage-label').textContent = mode === 'visual' ? 'Выберите блок на странице, чтобы изменить его' : mode === 'code' ? 'Исходный HTML; содержимое страницы не выполняется в окне редактора' : 'Изолированный предпросмотр';
  }
  function safeDocument(value) { return '<!doctype html>\n' + parseSafe(value).documentElement.outerHTML; }
  function setDirtyFromCode() { markDirty($('#source').value !== loadedOriginal); }
  let loadedOriginal = '';
  async function load() {
    if (!taskId || !pathParam) { status('Не задан результат задачи',true); return; }
    try {
      const result = await core.call('task_html_editor_load',{id:taskId,path:pathParam});
      sourceHtml = result.html; revision = result.revision; basePath = result.path; currentTitle = result.title || pathParam.split('/').pop(); loadedOriginal = sourceHtml;
      $('#doc-title').textContent = currentTitle; document.title = `HTML · ${currentTitle} · Dewiar`;
      const api = window.api(); if (api?.html_editor_set_title) api.html_editor_set_title(docKey,currentTitle);
      setupEditor(sourceHtml); status('HTML загружен'); markDirty(false);
      notice('В визуальном режиме скрипты, встроенные фреймы и обработчики событий отключены. Исходный результат задачи остаётся нетронутым.');
      refreshTargets();
    } catch (error) { status(error.message || String(error),true); $('#doc-title').textContent='Не удалось открыть HTML'; }
  }
  async function save() {
    if (busy) return;
    busy=true; $('#save').disabled=true; status('Сохраняю новую версию…');
    try {
      const html = currentHtml();
      const result = await core.call('task_html_editor_save',{id:taskId,path:basePath,revision,html});
      basePath = result.path; revision = result.revision; loadedOriginal = html; sourceHtml = html; markDirty(false); status(`Сохранено в результатах задачи: ${basePath}`);
    } catch (error) { status(error.message || String(error),true); }
    finally { busy=false; $('#save').disabled=false; }
  }
  async function saveAs() {
    const api=window.api(); if (!api?.save_file) { status('«Сохранить как» доступно в приложении Dewiar',true); return; }
    try { const result=await api.save_file(currentHtml(),currentTitle.replace(/\.(html?|htm)$/i,'')+'.html','HTML (*.html;*.htm)'); if(result)status('Копия сохранена: '+result); }
    catch(error){status(error.message||String(error),true);}
  }
  function inventory() {
    updateIds(); const rows=[];
    const walk=component=>{
      if(component?.is?.('textnode')||!component?.get?.('tagName')) return;
      const id=component.get?.('dewiarEditorId');
      if(id) rows.push({id,tag:String(component.get('tagName')||'div').slice(0,24),text:String(component.get('content')||component.getInnerHTML?.()||'').replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim().slice(0,240),style:component.getStyle?.()||{}});
      component.components?.().forEach(walk);
    };
    editor.getWrapper().components().forEach(walk); return rows.slice(0,250);
  }
  let proposed = null;
  function describe(op) {
    const element=inventory().find(x=>x.id===op.target);
    if(op.op==='delete') return `Удалить <${element?.tag||'элемент'}> «${element?.text||'без текста'}»`;
    if(op.op==='set_text') return `Изменить текст <${element?.tag||'элемент'}> на «${op.text}»`;
    if(op.op==='set_style'||op.op==='resize') return `Изменить ${op.property||'стиль'} у <${element?.tag||'элемент'}> на ${op.value}`;
    if(op.op==='move') return `Переместить элемент «${element?.text||element?.tag||''}»`;
    return `Добавить <${op.tag}>${op.text ? ` «${op.text}»` : ''}`;
  }
  async function askAI() {
    const request=$('#ai-request').value.trim(); if(!request)return status('Опишите нужную правку',true);
    $('#ai-ask').disabled=true; $('#ai-result').textContent='Ассистент изучает структуру страницы…'; $('#ai-ops').hidden=true;
    try {
      proposed=await core.call('html_editor_suggest',{request,nodes:inventory()});
      $('#ai-result').textContent=proposed.reply||'Проверьте предложенные действия.';
      const area=$('#ai-ops'); area.replaceChildren();
      (proposed.operations||[]).forEach(op=>{const row=document.createElement('div');row.className='ai-op';row.textContent=describe(op);area.append(row);});
      if(proposed.operations?.length){const buttons=document.createElement('div');buttons.className='ai-actions';const apply=document.createElement('button');apply.className='primary';apply.type='button';apply.textContent='Применить';const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Отмена';buttons.append(apply,cancel);area.append(buttons);area.hidden=false;apply.onclick=()=>applyAI();cancel.onclick=()=>{proposed=null;area.hidden=true;$('#ai-result').textContent='Предложение отменено.';};}
    } catch(error) { $('#ai-result').textContent=error.message||String(error); }
    finally { $('#ai-ask').disabled=false; }
  }
  function findComponent(id) { let found=null; const walk=c=>{if(c.get?.('dewiarEditorId')===id)found=c;else c.components?.().forEach(walk);};editor.getWrapper().components().forEach(walk);return found; }
  function applyAI() {
    if(!proposed) return;
    const now=new Set(inventory().map(n=>n.id));
    if(proposed.operations.some(op=>op.target&&!now.has(op.target)||op.before&&!now.has(op.before)||op.after&&!now.has(op.after))){$('#ai-result').textContent='Страница изменилась после подготовки предложения. Повторите команду.';return;}
    try {
      for(const op of proposed.operations){
        const target=op.target?findComponent(op.target):null;
        if(op.op==='delete') target.remove();
        else if(op.op==='set_text') target.components().reset([{type:'textnode',content:op.text}]);
        else if(op.op==='set_style'||op.op==='resize'){const key=(op.property||'width').replace(/[A-Z]/g,m=>'-'+m.toLowerCase());target.addStyle({[key]:op.value});}
        else if(op.op==='move'){const before=findComponent(op.before);if(before)target.move(before.parent(),{at:before.index()});}
        else if(op.op==='insert'){const parent=op.after?findComponent(op.after)?.parent():editor.getWrapper();const after=op.after?findComponent(op.after):null;const html=`<${op.tag}>${escapeText(op.text||'')}</${op.tag}>`;parent.components().add(html,{at:after?after.index()+1:parent.components().length});}
      }
      updateIds();markDirty(true);$('#ai-result').textContent='Предложенные изменения применены. Их можно отменить кнопкой «Отменить».';$('#ai-ops').hidden=true;proposed=null;
    } catch(error) { $('#ai-result').textContent='Не удалось применить все изменения: '+(error.message||error); }
  }
  function escapeText(v){return String(v).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
  async function refreshTargets() {
    const api=window.api(); if(!api?.list_html_editors)return;
    const list=await api.list_html_editors(); const select=$('#target-window'); if(!select)return;
    select.replaceChildren(new Option('Другое окно…',''));
    list.filter(row=>row.key!==docKey).forEach(row=>select.add(new Option(row.title,row.key)));
  }
  window.htmlEditorRefreshTargets = refreshTargets;
  function selectedFragment() {
    const component=editor?.getSelected(); if(!component)return null;
    return {html:safeFragment(component.toHTML()),css:(editor.getCss({component})||'').replace(/@import[^;]+;?/gi,'').replace(/url\s*\([^)]*\)/gi,''),baseUrl:splitDocument(sourceHtml).doc.querySelector('base[href]')?.href||''};
  }
  async function sendToOther() {
    const target=$('#target-window')?.value, payload=selectedFragment(), api=window.api();
    if(!target)return status('Сначала выберите целевое окно'); if(!payload)return status('Выберите блок на странице');
    if(api?.html_editor_transfer){await api.html_editor_transfer(target,payload);status('Блок скопирован в выбранное окно');}
  }
  window.htmlEditorReceiveTransfer = payload => {
    if(!payload||typeof payload.html!=='string'||payload.html.length>180000)return status('Получен недопустимый фрагмент',true);
    let html=safeFragment(payload.html); if(!html)return status('В передаваемом элементе нет поддерживаемой разметки',true);
    if(typeof payload.baseUrl==='string'){try{const base=new URL(payload.baseUrl);if(/^https?:$/.test(base.protocol)&&!base.username&&!base.password){const d=new DOMParser().parseFromString(html,'text/html');d.querySelectorAll('[src],[href]').forEach(el=>{for(const a of ['src','href'])if(el.hasAttribute(a)){try{const u=new URL(el.getAttribute(a),base);if(/^https?:$/.test(u.protocol))el.setAttribute(a,u.href);}catch(_){}}});html=d.body.innerHTML;}}catch(_){}}
    const selected=editor.getSelected(), parent=selected?.parent?.()||editor.getWrapper(), at=selected?selected.index()+1:parent.components().length;
    parent.components().add(html,{at});
    if(typeof payload.css==='string'&&payload.css.length<60000&&!/url\s*\(|@import|expression|javascript:|behavior\s*:|-moz-binding/i.test(payload.css)) editor.setStyle(editor.getCss()+'\n'+payload.css);
    updateIds();markDirty(true);status('Блок вставлен в это окно. Исходное окно не изменялось.');
  };
  window.htmlEditorRequestClose = () => !dirty || window.confirm('Есть несохранённые изменения. Закрыть HTML-редактор без сохранения?');

  document.querySelectorAll('.mode').forEach(button=>button.onclick=()=>setMode(button.dataset.mode));
  document.querySelectorAll('.side-tab').forEach(button=>button.onclick=()=>{document.querySelectorAll('.side-tab').forEach(x=>x.classList.toggle('active',x===button));document.querySelectorAll('.side-panel').forEach(x=>x.classList.toggle('active',x.id===button.dataset.side));});
  $('#save').onclick=save; $('#save-as').onclick=saveAs; $('#ai-ask').onclick=askAI;
  $('#ai-request').addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key==='Enter'){event.preventDefault();askAI();}});
  $('#source').addEventListener('input',()=>{codeDirty=true;setDirtyFromCode();});
  $('#undo').onclick=()=>editor?.UndoManager.undo(); $('#redo').onclick=()=>editor?.UndoManager.redo();
  $('#drop-target').addEventListener('dragover',event=>{event.preventDefault();event.dataTransfer.dropEffect='copy';$('#drop-target').classList.add('drag-over');});
  $('#drop-target').addEventListener('dragleave',()=>$('#drop-target').classList.remove('drag-over'));
  $('#drop-target').addEventListener('drop',event=>{event.preventDefault();$('#drop-target').classList.remove('drag-over');let payload=null;try{payload=JSON.parse(event.dataTransfer.getData('application/x-dewiar-html')||'');}catch(_){}if(!payload){const html=event.dataTransfer.getData('text/html');if(html)payload={html,css:''};}if(payload)window.htmlEditorReceiveTransfer(payload);else status('Перетащите выделенный элемент из другого окна редактора');});
  if(window.api()?.list_html_editors){const row=document.createElement('div');row.className='transfer-control';row.innerHTML='<label for="target-window">Скопировать выбранный блок в</label><select id="target-window"><option value="">Другое окно…</option></select><button type="button" id="send-block">Копировать</button>';$('.stage-toolbar').append(row);$('#send-block').onclick=sendToOther;}
  core.on('hello',load); if(core.snapshot)load();
})();
