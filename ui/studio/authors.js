(function () {
  'use strict';
  const articleRoot = document.getElementById('content-app'); if (!articleRoot) return;
  const root = document.createElement('details'); root.id = 'content-authors';
  root.innerHTML = `<summary>Библиотека авторов</summary><p>Храните имя, биографию и подписи для разных сайтов. Выбор в редакторе сохраняет подпись и биографию выбранной версии профиля. Изменение профиля не меняет готовые статьи и видео.</p>
    <p id="au-message" role="status" aria-live="polite"></p><div class="au-layout"><div>
    <label>Поиск автора<input id="au-query" type="search" maxlength="300"></label><label class="au-check"><input id="au-archived" type="checkbox"> Архив авторов</label>
    <button class="btn" id="au-new" type="button">Новый автор</button><button class="btn" id="au-refresh" type="button">Обновить список авторов</button>
    <p id="au-count"></p><div id="au-list"></div><div class="au-actions"><button class="btn" id="au-prev">Назад</button><button class="btn" id="au-next">Далее</button></div></div>
    <form id="au-form"><label>Имя автора<input id="au-name" maxlength="240" required></label><label>Биография автора<textarea id="au-bio" maxlength="4000" rows="4"></textarea></label>
    <fieldset><legend>Подписи сайтов</legend><p>Пустое поле использует основное имя.</p><div id="au-sites"></div></fieldset><p id="au-meta">Новый профиль</p>
    <div class="au-actions"><button class="btn primary" id="au-save" type="submit">Сохранить автора</button><button class="btn" id="au-reload" type="button" hidden>Загрузить автора заново</button><button class="btn" id="au-archive" type="button" hidden>В архив авторов</button></div></form></div>`;
  articleRoot.after(root); const $ = s => document.getElementById('au-' + s);
  let current = null, dirty = false, busy = false, request = null, offset = 0, total = 0, sequence = 0;
  const siteFields = new Map(), pickers = [];
  function status(text, error = false) { $('message').textContent = text; $('message').classList.toggle('au-error', error); }
  function locks() { root.querySelectorAll('input,textarea,button').forEach(e => e.disabled = busy); if (!busy) { $('prev').disabled = offset === 0; $('next').disabled = offset + 50 >= total; if(current?.archived) $('form').querySelectorAll('input,textarea,#au-save').forEach(e => e.disabled = true); } }
  async function act(fn) { if(busy)return; busy = true; locks(); try { await fn(); } catch(e) { status(e.message, true); } finally { busy = false; locks(); } }
  async function discard() { return !dirty || await window.contentConfirm('В карточке автора есть несохранённые изменения. Перейти без сохранения?'); }
  function paint(row) { current = row; dirty = false; request = null; $('name').value = row?.name || ''; $('bio').value = row?.bio || ''; for(const [site,input] of siteFields) input.value = row?.site_names[site] || ''; $('meta').textContent = row ? `Профиль v${row.revision}${row.archived?' · Архив':''}` : 'Новый профиль'; for(const id of ['reload','archive']) $(id).hidden = !row; $('archive').textContent = row?.archived ? 'Восстановить автора' : 'В архив авторов'; }
  async function list() {
    const seq = ++sequence, result = await core.call('content_author_list',{query:$('query').value,archived:$('archived').checked,offset,limit:50}); if(seq !== sequence)return;
    total = result.total; $('count').textContent = 'Авторов: ' + total; $('list').replaceChildren();
    for(const row of result.items) { const button = document.createElement('button'); button.type = 'button'; button.className = 'au-row'; button.textContent = row.name + ' · v' + row.revision; button.onclick = () => act(async () => { if(await discard()) { paint(await core.call('content_author_get',{id:row.id})); status(''); } }); $('list').append(button); }
    if(!result.items.length) $('list').textContent = 'Авторы не найдены.';
    locks();
  }
  async function refreshPickers() { for(const picker of pickers) await picker.refresh(); }
  $('form').onsubmit = e => { e.preventDefault(); act(async () => {
    const data = {name:$('name').value,bio:$('bio').value,site_names:Object.fromEntries([...siteFields].filter(([,el]) => el.value.trim()).map(([id,el]) => [id,el.value]))};
    const signature = JSON.stringify(data); if(request?.signature !== signature) request = {signature,key:crypto.randomUUID()};
    const row = await core.call(current?'content_author_update':'content_author_create',current?{id:current.id,expected_revision:current.revision,patch:data}:{data,request_key:request.key});
    paint(row); await list(); await refreshPickers(); status('Автор сохранён. Подписи в существующих материалах не изменились.');
  }); };
  $('form').oninput = () => { dirty = true; $('meta').textContent = 'Есть несохранённые изменения автора'; };
  $('new').onclick = () => act(async () => { if(await discard()) { paint(null); status(''); $('name').focus(); } });
  $('reload').onclick = () => act(async () => { if(await discard()) { paint(await core.call('content_author_get',{id:current.id})); status('Карточка автора загружена.'); } });
  $('archive').onclick = () => act(async () => { if(await discard()) { const row = await core.call('content_author_archive',{id:current.id,expected_revision:current.revision,archived:!current.archived}); paint(row); await list(); await refreshPickers(); status(row.archived?'Автор в архиве. Сохранённые подписи остались в материалах.':'Автор восстановлен.'); } });
  $('refresh').onclick = () => act(async () => { await list(); await refreshPickers(); });
  for(const id of ['query','archived']) $(id).onchange = () => act(async () => { offset = 0; await list(); });
  $('prev').onclick = () => act(async () => { offset = Math.max(0,offset-50); await list(); }); $('next').onclick = () => act(async () => { offset += 50; await list(); });
  function picker(prefix, read, apply) {
    const author = document.getElementById(prefix+'-author'); if(!author)return;
    const box = document.createElement('details'); box.className = 'au-picker'; box.dataset.authorPicker = 'true';
    box.innerHTML = `<summary>Выбрать автора из библиотеки</summary><label>Поиск в библиотеке авторов<input id="${prefix}-author-query" maxlength="300" type="search"></label><button class="btn" type="button" id="${prefix}-author-find">Найти авторов</button><label>Профиль автора<select id="${prefix}-author-profile"></select></label><button class="btn" type="button" id="${prefix}-author-apply">Подставить автора и биографию</button><p aria-live="polite"></p>`;
    author.closest('label').after(box); box.addEventListener('input',e => e.stopPropagation());
    const query = box.querySelector('input'), select = box.querySelector('select'), find = box.querySelector('#'+prefix+'-author-find'), button = box.querySelector('#'+prefix+'-author-apply'), info = box.querySelector('p');
    let items = [], seq = 0, pending = false;
    async function refresh() { const n = ++seq, chosen = select.value; const result = await core.call('content_author_list',{query:query.value,limit:100}); if(n !== seq)return; items = result.items; select.replaceChildren(new Option(items.length?'Выберите профиль':'Авторов пока нет','')); for(const row of items)select.append(new Option(row.name+' · v'+row.revision,row.id)); select.value = chosen; if(!select.value) select.value=''; info.textContent = result.total > 100 ? 'Показаны первые 100. Уточните поиск.' : 'Подпись выбирается для сайта, указанного в редакторе. Затем сохраните материал.'; }
    async function run(fn) { if(pending)return; pending = true; button.disabled = find.disabled = true; try { await fn(); } catch(e) { info.textContent = e.message; } finally { pending = false; button.disabled = find.disabled = author.disabled; } }
    find.onclick = () => run(refresh); box.addEventListener('toggle',() => { if(box.open)run(refresh); });
    button.onclick = () => run(async () => { const item = items.find(r => r.id === select.value); if(!item)throw Error('Выберите автора.'); const target = read(); const result = await core.call('content_author_byline',{id:item.id,revision:item.revision,site_id:target.site_id}); apply(result,target); info.textContent = 'Сведения об авторе добавлены в редактор: '+result.byline+'. Сохраните материал.'; });
    pickers.push({refresh});
  }
  picker('ct',() => window.contentAuthorTarget(),(result,target) => window.contentApplyAuthor(result,target));
  picker('vd',() => window.contentVideoAuthorTarget(),(result,target) => window.contentVideoApplyAuthor(result,target));
  async function load() { await act(async () => { if(!siteFields.size) { const sites = await core.call('content_sites',{}); for(const site of sites) { const label = document.createElement('label'); label.textContent = 'Подпись для '+site.domain; const input = document.createElement('input'); input.maxLength = 240; input.id = 'au-site-'+site.id; label.htmlFor = input.id; label.append(input); $('sites').append(label); siteFields.set(site.id,input); } } await list(); await refreshPickers(); }); }
  window.loadAuthorsPage = load; core.on('hello',load); if(core.connected)load();
  root.addEventListener('toggle',() => { if(root.open)load(); });
  window.addEventListener('beforeunload',e => { if(dirty){e.preventDefault();e.returnValue='';} });
  document.addEventListener('click',e => { const nav = e.target.closest('button[data-p]'); if(nav && nav.dataset.p !== 'articles' && dirty) { e.preventDefault(); e.stopImmediatePropagation(); discard().then(ok => { if(ok) { paint(current); nav.click(); } }); } },true);
})();
