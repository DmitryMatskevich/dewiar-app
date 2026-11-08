/* Real content editor shared by the studio and the isolated test harness. */
(function () {
  'use strict';
  const root = document.getElementById('content-app');
  if (!root) return;
  const state = {current:null, dirty:false, busy:false, sites:[], offset:0, total:0, listSeq:0, openSeq:0, request:null, sources:[], importKey:null};
  root.innerHTML = `<header class="ct-head"><div><p class="ct-eyebrow">Редакция</p><h1>Статьи и новости</h1><p>Материалы, авторы и история изменений. Публикация подключается отдельно.</p></div><button class="btn primary" id="ct-new">Новый материал</button></header>
  <div id="ct-message" role="status" aria-live="polite"></div>
  <div class="ct-layout"><aside class="ct-library" aria-label="Библиотека материалов">
    <label>Поиск<input id="ct-query" type="search" maxlength="300" placeholder="Название, автор или текст"></label>
    <div class="ct-filters"><label>Тип<select id="ct-filter"><option value="">Все материалы</option><option value="article">Статьи</option><option value="news">Новости</option></select></label><label class="ct-check"><input id="ct-archived" type="checkbox"> Архив</label></div>
    <details><summary>Из готовой транскрибации</summary><label for="ct-source">Текстовый результат<select id="ct-source"><option value="">Выберите результат</option></select></label><button class="btn" id="ct-import">Создать из источника</button><p class="ct-help">Исходный текст сохраняется со ссылкой на задачу. Распознавание заново не запускается.</p></details><p id="ct-count"></p><div id="ct-list"></div><div class="ct-pager"><button class="btn" id="ct-prev">Назад</button><button class="btn" id="ct-next">Далее</button></div>
    <details><summary>Импорт HTML</summary><label>Название импорта<input id="ct-html-title" maxlength="240"></label><label>Исходный HTML<textarea id="ct-html-input" rows="5" placeholder="Вставьте HTML статьи"></textarea></label><button class="btn" id="ct-html-import">Создать из HTML</button><p class="ct-help">Оригинал сохраняется. Формы и сложная разметка остаются защищёнными фрагментами.</p></details>
    <details><summary>Импорт пакета Dewiar</summary><label>ZIP статьи<input type="file" id="ct-package-file" accept=".zip,application/zip"></label><button class="btn" id="ct-package-import">Восстановить как новый черновик</button><p class="ct-help">До 8 МиБ. Контрольные суммы проверяются. Создаётся новый материал; редакционную готовность нужно проверить заново.</p></details>
  </aside><div class="ct-main"><p id="ct-empty">Выберите материал или создайте новый. Черновики сохраняются в рабочей папке.</p>
    <form id="ct-form" hidden><div class="ct-fields"><label for="ct-kind">Тип материала<select id="ct-kind"><option value="article">Статья</option><option value="news">Новость</option></select></label><label for="ct-site">Сайт<select id="ct-site"><option value="">Не выбран</option></select></label></div>
    <label>Название<input id="ct-title" maxlength="240" required></label><label>Автор<input id="ct-author" maxlength="240" placeholder="Имя автора"></label>
    <div id="ct-credit" hidden><p id="ct-credit-meta"></p><p id="ct-credit-bio" style="white-space:pre-wrap;overflow-wrap:anywhere"></p><button class="btn" id="ct-credit-remove" type="button">Отсоединить сведения об авторе</button></div>
    <div id="ct-blocks" aria-label="Блоки материала"></div><button class="btn" id="ct-add-block" type="button">Добавить блок</button><p class="ct-help">Защита блока сохраняет ручной текст при будущей генерации. Редактирование вручную доступно.</p>
    <p id="ct-meta"></p><div class="ct-actions"><button class="btn primary" id="ct-save" type="submit">Сохранить</button><button class="btn" id="ct-copy" type="button">Сохранить копию</button><button class="btn" id="ct-reload" type="button">Загрузить сохранённое</button><button class="btn" id="ct-archive" type="button">В архив</button></div>
    <button class="btn" id="ct-export" type="button">Скачать HTML и Markdown</button><button class="btn" id="ct-original" type="button" hidden>Скачать HTML-оригинал как текст</button><p id="ct-source-info"></p><details id="ct-history"><summary>История изменений</summary><div id="ct-revisions"></div><pre id="ct-diff" aria-live="polite"></pre></details>
    <details id="ct-generation"><summary>Предложение модели</summary><label>Модель<select id="ct-model"></select></label><label>Задание редактора<textarea id="ct-brief" rows="4" maxlength="6000" placeholder="Например: подготовь связную статью по исходному тексту"></textarea></label><p class="ct-help">Выбранной модели передаются отмеченные блоки, бриф и сохранённый источник. Защищённые блоки не заменяются. Результат сначала появится отдельным предложением.</p><button class="btn" id="ct-generate" type="button">Подготовить предложение</button><p id="ct-gen-status" aria-live="polite"></p><div id="ct-gen-jobs"></div><pre id="ct-gen-preview"></pre><button class="btn" id="ct-gen-apply" type="button" hidden>Применить предложение</button><button class="btn" id="ct-gen-cancel" type="button" hidden>Отменить предложение</button></details>
    <details id="ct-review"><summary>Редакционная проверка</summary><p id="ct-review-status" aria-live="polite"></p><p class="ct-help">Цитаты проверяются на совпадение с источником. Достоверность и соответствие смыслу проверяет редактор. Внешняя ссылка сохраняется с его пояснением.</p><label>Редактор проверки<input id="ct-reviewer" maxlength="240"></label><div id="ct-claims"></div><button class="btn" id="ct-review-save" type="button">Сохранить проверку</button><button class="btn" id="ct-review-ready" type="button">Отметить готовым</button><details><summary>Сохранённый источник</summary><select id="ct-source-view" aria-label="Источник для просмотра"></select><textarea id="ct-source-excerpt" aria-label="Фрагмент источника" readonly rows="8"></textarea><button class="btn" id="ct-source-prev" type="button">Предыдущий фрагмент</button><button class="btn" id="ct-source-next" type="button">Следующий фрагмент</button><p id="ct-source-page"></p></details></details>
    <details><summary>Предпросмотр текста</summary><div id="ct-preview"></div></details></form>
    <div id="content-site-editions"></div><div id="content-distribution"></div></div></div>`;
  const $ = id => document.getElementById('ct-'+id);
  let authorTargetStamp=0,authorCredit=null;
  window.contentAuthorTarget=()=>({stamp:authorTargetStamp,author:$('author').value,site_id:$('site').value||null});
  window.contentApplyAuthor=(result,target)=>{if(state.busy||state.current?.archived||JSON.stringify(target)!==JSON.stringify(window.contentAuthorTarget()))throw Error('Редактор изменился или занят. Выберите подпись заново.');$('author').value=result.byline;authorCredit=result.author_credit;paintCredit();changed();};
  function paintCredit(){ $('credit').hidden=!authorCredit;$('credit-meta').textContent=authorCredit?(authorCredit.origin==='catalog'?'Снимок профиля v'+authorCredit.catalog_ref.revision:'Сведения из пакета · связь с местным каталогом отсутствует')+' · '+authorCredit.name:'';$('credit-bio').textContent=authorCredit?.bio||'Биография не указана.'; }
  $('credit-remove').onclick=()=>{if(state.busy)return;authorCredit=null;paintCredit();changed();};
  for(const id of ['author','site'])$(id).addEventListener('input',()=>{authorCredit=null;paintCredit();});
  function message(text, error=false) { $('message').textContent=text; $('message').classList.toggle('ct-error',error); }
  function busy(on) {state.busy=on; root.querySelectorAll('button, #ct-form input, #ct-form textarea, #ct-form select').forEach(b=>b.disabled=on||b.dataset.permanentDisabled==='true'); if(!on) {$('prev').disabled=state.offset===0; $('next').disabled=state.offset+50>=state.total;} }
  window.contentConfirm=function(text,acceptLabel='Перейти без сохранения',cancelLabel='Остаться'){return new Promise(resolve=>{const dialog=document.createElement('dialog');dialog.className='ct-confirm';const p=document.createElement('p');p.textContent=text;dialog.append(p);for(const [label,value] of [[cancelLabel,false],[acceptLabel,true]]){const b=document.createElement('button');b.type='button';b.className='btn';b.textContent=label;b.onclick=()=>{dialog.close();dialog.remove();resolve(value);};dialog.append(b);}dialog.oncancel=e=>{e.preventDefault();dialog.close();dialog.remove();resolve(false);};root.append(dialog);dialog.showModal();});};
  async function discard() { const ok=(!state.dirty&&!state.reviewDirty&&!window.contentSiteEditionDirty?.()&&!(window.contentDistributionDirty?.()||window.contentSourcesDirty?.())) || await window.contentConfirm('Есть несохранённый текст, источник, версия сайта, посты или редакционная проверка. Перейти без сохранения?');if(ok)window.contentSourcesDiscard?.();return ok; }
  window.contentHasUnsavedChanges=()=>!!(state.dirty||state.reviewDirty||window.contentSiteEditionDirty?.()||(window.contentDistributionDirty?.()||window.contentSourcesDirty?.()));
  function values() {return {kind:$('kind').value,title:$('title').value,author:$('author').value,author_credit:authorCredit,blocks:readBlocks(),site_id:$('site').value||null};}
  function readBlocks() {return [...$('blocks').children].map(el=>({id:el.dataset.blockId,type:el.querySelector('select').value,text:el.querySelector('textarea').value,locked:el.querySelector('input').checked,...(el.dataset.originalRef?{original_ref:el.dataset.originalRef}:{})}));}
  function changed() {state.dirty=true;$('meta').textContent='Есть несохранённые изменения';$('gen-apply').hidden=true;for(const el of $('blocks').children){const locked=el.querySelector('input').checked;const check=el.querySelector('.ct-generate-block');check.dataset.permanentDisabled=String(locked);check.disabled=locked;if(locked)check.checked=false;}preview();}
  function preview() {
    $('preview').replaceChildren();
    for(const b of readBlocks()) {
      const el=document.createElement({paragraph:'p',heading:'h2',quote:'blockquote',list:'ul',unsupported:'p'}[b.type]);
      if(b.type==='list')b.text.split('\n').forEach(t=>{const li=document.createElement('li');li.textContent=t;el.append(li);});else el.textContent=b.text;
      $('preview').append(el);
    }
  }
  function paintBlocks(blocks) {
    $('blocks').replaceChildren();
    blocks.forEach((b,index)=>{
      const box=document.createElement('fieldset');box.className='ct-block';box.dataset.blockId=b.id;
      if(b.original_ref)box.dataset.originalRef=b.original_ref;
      const legend=document.createElement('legend');legend.textContent=`Блок ${index+1}`;box.append(legend);
      const select=document.createElement('select');select.setAttribute('aria-label',`Тип блока ${index+1}`);
      Object.entries({paragraph:'Абзац',heading:'Заголовок',quote:'Цитата',list:'Список'}).forEach(([key,label])=>select.append(new Option(label,key)));select.value=b.type;box.append(select);
      const area=document.createElement('textarea');area.rows=5;area.value=b.text;area.setAttribute('aria-label',`Текст блока ${index+1}`);box.append(area);
      const lockLabel=document.createElement('label');lockLabel.className='ct-check';const lock=document.createElement('input');lock.type='checkbox';lock.checked=b.locked;lockLabel.append(lock,document.createTextNode(' Защитить от генерации'));box.append(lockLabel);
      if(b.type==='unsupported'){select.replaceChildren(new Option('HTML — защищённый оригинал','unsupported'));select.disabled=true;select.dataset.permanentDisabled='true';area.readOnly=true;lock.disabled=true;lock.dataset.permanentDisabled='true';}
      const genLabel=document.createElement('label');genLabel.className='ct-check';const gen=document.createElement('input');gen.type='checkbox';gen.className='ct-generate-block';gen.checked=!b.locked;gen.disabled=b.locked;gen.dataset.permanentDisabled=String(b.locked);genLabel.append(gen,document.createTextNode(' Включить в предложение модели'));gen.addEventListener('input',e=>e.stopPropagation());box.append(genLabel);
      const actions=document.createElement('div');actions.className='ct-block-actions';
      [['Выше',-1],['Ниже',1],['Удалить блок',0]].forEach(([label,delta])=>{
        const button=document.createElement('button');button.type='button';button.className='btn';button.textContent=label;
        button.setAttribute('aria-label',`${label}: блок ${index+1}`);
        button.onclick=()=>{if(state.busy)return;const items=readBlocks();if(delta){const other=index+delta;if(other<0||other>=items.length)return;[items[index],items[other]]=[items[other],items[index]];}else{items.splice(index,1);if(!items.length)items.push({id:crypto.randomUUID(),type:'paragraph',text:'',locked:false});}paintBlocks(items);changed();};actions.append(button);
      });box.append(actions);$('blocks').append(box);
    });
    preview();
  }
  window.contentSourceCommit=async(args)=>{
    if(state.busy||state.dirty||state.reviewDirty)throw Error('Сначала сохраните текст статьи и редакционную проверку.');
    if(!state.current||args.id!==state.current.id||args.expected_revision!==state.current.revision)throw Error('Статья изменилась. Загрузите состав источников заново.');
    busy(true);try{const row=await core.call('content_source_change',args);paint(row);await list();return row;}finally{busy(false);}
  };
  function paint(row) {
    authorTargetStamp++;
    state.current=row; state.dirty=false; state.reviewDirty=false; state.request=null;
    $('form').hidden=false; $('empty').hidden=true;
    $('kind').value=row?.kind||'article'; $('site').value=row?.site_id||'';
    for(const key of ['title','author']) $(key).value=row?.[key]||'';
    authorCredit=row?.author_credit||null;paintCredit();
    paintBlocks(row?.blocks||[{id:crypto.randomUUID(),type:'paragraph',text:row?.body||'',locked:false}]);
    $('archive').hidden=!row; $('archive').textContent=row?.archived?'Восстановить из архива':'В архив';
    $('export').hidden=!row; $('original').hidden=true; $('source-info').textContent=''; $('copy').hidden=!row; $('reload').hidden=!row; $('history').hidden=!row;
    $('meta').textContent=row?`Версия ${row.revision} · ${new Date(row.updated_at).toLocaleString('ru-RU')}${row.archived?' · В архиве':''}`:'Новый черновик';
    $('revisions').replaceChildren();$('diff').textContent='';
    $('generation').hidden=!row;generationJob=null;$('gen-preview').textContent='';$('gen-status').textContent='';$('gen-apply').hidden=true;$('gen-cancel').hidden=true;clearTimeout(generationTimer);$('gen-jobs').replaceChildren();if(row)refreshGeneration(row);
    $('review').hidden=!row;reviewState=null;$('reviewer').value='';$('reviewer').readOnly=true;$('claims').replaceChildren();$('review-status').textContent='';$('source-excerpt').value='';sourcePage=0;if(row)loadReview(row);
    window.dispatchEvent(new CustomEvent('dewiar:content-selected',{detail:row}));
    if(row) {history(row);core.call('content_source_refs',{id:row.id,revision:row.revision}).then(refs=>{if(state.current?.id===row.id&&state.current.revision===row.revision){$('source-info').textContent=refs.map(r=>`Источник: ${r.task_title} · ${r.path||r.origin_url||'Текст редактора'} · требуется редакторская проверка`).join('\n');$('original').hidden=!refs.some(r=>r.source_kind==='html');}}).catch(e=>message(e.message,true));}
  }
  async function history(row) {
    try {
      const versions=await core.call('content_revisions',{id:row.id});
      if(state.current?.id!==row.id||state.current.revision!==row.revision) return;
      $('revisions').replaceChildren();
      for(const v of versions) {
        const line=document.createElement('div');line.className='ct-version';
        const label=document.createElement('span');label.textContent=`Версия ${v.revision} · ${new Date(v.created_at).toLocaleString('ru-RU')}`;line.append(label);
        if(v.revision!==row.revision) {
          const diff=document.createElement('button');diff.type='button';diff.className='btn';diff.textContent='Сравнить';diff.setAttribute('aria-label',`Сравнить версию ${v.revision} с ${row.revision}`);
          diff.onclick=()=>act(async()=>{const result=await core.call('content_diff',{id:row.id,from_revision:v.revision,to_revision:row.revision});if(state.current?.id!==row.id||state.current.revision!==row.revision)return;$('diff').textContent=`Сохранённые версии ${v.revision} → ${row.revision}\nИзменено блоков: ${result.blocks.length}\n${result.sources?.changed?'Источники изменены:\nБыло: '+result.sources.before.map(s=>(s.task_title||'Источник')+' ['+s.source_id.slice(4,12)+']').join(', ')+'\nСтало: '+result.sources.after.map(s=>(s.task_title||'Источник')+' ['+s.source_id.slice(4,12)+']').join(', ')+'\n':''}${result.author_credit?.changed?'Сведения об авторе изменены.\n':''}${Object.entries(result.fields).map(([k,v])=>`${k}: ${v.before??'—'} → ${v.after??'—'}`).join('\n')}\n${result.text||'Текст совпадает.'}${result.truncated?'\nСравнение сокращено.':''}`;});line.append(diff);
          const button=document.createElement('button');button.type='button';button.className='btn';button.textContent='Вернуть эту версию';
          button.onclick=()=>act(async()=>{if(!await discard())return;const current=state.current;const restored=await core.call('content_restore_revision',{id:current.id,expected_revision:current.revision,revision:v.revision});paint(restored);await list();message('Версия восстановлена как новая запись истории.');});line.append(button);
        }
        const sourcesButton=document.createElement('button');sourcesButton.type='button';sourcesButton.className='btn';sourcesButton.textContent='Источники версии';sourcesButton.setAttribute('aria-label',`Источники версии ${v.revision}`);
        sourcesButton.onclick=()=>act(async()=>{const refs=await core.call('content_source_records',{id:row.id,revision:v.revision});const parts=[`Источники сохранённой версии ${v.revision}`];for(const ref of refs){const part=await core.call('content_source_record_text',{id:row.id,revision:v.revision,source_id:ref.source_id,offset:0,limit:2000});parts.push(`${ref.task_title||'Источник'} · SHA256 ${ref.sha256}\n${part.text}${part.total>2000?'\n[Фрагмент. Полный текст — в пакете этой версии.]':''}`);}if(state.current?.id===row.id)$('diff').textContent=parts.join('\n\n');});line.append(sourcesButton);
        const exportButton=document.createElement('button');exportButton.type='button';exportButton.className='btn';exportButton.textContent='Скачать версию';exportButton.setAttribute('aria-label',`Скачать версию ${v.revision}`);exportButton.onclick=()=>act(async()=>download(await core.call('content_export',{id:row.id,revision:v.revision})));line.append(exportButton);
        $('revisions').append(line);
      }
    } catch(e){message(e.message,true);}
  }
  async function list() {
    const seq=++state.listSeq;
    const res=await core.call('content_list',{query:$('query').value,kind:$('filter').value,archived:$('archived').checked,offset:state.offset,limit:50});
    if(seq!==state.listSeq) return;
    state.total=res.total;$('count').textContent=`Материалов: ${res.total}`;$('list').replaceChildren();
    if(!res.items.length) {$('list').textContent='Здесь пока нет материалов.';}
    for(const row of res.items) {
      const button=document.createElement('button');button.type='button';button.className='ct-row';
      const title=document.createElement('strong');title.textContent=row.title;
      const sub=document.createElement('span');sub.textContent=`${row.kind==='news'?'Новость':'Статья'} · ${row.author||'Автор не указан'} · v${row.revision}`;
      const site=document.createElement('small');site.textContent=state.sites.find(s=>s.id===row.site_id)?.domain||'Сайт не выбран';button.append(title,sub,site);
      button.onclick=()=>act(async()=>{if(!await discard())return;const seq=++state.openSeq;const full=await core.call('content_get',{id:row.id});if(seq===state.openSeq)paint(full);message('');});$('list').append(button);
    }
    $('prev').disabled=state.offset===0;$('next').disabled=state.offset+50>=state.total;
  }
  async function act(fn) {if(state.busy)return;if(window.contentSourcesBusy?.()){message('Дождитесь сохранения источника.');return;}busy(true);try{await fn();}catch(e){message(e.message||'Не удалось выполнить действие. Введённый текст сохранён в форме.',true);}finally{busy(false);}}
  async function save(copy=false) {
    if(state.reviewDirty&&!await window.contentConfirm('В редакционной проверке есть несохранённые записи. Сохранение текста сбросит эти записи; проверку новой версии нужно выполнить заново.','Сохранить текст и сбросить проверку','Вернуться к редактированию'))return;
    if(copy&&(window.contentSiteEditionDirty?.()||(window.contentDistributionDirty?.()||window.contentSourcesDirty?.())))throw Error('Сначала сохраните версию сайта и варианты постов.');
    const data=values();if(!data.title.trim()) {$('title').focus();throw Error('Введите название материала');}
    let row;
    if(state.current&&!copy) row=await core.call('content_update',{id:state.current.id,expected_revision:state.current.revision,patch:data});
    else {
      const signature=JSON.stringify(data);if(state.request?.signature!==signature)state.request={signature,key:crypto.randomUUID()};
      row=copy?await core.call('content_copy',{id:state.current.id,source_revision:state.current.revision,data,request_key:state.request.key}):await core.call('content_create',{...data,request_key:state.request.key});
    }
    paint(row);await list();message(copy?'Копия сохранена.':'Материал сохранён.');
  }
  function download(result) {
    const bytes=Uint8Array.from(atob(result.b64),c=>c.charCodeAt(0));const url=URL.createObjectURL(new Blob([bytes],{type:result.mime}));
    const a=document.createElement('a');a.href=url;a.download=result.filename;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);
  }
  $('original').onclick=()=>act(async()=>{download(await core.call('content_html_original',{id:state.current.id}));message('Оригинальная разметка сохранена как текстовый файл.');});
  let htmlRequest=null;
  $('html-import').onclick=()=>act(async()=>{
    if(!await discard())return;const data={html:$('html-input').value,title:$('html-title').value};const signature=JSON.stringify(data);
    if(htmlRequest?.signature!==signature)htmlRequest={signature,key:crypto.randomUUID()};
    const row=await core.call('content_import_html',{...data,request_key:htmlRequest.key});htmlRequest=null;paint(row);await list();message('HTML импортирован. Проверьте защищённые фрагменты перед экспортом.');
  });
  $('export').onclick=()=>act(async()=>{
    if(state.dirty)throw Error('Сначала сохраните изменения для экспорта этой версии.');
    download(await core.call('content_export',{id:state.current.id,revision:state.current.revision}));message('Подготовлен ZIP: HTML, Markdown, исходник и контрольные суммы.');
  });
  let packageRequest=null;
  $('package-import').onclick=()=>act(async()=>{
    if(!await discard())return;const file=$('package-file').files[0];if(!file)throw Error('Выберите ZIP статьи.');if(file.size>8*1024*1024)throw Error('Пакет превышает 8 МиБ.');
    const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));const b64=btoa(binary);
    if(packageRequest?.b64!==b64)packageRequest={b64,key:crypto.randomUUID()};
    const row=await core.call('content_import_package',{b64,request_key:packageRequest.key});packageRequest=null;paint(row);await list();message('Пакет восстановлен как новый черновик. Источники сохранены; готовность проверяется заново.');
  });
  $('import').onclick=()=>act(async()=>{
    if(!await discard())return;const index=$('source').value;if(index==='')throw Error('Выберите готовый текстовый результат.');
    const source=state.sources[Number(index)];if(!source)throw Error('Источник недоступен');
    if(!state.importKey||state.importKey.source!==source.path+source.task_id)state.importKey={source:source.path+source.task_id,key:crypto.randomUUID()};
    const row=await core.call('content_import_source',{task_id:source.task_id,path:source.path,request_key:state.importKey.key});
    state.importKey=null;paint(row);await list();message('Создан черновик из сохранённого текста. Проверьте автора и содержание.');
  });
  $('form').onsubmit=e=>{e.preventDefault();act(()=>save());};
  $('copy').onclick=()=>act(()=>save(true));
  $('new').onclick=()=>act(async()=>{if(await discard()){++state.openSeq;paint(null);message('');$('title').focus();}});
  $('reload').onclick=()=>act(async()=>{if(await discard()){paint(await core.call('content_get',{id:state.current.id}));message('Загружена сохранённая версия.');}});
  $('archive').onclick=()=>act(async()=>{if(!await discard())return;const row=await core.call('content_archive',{id:state.current.id,expected_revision:state.current.revision,archived:!state.current.archived});paint(row);await list();message(row.archived?'Материал перемещён в архив. Его можно восстановить.':'Материал восстановлен.');});
  $('form').oninput=e=>{if(e.target.closest('#ct-review')){if(e.target.id!=='ct-source-view')state.reviewDirty=true;return;}if(!e.target.closest('#ct-generation'))changed();};
  $('add-block').onclick=()=>{if(state.busy)return;const blocks=readBlocks();if(blocks.length>=500){message('Допускается не более 500 блоков.',true);return;}blocks.push({id:crypto.randomUUID(),type:'paragraph',text:'',locked:false});paintBlocks(blocks);changed();$('blocks').lastElementChild.querySelector('textarea').focus();};
  let searchTimer;
  for(const id of ['query','filter','archived']) $(id).addEventListener(id==='query'?'input':'change',()=>{clearTimeout(searchTimer);state.offset=0;searchTimer=setTimeout(()=>list().catch(e=>message(e.message,true)),180);});
  $('prev').onclick=()=>{state.offset=Math.max(0,state.offset-50);act(list);};$('next').onclick=()=>{state.offset+=50;act(list);};
  let generationTimer=null,generationJob=null,generationRequest=null;
  const genStates={running:'Генерация выполняется',proposed:'Предложение готово. Статья ещё не изменена.',applied:'Предложение применено',canceled:'Предложение отменено',failed:'Генерация не завершена',interrupted:'Генерация прервана'};
  async function showGeneration(id,row) {
    const job=await core.call('content_generation_get',{job_id:id});if(state.current?.id!==row.id)return;
    generationJob=job;$('gen-status').textContent=`${genStates[job.state]||job.state} · база v${job.base_revision}${job.error?' · '+job.error:''}${job.input.model.test_only?' · ТЕСТОВЫЙ РЕЗУЛЬТАТ':''}`;
    $('gen-preview').textContent=job.output?job.output.replacements.map(r=>`Блок ${r.id}\nБыло: ${job.input.blocks.find(b=>b.id===r.id)?.text||''}\nПредложено: ${r.text}`).join('\n\n')+'\n\nЗамечания:\n'+job.output.issues.join('\n'):'';
    if(job.state==='proposed'&&job.base_revision!==state.current.revision)$('gen-status').textContent+=' · Статья изменилась: применение к этой версии недоступно.';
    $('gen-apply').hidden=job.state!=='proposed'||job.base_revision!==state.current.revision||state.dirty;$('gen-cancel').hidden=!['running','proposed'].includes(job.state);
  }
  async function refreshGeneration(row) {
    try{const jobs=await core.call('content_generation_list',{id:row.id});if(state.current?.id!==row.id)return;$('gen-jobs').replaceChildren();
      jobs.forEach(job=>{const b=document.createElement('button');b.type='button';b.className='btn';b.textContent=`v${job.base_revision} · ${genStates[job.state]||job.state}`;b.onclick=()=>act(()=>showGeneration(job.id,row));$('gen-jobs').append(b);});
      const selected=jobs.find(j=>j.id===generationJob?.id)||jobs[0];if(selected)await showGeneration(selected.id,row);
      if(jobs.some(j=>j.state==='running')){clearTimeout(generationTimer);generationTimer=setTimeout(()=>refreshGeneration(row),1200);}
    }catch(e){message(e.message,true);}
  }
  $('generate').onclick=()=>act(async()=>{
    if(state.dirty)throw Error('Сначала сохраните статью.');const row=state.current;
    const data={id:row.id,expected_revision:row.revision,brief:$('brief').value,profile_id:$('model').value,block_ids:[...$('blocks').children].filter(el=>el.querySelector('.ct-generate-block').checked).map(el=>el.dataset.blockId)};
    const signature=JSON.stringify(data);if(generationRequest?.signature!==signature)generationRequest={signature,key:crypto.randomUUID()};
    const job=await core.call('content_generate',{...data,request_key:generationRequest.key});generationRequest=null;await showGeneration(job.id,row);await refreshGeneration(row);
  });
  $('gen-cancel').onclick=()=>act(async()=>{await core.call('content_generation_cancel',{job_id:generationJob.id});await refreshGeneration(state.current);});
  $('gen-apply').onclick=()=>act(async()=>{if(state.dirty)throw Error('Сначала сохраните правки или загрузите сохранённую версию.');if(state.reviewDirty&&!await window.contentConfirm('Применение предложения сбросит несохранённую редакционную проверку.','Применить и сбросить проверку','Вернуться к редактированию'))return;const row=await core.call('content_generation_apply',{job_id:generationJob.id});paint(row);await list();message('Предложение применено. Проверьте факты и источники.');});
  let reviewState=null,sourcePage=0,sourceTotal=0;
  function paintReview(report) {
    reviewState=report;$('reviewer').readOnly=false;state.reviewDirty=false;$('reviewer').value=report.review.reviewer;$('review-status').textContent=`Версия ${report.revision} · ${report.ready?'Готово к подготовке публикации':'Проверка не завершена'}${report.issues.length?' · '+report.issues.join('; '):''}`;
    $('claims').replaceChildren();
    report.claims.forEach((claim,index)=>{
      const box=document.createElement('fieldset');box.className='ct-claim';box.dataset.blockId=claim.block_id;const legend=document.createElement('legend');legend.textContent=claim.block_id==='$title'?'Название статьи':`Блок ${index}`;box.append(legend);
      const excerpt=document.createElement('p');excerpt.textContent=claim.text.slice(0,500)+(claim.text.length>500?'…':'');box.append(excerpt);
      if(claim.numbers.length){const nums=document.createElement('p');nums.className='ct-help';nums.textContent='Проверить числа: '+claim.numbers.join(', ');box.append(nums);}
      function field(name,label,kind='input') {const el=document.createElement(kind);el.dataset.reviewField=name;el.setAttribute('aria-label',`${label}: ${index}`);const wrap=document.createElement('label');wrap.append(document.createTextNode(label),el);box.append(wrap);return el;}
      const status=field('status','Решение','select');Object.entries({needs_review:'Требует проверки',supported:'Подтверждено редактором по источнику',opinion:'Авторское суждение',disputed:'Спорное утверждение'}).forEach(([k,v])=>status.append(new Option(v,k)));status.value=claim.status;
      const source=field('source_id','Сохранённый источник','select');source.append(new Option('Выберите источник',''));report.sources.forEach(s=>source.append(new Option(s.title+' · '+s.source_id.slice(4,12),s.source_id)));source.value=claim.source_id||report.sources.find(s=>s.sha256===claim.source_sha256)?.source_id||report.sources[0]?.source_id||'';
      for(const [name,label,kind,max] of [['quote','Выдержка из источника','textarea',6000],['url','Внешняя ссылка (если источник вне Dewiar)','input',2000],['note','Пояснение редактора','textarea',2000]]){const el=field(name,label,kind);el.maxLength=max;el.value=claim[name]||'';}
      $('claims').append(box);
    });
    $('source-view').replaceChildren(new Option(report.sources.length?'Выберите источник':'Сохранённых источников нет',''));report.sources.forEach(s=>$('source-view').append(new Option(s.title+' · '+s.source_id.slice(4,12),s.source_id)));
  }
  async function loadReview(row) {try{const report=await core.call('content_review',{id:row.id});if(state.current?.id===row.id&&state.current.revision===report.revision)paintReview(report);}catch(e){message(e.message,true);}}
  async function saveReview(ready) {
    if(state.dirty)throw Error('Сначала сохраните текст статьи.');if(!reviewState)throw Error('Проверка ещё загружается.');
    const decisions=[...$('claims').children].map(box=>{const d={block_id:box.dataset.blockId};box.querySelectorAll('[data-review-field]').forEach(el=>d[el.dataset.reviewField]=el.value);d.source_sha256=reviewState.sources.find(s=>s.source_id===d.source_id)?.sha256||'';return d;});
    const result=await core.call('content_review_save',{id:state.current.id,expected_revision:state.current.revision,expected_review_version:reviewState.review.review_version,reviewer:$('reviewer').value,decisions,ready});paintReview(result);message(ready?'Эта версия отмечена готовой. Публикация ещё не выполнялась.':'Редакторская проверка сохранена.');
  }
  $('review-save').onclick=()=>act(()=>saveReview(false));$('review-ready').onclick=()=>act(()=>saveReview(true));
  async function sourceExcerpt() {if(!$('source-view').value){$('source-excerpt').value='';return;}const res=await core.call('content_source_excerpt',{id:state.current.id,revision:state.current.revision,source_id:$('source-view').value,offset:sourcePage,limit:20000});sourceTotal=res.total;$('source-excerpt').value=res.text;$('source-page').textContent=`Символы ${res.offset+1}–${Math.min(res.offset+20000,res.total)} из ${res.total}`;}
  $('source-view').onchange=()=>act(async()=>{sourcePage=0;await sourceExcerpt();});$('source-prev').onclick=()=>act(async()=>{sourcePage=Math.max(0,sourcePage-20000);await sourceExcerpt();});$('source-next').onclick=()=>act(async()=>{if(sourcePage+20000<sourceTotal)sourcePage+=20000;await sourceExcerpt();});
  async function load() {await act(async()=>{state.sites=await core.call('content_sites',{});const chosen=$('site').value;$('site').replaceChildren(new Option('Не выбран',''));state.sites.forEach(s=>$('site').append(new Option(s.domain,s.id)));$('site').value=chosen;state.sources=await core.call('content_sources',{});$('source').replaceChildren(new Option(state.sources.length?'Выберите результат':'Нет готовых текстовых результатов',''));state.sources.forEach((s,i)=>$('source').append(new Option(s.title,String(i))));const models=await core.call('content_models',{});const model=$('model').value;$('model').replaceChildren(new Option(models.length?'Выберите модель':'Нет настроенных моделей',''));models.forEach(p=>$('model').append(new Option(p.name+' · '+p.model,p.id)));$('model').value=model;await list();});}
  window.addEventListener('dewiar:studio-page',e=>{if(e.detail.page==='articles')load();});
  window.addEventListener('beforeunload',e=>{if(state.dirty||state.reviewDirty||window.contentSiteEditionDirty?.()||(window.contentDistributionDirty?.()||window.contentSourcesDirty?.())){e.preventDefault();e.returnValue='';}});
  core.on('hello',()=>{if(document.querySelector('#p-articles.cur'))load();});
  window.loadContentPage=load;
})();
