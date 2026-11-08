/* Independent briefs/storyboards. Planned times are not measured audio alignment. */
(function () {
  'use strict';
  const root = document.getElementById('video-app'); if (!root) return;
  root.innerHTML = `<div class="vd-head"><div><p class="vd-eyebrow">ОТ ИДЕИ К РОЛИКУ</p><h1>Видео и рилсы</h1><p>Сценарий, последовательность сцен и отдельные короткие версии.</p></div><button class="btn" id="vd-new">Новое видео</button></div>
    <p id="vd-message" role="status" aria-live="polite"></p><div class="vd-layout"><aside aria-label="Библиотека видео"><label>Поиск видео<input id="vd-query" type="search" maxlength="240"></label><label>Тип в списке<select id="vd-filter"><option value="">Все материалы</option><option value="video">Видео</option><option value="reel">Рилсы</option></select></label><label class="vd-check"><input type="checkbox" id="vd-archived"> Архив видео</label><p id="vd-count"></p><div id="vd-list"></div><div class="vd-actions"><button class="btn" id="vd-prev">Назад</button><button class="btn" id="vd-next">Далее</button></div></aside>
    <div class="vd-editor"><p id="vd-meta"></p><p class="vd-note">Длительности в раскадровке — план автора. Готовые файлы и результаты их проверки находятся в разделе «Монтаж и готовые файлы» ниже.</p>
    <form id="vd-form"><div class="vd-grid"><label>Название видео<input id="vd-title" maxlength="240" required></label><label>Автор видео<input id="vd-author" maxlength="240"></label><label>Тип материала<select id="vd-kind"><option value="video">Видео</option><option value="reel">Рилс</option></select></label><label>Проект видео<select id="vd-site"><option value="">Без проекта</option></select></label><label>Язык сценария<select id="vd-language"><option value="ru">Русский</option><option value="en">English</option></select></label><label>Формат кадра<select id="vd-aspect"><option value="16:9">16:9 · горизонтальный</option><option value="9:16">9:16 · вертикальный</option><option value="1:1">1:1 · квадратный</option></select></label></div>
    <label>Бриф видео<textarea id="vd-brief" rows="3" maxlength="10000" placeholder="Для кого ролик, главная мысль, что показать"></textarea></label><label>Сценарий видео<textarea id="vd-script" rows="7" maxlength="100000"></textarea></label>
    <div class="vd-head"><div><h2>Раскадровка</h2><p id="vd-duration"></p></div><button class="btn" type="button" id="vd-add-scene">Добавить сцену</button></div><div id="vd-scenes"></div>
    <div class="vd-actions"><button class="btn" type="submit" id="vd-save">Сохранить видео</button><button class="btn" type="button" id="vd-reload">Загрузить видео заново</button><button class="btn" type="button" id="vd-archive">В архив</button><button class="btn" type="button" id="vd-reel">Создать рилс из выбранных сцен</button></div></form>
    <section id="vd-source-section" hidden><h2>Закреплённый источник</h2><p id="vd-source-meta"></p><p id="vd-source-warning" class="vd-note"></p><pre id="vd-source-text"></pre><div class="vd-actions"><button class="btn" id="vd-source-prev">Предыдущий фрагмент</button><button class="btn" id="vd-source-next">Следующий фрагмент</button></div></section>
    <section id="vd-history-section" hidden><h2>История видео</h2><label>Сохранённая версия<select id="vd-history"></select></label><div class="vd-actions"><button class="btn" id="vd-history-view">Посмотреть версию</button><button class="btn" id="vd-history-more">Ещё версии</button><button class="btn" id="vd-history-restore">Восстановить выбранную версию</button></div><pre id="vd-history-text"></pre></section></div></div>`;
  const $ = s => document.getElementById('vd-' + s);
  const provenance=document.createElement('details');provenance.id='vd-provenance';provenance.hidden=true;
  provenance.innerHTML='<summary>Источники статьи, сохранённые в видео</summary><p id="vd-records-note"></p><label>Закреплённый первоисточник<select id="vd-record"></select></label><p id="vd-record-meta" style="overflow-wrap:anywhere"></p><pre id="vd-record-text" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre><div class="vd-actions"><button class="btn" id="vd-record-prev">Назад по источнику</button><button class="btn" id="vd-record-next">Далее по источнику</button></div>';
  $('source-section').after(provenance);
  let sourceRecords=[],recordOffset=0,recordTotal=0;
  const sourceSegments=new Map();
  let row = null, dirty = false, pending = false, offset = 0, total = 0, request = null, historyOffset = 0, sourceOffset = 0, sourceTotal = 0;
  let authorTargetStamp=0,authorCredit=null;
  window.contentVideoAuthorTarget=()=>({stamp:authorTargetStamp,author:$('author').value,site_id:$('site').value||null});
  const creditBox=document.createElement('div');creditBox.id='vd-credit';creditBox.hidden=true;creditBox.innerHTML='<p id="vd-credit-meta"></p><p id="vd-credit-bio" style="white-space:pre-wrap;overflow-wrap:anywhere"></p><button class="btn" id="vd-credit-remove" type="button">Отсоединить сведения об авторе видео</button>';$('author').closest('label').after(creditBox);
  window.contentVideoApplyAuthor=(result,target)=>{if(pending||row?.archived||JSON.stringify(target)!==JSON.stringify(window.contentVideoAuthorTarget()))throw Error('Редактор видео изменился или занят. Выберите подпись заново.');$('author').value=result.byline;authorCredit=result.author_credit;paintCredit();modified();};
  function paintCredit(){creditBox.hidden=!authorCredit;$('credit-meta').textContent=authorCredit?(authorCredit.origin==='catalog'?'Снимок профиля v'+authorCredit.catalog_ref.revision:'Сведения из пакета · связь с местным каталогом отсутствует')+' · '+authorCredit.name:'';$('credit-bio').textContent=authorCredit?.bio||'Биография не указана.';}
  $('credit-remove').onclick=()=>{if(pending||row?.archived)return;authorCredit=null;paintCredit();modified();};
  for(const id of ['author','site'])$(id).addEventListener('input',()=>{authorCredit=null;paintCredit();});
  const fields = {title:'title', author:'author', kind:'kind', site_id:'site', language:'language', aspect:'aspect', brief:'brief', script:'script'};
  const sceneControls = new Map();
  function message(text, error = false) { $('message').textContent = text; $('message').classList.toggle('vd-error', error); }
  function modified() { dirty = true; $('meta').textContent = 'Есть несохранённые изменения'; }
  function seconds(us) { return String(Math.floor(us / 1000000)) + (us % 1000000 ? '.' + String(us % 1000000).padStart(6,'0').replace(/0+$/,'') : ''); }
  function micros(value) {
    if (!/^\d{1,4}(?:[.,]\d{1,6})?$/.test(value)) throw Error('Длительность: укажите секунды, не более шести знаков после запятой.');
    const [whole, fraction = ''] = value.replace(',','.').split('.');
    const result = Number(whole) * 1000000 + Number(fraction.padEnd(6,'0'));
    if (result < 1 || result > 3600000000) throw Error('Длительность сцены должна быть больше нуля и не больше 3600 секунд.');
    return result;
  }
  function discard() {
    if (!dirty && !window.contentVideoExchangeDirty?.() && !window.contentVideoCaptionsDirty?.() && !window.contentVideoReviewDirty?.() && !window.contentVideoDistributionDirty?.()) return Promise.resolve(true);
    return new Promise(resolve => {
      const dialog = document.createElement('dialog'); dialog.className = 'vd-dialog';
      const p = document.createElement('p'); p.textContent = 'В видео есть несохранённые изменения. Перейти без сохранения?'; dialog.append(p);
      for (const [label,result] of [['Остаться',false],['Перейти без сохранения',true]]) {
        const b = document.createElement('button'); b.type = 'button'; b.className = 'btn'; b.textContent = label;
        b.onclick = () => { dialog.close(); dialog.remove(); if(result){window.contentVideoExchangeDiscard?.();window.contentVideoCaptionsDiscard?.();window.contentVideoReviewDiscard?.();window.contentVideoDistributionDiscard?.();} resolve(result); }; dialog.append(b);
      }
      dialog.oncancel = e => { e.preventDefault(); dialog.close(); dialog.remove(); resolve(false); };
      root.append(dialog); dialog.showModal();
    });
  }
  function locks() {
    root.querySelectorAll('button,input,select,textarea').forEach(e => e.disabled = pending);
    if (!pending) {
      $('kind').disabled = !!row; $('prev').disabled = offset === 0; $('next').disabled = offset + 50 >= total;
      $('source-prev').disabled = sourceOffset === 0; $('source-next').disabled = sourceOffset + 20000 >= sourceTotal;
      $('record-prev').disabled=recordOffset===0;$('record-next').disabled=recordOffset+20000>=recordTotal;$('record').disabled=!sourceRecords.length;
      if (row?.archived) $('form').querySelectorAll('input,select,textarea,button').forEach(e => e.disabled = !['vd-reload','vd-archive'].includes(e.id));
      $('history-restore').disabled = !row || row.archived;
      window.contentVideoCaptionsLock?.();window.contentVideoReviewLock?.();
    }
  }
  async function act(fn) { if (pending) return; if(window.contentVideoExchangeBusy?.()||window.contentVideoCaptionsBusy?.()||window.contentVideoReviewBusy?.()||window.contentVideoDistributionBusy?.()){message('Дождитесь операции с пакетом или файлом.');return;} pending = true; locks(); try { await fn(); } catch(e) { message(e.message, true); } finally { pending = false; locks(); } }
  window.contentVideoOperationBusy=()=>pending;
  function scenes() {
    return [...sceneControls].map(([id,c]) => ({id, title:c.title.value, narration:c.narration.value, visual_prompt:c.visual_prompt.value, duration_us:micros(c.duration.value), locked:c.locked.checked,...(c.source_segments.length?{source_segments:c.source_segments.map(ref=>({...ref}))}:{})}));
  }
  async function segmentsFor(sourceId) {
    if(!sourceSegments.has(sourceId))sourceSegments.set(sourceId,await core.call('content_video_source_segments',{id:row.id,revision:row.revision,source_id:sourceId}));
    return sourceSegments.get(sourceId);
  }
  function segmentText(item,origin) {
    return '«'+item.quote+'»\n'+(item.speaker_label?'Говорящий: '+item.speaker_label+' · ':'')+(item.start_us===null?'Время не указано':seconds(item.start_us)+'–'+seconds(item.end_us)+' с')+' · '+(origin==='package_claimed'?'Метки из импортированного пакета':'Ручные метки')+' · привязка к медиа не проверена';
  }
  function sceneCitations(card,controls,index) {
    const details=document.createElement('details');details.className='vd-citations';
    const summary=document.createElement('summary'),note=document.createElement('p'),list=document.createElement('div');
    note.className='vd-note';note.textContent='Фрагменты обосновывают сцену. Речь и плановая длительность сцены редактируются отдельно.';
    const sourceLabel=document.createElement('label');sourceLabel.textContent='Источник фрагмента';
    const sourceSelect=document.createElement('select');sourceSelect.setAttribute('aria-label','Источник фрагмента сцены '+(index+1));sourceLabel.append(sourceSelect);
    for(const item of sourceRecords.filter(x=>x.segment_count))sourceSelect.append(new Option(item.task_title||item.source_kind||'Источник',item.source_id));
    const segmentLabel=document.createElement('label');segmentLabel.textContent='Фрагмент источника';
    const segmentSelect=document.createElement('select');segmentSelect.setAttribute('aria-label','Фрагмент источника сцены '+(index+1));segmentLabel.append(segmentSelect);
    const preview=document.createElement('p');preview.className='vd-citation-text';
    const attach=document.createElement('button');attach.type='button';attach.className='btn';attach.textContent='Привязать фрагмент';attach.setAttribute('aria-label','Привязать фрагмент к сцене '+(index+1));
    const error=document.createElement('p');error.setAttribute('role','status');
    function count(){summary.textContent='Основание сцены · фрагментов: '+controls.source_segments.length;}
    function selection(){const data=sourceSegments.get(sourceSelect.value),item=data?.items.find(x=>x.id===segmentSelect.value);preview.textContent=item?segmentText(item,data.timing_origin):'В источнике нет размеченных фрагментов.';}
    async function choose(){segmentSelect.replaceChildren();if(sourceSelect.value){const data=await segmentsFor(sourceSelect.value);for(const [i,item] of data.items.entries())segmentSelect.append(new Option((i+1)+'. '+item.quote.slice(0,90),item.id));}selection();}
    async function render(){
      list.replaceChildren();
      for(const ref of controls.source_segments){
        const data=await segmentsFor(ref.source_id),item=data.items.find(x=>x.id===ref.segment_id);
        if(!item)throw Error('Сохранённый фрагмент не найден. Загрузите видео заново.');
        const block=document.createElement('div');block.className='vd-citation';
        const label=document.createElement('strong');label.textContent=sourceRecords.find(x=>x.source_id===ref.source_id)?.task_title||'Закреплённый источник';
        const text=document.createElement('p');text.className='vd-citation-text';text.textContent=segmentText(item,data.timing_origin);
        const remove=document.createElement('button');remove.type='button';remove.className='btn';remove.textContent='Удалить связь';remove.setAttribute('aria-label','Удалить связь с фрагментом '+item.id+' сцены '+(index+1));
        remove.onclick=()=>act(async()=>{controls.source_segments=controls.source_segments.filter(x=>x!==ref);count();modified();await render();});
        block.append(label,text,remove);list.append(block);
      }
      count();
    }
    sourceSelect.onchange=()=>act(choose);segmentSelect.onchange=selection;
    attach.onclick=()=>act(async()=>{
      error.textContent='';const source_id=sourceSelect.value,segment_id=segmentSelect.value;
      if(!source_id||!segment_id){error.textContent='Сначала разметьте фрагменты в источнике статьи, затем создайте видеозадачу из этой версии.';return;}
      if(controls.source_segments.some(x=>x.source_id===source_id&&x.segment_id===segment_id)){error.textContent='Этот фрагмент уже привязан.';return;}
      if(controls.source_segments.length>=20)throw Error('У сцены может быть до 20 ссылок на фрагменты.');
      controls.source_segments.push({source_id,segment_id});modified();await render();
    });
    let loaded=false;
    details.ontoggle=()=>{if(details.open&&!loaded)act(async()=>{await render();await choose();loaded=true;});};
    details.append(summary,note,list);
    if(sourceSelect.options.length)details.append(sourceLabel,segmentLabel,preview,attach,error);
    else {const empty=document.createElement('p');empty.textContent='В закреплённых источниках нет разметки. Добавьте фрагменты в статье и создайте видео из новой версии.';details.append(empty);}
    count();card.append(details);
  }
  function duration() {
    try {
      let start = 0; for (const c of sceneControls.values()) { const end = start + micros(c.duration.value); c.time.textContent = `${seconds(start)}–${seconds(end)} с · план`; start = end; }
      $('duration').textContent = `${sceneControls.size} сцен · плановая длительность ${seconds(start)} с`;
    } catch(e) { $('duration').textContent = e.message; }
  }
  function drawScenes(items, selected = new Set()) {
    sceneControls.clear(); $('scenes').replaceChildren();
    if (!items.length) { const p = document.createElement('p'); p.className = 'vd-empty'; p.textContent = 'Пока нет сцен. Добавьте первую: опишите кадр и текст речи.'; $('scenes').append(p); }
    items.forEach((scene,index) => {
      const card = document.createElement('section'); card.className = 'vd-scene';
      const head = document.createElement('div'); head.className = 'vd-scene-head';
      const title = document.createElement('h3'); title.textContent = 'Сцена ' + (index + 1); head.append(title);
      const time = document.createElement('span'); time.className = 'vd-time'; head.append(time); card.append(head);
      const controls = {time,source_segments:(scene.source_segments||[]).map(ref=>({...ref}))};
      for (const [key,label,type,value] of [['title','Название сцены','text',scene.title],['duration','Длительность, с','text',seconds(scene.duration_us)],['narration','Речь в сцене','area',scene.narration],['visual_prompt','Что в кадре','area',scene.visual_prompt],['locked','Защитить от будущей генерации','checkbox',scene.locked],['selected','Включить в отдельный рилс','checkbox',selected.has(scene.id)]]) {
        const lab = document.createElement('label'); lab.textContent = label;
        const input = document.createElement(type === 'area' ? 'textarea' : 'input');
        input.id = `vd-scene-${scene.id}-${key}`; lab.htmlFor = input.id;
        if (type === 'area') { input.rows = 3; input.maxLength = 10000; }
        else { input.type = type; if(type === 'text') input.maxLength = key === 'title' ? 240 : 12; }
        if (key === 'duration') input.inputMode = 'decimal';
        if (type === 'checkbox') { input.checked = !!value; lab.className = 'vd-check'; lab.prepend(input); } else { input.value = value || ''; lab.append(input); }
        input.oninput = () => { if (key !== 'selected') modified(); if (key === 'duration') duration(); };
        controls[key] = input; card.append(lab);
      }
      sceneCitations(card,controls,index);
      const actions = document.createElement('div'); actions.className = 'vd-actions';
      for (const [label, delta] of [['Выше',-1],['Ниже',1],['Удалить сцену',0]]) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'btn'; button.textContent = label; button.setAttribute('aria-label', `${label} ${index+1}`);
        button.onclick = () => act(async () => { const all = scenes(), chosen = selectedScenes(); const at = all.findIndex(s => s.id === scene.id); if(delta) { const to = at + delta; if(to < 0 || to >= all.length)return; [all[at],all[to]] = [all[to],all[at]]; } else all.splice(at,1); drawScenes(all,chosen); modified(); }); actions.append(button);
      }
      card.append(actions); $('scenes').append(card); sceneControls.set(scene.id, controls);
    }); duration();
  }
  function selectedScenes() { return new Set([...sceneControls].filter(([,c]) => c.selected.checked).map(([id]) => id)); }
  function data() { const out = Object.fromEntries(Object.entries(fields).map(([key,id]) => [key,$(id).value])); out.site_id ||= null; out.scenes = scenes();out.author_credit=authorCredit; return out; }
  async function history(reset = true) {
    if (!row) return;
    if (reset) { $('history').replaceChildren(); historyOffset = 0; $('history-text').textContent = ''; }
    const list = await core.call('content_video_revisions', {id:row.id,offset:historyOffset,limit:50});
    list.forEach(r => $('history').append(new Option(`Версия ${r.revision} · ${r.title}${r.archived?' · архив':''}`,r.revision)));
    historyOffset += list.length; $('history-more').hidden = list.length < 50;
  }
  async function source() {
    const result = row?.source_ref ? await core.call('content_video_source',{id:row.id,offset:sourceOffset,limit:20000}) : null;
    $('source-section').hidden = !result;
    if (!result) { sourceTotal = 0; return; }
    sourceTotal = result.total;
    $('source-meta').textContent = `${result.ref.kind==='article'?'Статья/новость':'Видео'}: ${result.ref.title} · версия ${result.ref.revision} · ${result.ref.sha256.slice(0,12)}…`;
    $('source-warning').textContent = result.changed ? `Источник уже имеет другую версию (${result.current_revision ?? 'нет данных'}). Здесь сохранён исходный снимок; сценарий не перезаписан.` : 'Сохранён неизменяемый снимок источника.';
    $('source-text').textContent = result.text || 'В исходной версии нет общего текста сценария. Выбранные сцены сохранены в раскадровке.';
  }
  async function readRecord(){
    const item=sourceRecords.find(x=>x.source_id===$('record').value);
    $('record-meta').textContent='';$('record-text').textContent='';recordTotal=0;if(!item)return;
    const origin={editor_supplied:'введён редактором',package_claimed:'заявлен в импортированном пакете'}[item.attribution==='package_claimed'?'package_claimed':item.provenance]||(item.task_id?'сохранённый результат задачи':'не уточнено');
    $('record-meta').textContent=[item.task_title||'Источник',item.origin_url||item.task_id||'', 'Происхождение: '+origin,'SHA256: '+item.sha256].filter(Boolean).join(' · ');
    const result=await core.call('content_video_source_read',{id:row.id,revision:row.revision,source_id:item.source_id,offset:recordOffset,limit:20000});
    recordTotal=result.total;$('record-text').textContent=result.text||'В источнике нет текста.';
  }
  async function records(){
    sourceRecords=[];sourceSegments.clear();recordOffset=0;recordTotal=0;$('record').replaceChildren();provenance.hidden=!row;
    if(!row)return;
    const result=await core.call('content_video_source_records',{id:row.id,revision:row.revision});sourceRecords=result.items;
    $('records-note').textContent=result.status==='legacy_unavailable'?'В старой видеозадаче состав первоисточников не фиксировался. Он не подставляется из текущей статьи.':result.status==='none'?'Видео создано самостоятельно; перенесённых источников нет.':sourceRecords.length?`Закреплено источников: ${sourceRecords.length}. Изменение статьи не меняет эти снимки. Метки времени в тексте не являются измеренным выравниванием.`:'В выбранной версии статьи не было прикреплённых первоисточников.';
    for(const [index,item] of sourceRecords.entries())$('record').append(new Option(`${index+1}. ${item.task_title||item.source_kind||'Источник'}`,item.source_id));
    await readRecord();
  }
  $('record').onchange=()=>act(async()=>{recordOffset=0;await readRecord();});
  $('record-prev').onclick=()=>act(async()=>{recordOffset=Math.max(0,recordOffset-20000);await readRecord();});
  $('record-next').onclick=()=>act(async()=>{recordOffset+=20000;await readRecord();});
  async function paint(value) {
    authorTargetStamp++;
    row = value; dirty = false; request = null; sourceOffset = 0;
    const defaults = {kind:'video',language:'ru',aspect:'16:9'};
    for (const [key,id] of Object.entries(fields)) $(id).value = value?.[key] ?? defaults[key] ?? '';
    authorCredit=value?.author_credit||null;paintCredit();
    $('meta').textContent = value ? `Версия ${value.revision} · ${value.id}${value.archived?' · Архив':''}` : 'Новая видеозадача';
    $('archive').textContent = value?.archived ? 'Восстановить видео' : 'В архив';
    for (const id of ['reload','archive','history-section']) $(id).hidden = !value;
    $('reel').hidden = !value || !value.scenes.length;
    if (value?.warnings?.length) message(value.warnings.join(' '));
    await source(); await records(); drawScenes(value?.scenes || []); await history();
    window.dispatchEvent(new CustomEvent('dewiar:video-selected',{detail:row}));
  }
  async function list() {
    const result = await core.call('content_video_list',{query:$('query').value,kind:$('filter').value,archived:$('archived').checked,offset,limit:50});
    total = result.total; $('count').textContent = 'Материалов: ' + total; $('list').replaceChildren();
    if (!result.items.length) { const p = document.createElement('p'); p.textContent = 'Видео по выбранному фильтру пока нет.'; $('list').append(p); }
    for (const item of result.items) {
      const button = document.createElement('button'); button.className = 'vd-row'; button.type = 'button';
      const name = document.createElement('strong'); name.textContent = item.title;
      const meta = document.createElement('span'); meta.textContent = `${item.kind==='reel'?'Рилс':'Видео'} · ${item.scene_count} сцен · ${seconds(item.duration_us)} с`;
      button.append(name,meta); button.onclick = () => act(async () => { if(await discard()) { await paint(await core.call('content_video_get',{id:item.id})); message(''); } }); $('list').append(button);
    }
  }
  $('form').onsubmit = e => { e.preventDefault(); act(async () => {
    const value = data(); let saved;
    if (row) saved = await core.call('content_video_update',{id:row.id,expected_revision:row.revision,patch:value});
    else { const signature = JSON.stringify(value); if(request?.signature !== signature) request = {signature,key:crypto.randomUUID()}; saved = await core.call('content_video_create',{data:value,request_key:request.key}); }
    await paint(saved); await list(); message('Сценарий и сцены сохранены.');
  }); };
  for (const id of Object.values(fields)) $(id).addEventListener('input',modified);
  $('kind').onchange = () => { if(!row) $('aspect').value = $('kind').value==='reel'?'9:16':'16:9'; };
  $('new').onclick = () => act(async () => { if(await discard()) { await paint(null); message(''); } });
  $('reload').onclick = () => act(async () => { if(await discard()) { await paint(await core.call('content_video_get',{id:row.id})); message('Сохранённая версия загружена.'); } });
  $('archive').onclick = () => act(async () => { if(!await discard())return; const saved = await core.call('content_video_archive',{id:row.id,expected_revision:row.revision,archived:!row.archived}); await paint(saved); await list(); message(saved.archived?'Видео в архиве. Его можно восстановить.':'Видео восстановлено.'); });
  $('add-scene').onclick = () => act(async () => { const current = scenes(); if(current.length >= 200)throw Error('Допустимо до 200 сцен.'); drawScenes([...current,{id:crypto.randomUUID(),title:'',narration:'',visual_prompt:'',duration_us:5000000,locked:false}],selectedScenes()); modified(); });
  $('reel').onclick = () => act(async () => {
    if(dirty)throw Error('Сначала сохраните сценарий и сцены.');
    const scene_ids = [...selectedScenes()]; if(!scene_ids.length)throw Error('Отметьте сцены, которые войдут в отдельный рилс.');
    const args = {source:{kind:'video',id:row.id,revision:row.revision,scene_ids}};
    const signature = JSON.stringify(args); if(request?.signature!==signature)request={signature,key:crypto.randomUUID()};
    const saved = await core.call('content_video_create',{...args,request_key:request.key}); await paint(saved); await list(); message('Создан самостоятельный рилс из выбранных сцен.');
  });
  $('history-view').onclick = () => act(async () => { const item=await core.call('content_video_revision_get',{id:row.id,revision:Number($('history').value)}); $('history-text').textContent=`${item.title}\n${item.brief}\n\n${item.script}\n\n`+item.scenes.map((s,i)=>`${i+1}. ${s.title} (${seconds(s.duration_us)} с)\n${s.narration}\nКадр: ${s.visual_prompt}`).join('\n\n'); });
  $('history-restore').onclick = () => act(async () => { if(!await discard())return; const saved = await core.call('content_video_restore',{id:row.id,expected_revision:row.revision,revision:Number($('history').value)}); await paint(saved); await list(); message('Выбранная версия восстановлена как новая.'); });
  $('history-more').onclick = () => act(() => history(false));
  $('source-prev').onclick = () => act(async () => { sourceOffset=Math.max(0,sourceOffset-20000); await source(); });
  $('source-next').onclick = () => act(async () => { sourceOffset+=20000; await source(); });
  for (const id of ['query','filter','archived']) $(id).onchange = () => act(async () => { offset=0; await list(); });
  $('prev').onclick = () => act(async () => { offset=Math.max(0,offset-50); await list(); });
  $('next').onclick = () => act(async () => { offset+=50; await list(); });
  window.contentVideoDirty=()=>dirty;
  window.addEventListener('beforeunload',e=>{if(dirty||window.contentVideoExchangeDirty?.()||window.contentVideoCaptionsDirty?.()||window.contentVideoReviewDirty?.()||window.contentVideoDistributionDirty?.()){e.preventDefault();e.returnValue='';}});
  document.addEventListener('click',e=>{const nav=e.target.closest('button[data-p]');if(nav&&nav.dataset.p!=='video'&&(dirty||window.contentVideoExchangeDirty?.()||window.contentVideoCaptionsDirty?.()||window.contentVideoReviewDirty?.()||window.contentVideoDistributionDirty?.())){e.preventDefault();e.stopImmediatePropagation();discard().then(ok=>{if(ok){dirty=false;window.contentVideoExchangeDiscard?.();window.contentVideoCaptionsDiscard?.();window.contentVideoReviewDiscard?.();window.contentVideoDistributionDiscard?.();nav.click();}});}},true);
  async function load() { await act(async () => { if(!$('site').dataset.loaded){const sites=await core.call('content_sites',{}); sites.forEach(s=>$('site').append(new Option(s.domain,s.id))); $('site').dataset.loaded='1';} if(!$('form').dataset.loaded){await paint(null);$('form').dataset.loaded='1';} await list(); }); }
  window.loadVideoPage=load; window.addEventListener('dewiar:studio-page',e=>{if(e.detail.page==='video')load();});
  const articleRoot = document.getElementById('content-app');
  if(articleRoot) {
    const handoff=document.createElement('section'); handoff.className='vd-handoff'; handoff.hidden=true;
    const button=document.createElement('button'); button.type='button'; button.className='btn'; button.textContent='Создать видеозадачу из этой статьи';
    const note=document.createElement('p'); note.textContent='В видео перейдут сохранённая версия текста и закреплённые первоисточники. Статья сохранится отдельно.';
    const status=document.createElement('p'); status.setAttribute('role','status'); handoff.append(button,note,status); articleRoot.append(handoff);
    let article=null, sourceRequest=null;
    window.addEventListener('dewiar:content-selected',e=>{article=e.detail;handoff.hidden=!article;button.disabled=!!article?.archived;status.textContent='';});
    button.onclick=async()=>{
      if(pending)return;
      if(window.contentHasUnsavedChanges?.()){status.textContent='Сначала сохраните изменения статьи и связанных форм.';return;}
      if(!article||article.archived)return;
      await act(async()=>{
        if(!await discard())return;
        const ref={kind:'article',id:article.id,revision:article.revision}; const signature=JSON.stringify(ref);
        if(sourceRequest?.signature!==signature)sourceRequest={signature,key:crypto.randomUUID()};
        const saved=await core.call('content_video_create',{source:ref,request_key:sourceRequest.key}); sourceRequest=null;
        if(!$('form').dataset.loaded){const sites=await core.call('content_sites',{});if(!$('site').dataset.loaded){sites.forEach(s=>$('site').append(new Option(s.domain,s.id)));$('site').dataset.loaded='1';}$('form').dataset.loaded='1';}
        await paint(saved); await list(); status.textContent='Видеозадача создана: '+saved.title;
        window.openContentTaskTab?.('video'); root.scrollIntoView({block:'start'}); message('Создана видеозадача из сохранённой статьи.');
      });
    };
  }
})();
