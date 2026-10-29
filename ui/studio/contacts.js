/* Local Contacts UI. Permission requests are native and only from the explicit button. */
(() => {
  const $c=s=>document.querySelector(s),section=$c('#p-contacts');if(!section)return;
  let lastDraftId=null,preview=null,selected=new Set(),shown=50,permissionTimer=null,pending=null,busy=false,localOffset=0,requestId=makeRequestId(),lastPermissionPopup='';
  function makeRequestId(){return globalThis.crypto?.randomUUID?.()||'contact_'+Date.now()+'_'+Math.random().toString(36).slice(2);}
  function message(text,error=false,opts={}){
    const el=$c('#ct-status');el.textContent=text;el.classList.toggle('bad',error);
    if((opts.popup||error&&opts.popup!==false||String(text).length>110)&&typeof window.messagePopup==='function')window.messagePopup(text,{title:opts.title||(error?'Ошибка в Контактах':'Контакты Dewiar'),steps:opts.steps,note:opts.note}).catch(()=>{});
  }
  async function operation(fn){if(busy){message('Предыдущее действие с контактами ещё выполняется. Дождитесь результата.');return;}busy=true;section.setAttribute('aria-busy','true');try{return await fn();}catch(e){message(e.message||String(e),true);}finally{busy=false;section.removeAttribute('aria-busy');}}
  const fullName=r=>[r.family_name,r.given_name,r.middle_name].filter(Boolean).join(' ')||r.organization||'Без имени';
  const details=r=>[...(r.phones||[]),...(r.emails||[]),r.organization,r.birthday?`День рождения: ${String(r.birthday.day).padStart(2,'0')}.${String(r.birthday.month).padStart(2,'0')}${r.birthday.year?'.'+r.birthday.year:''}`:''].filter(Boolean).join('\n');
  function setPermission(s){
    const labels={authorized:'Разрешён полный доступ.',limited:'Разрешён доступ только к выбранным в macOS контактам. «Все» означает все доступные Dewiar карточки.',denied:'Доступ запрещён. Включите Dewiar в системных настройках Контактов.',restricted:'Доступ ограничен системой.',not_determined:'Доступ ещё не запрашивался.',unavailable:'Чтение системной адресной книги доступно только на Mac.'};
    const text=s.pending?'Ожидаю ответ в системном окне macOS…':s.requested&&!s.pending&&s.state==='not_determined'?'Запрос уже отправлен, но macOS не подтвердила доступ. Проверьте системные настройки и нажмите «Проверить доступ».':s.request_supported===false?'Запуск из исходников: для выдачи доступа Dewiar откройте Dewiar.app.':labels[s.state]||'Не удалось определить доступ.';
    const failed=s.state==='denied'||s.state==='restricted'||(s.requested&&!s.pending&&s.state==='not_determined');
    $c('#ct-permission').textContent=text;$c('#ct-permission').classList.toggle('bad',failed);
    const signature=`${s.state}|${!!s.requested}|${!!s.pending}`;
    if(failed&&signature!==lastPermissionPopup){
      lastPermissionPopup=signature;
      const steps=s.state==='restricted'?
        ['Откройте «Настройки macOS».','Перейдите в «Конфиденциальность и безопасность» → «Контакты».','Если macOS не даёт изменить переключатель, проверьте ограничения Screen Time/корпоративного профиля.','Вернитесь в Dewiar и нажмите «Проверить доступ».']:
        ['Нажмите «Настройки macOS» в этом разделе.','Откройте «Конфиденциальность и безопасность» → «Контакты».','Включите Dewiar. Если доступ уже включён, выключите и включите его один раз.','Вернитесь в Dewiar и нажмите «Проверить доступ».','После статуса «Разрешён полный доступ» нажмите «Прочитать доступные контакты».'];
      if(typeof window.messagePopup==='function')window.messagePopup(text,{title:'Нужен доступ к Контактам macOS',steps,note:'Пароли и содержимое Контактов не отправляются модели автоматически. Dewiar читает только имя, телефоны, почту, организацию и дату рождения.'}).catch(()=>{});
    }
    if(!failed)lastPermissionPopup='';
    const request=$c('#ct-permit');request.disabled=!!s.pending;request.hidden=s.request_supported===false||s.state!=='not_determined'||!!s.requested;
    $c('#ct-read').disabled=!s.granted;$c('#ct-native-settings').hidden=s.state==='unavailable';
    clearTimeout(permissionTimer);if(s.pending)permissionTimer=setTimeout(refreshPermission,1000);
  }
  async function refreshPermission(announce=false){
    if(announce)message('Проверяю разрешение macOS на доступ к Контактам…');
    const a=window.api?.();let value;
    try{
      if(a?.contacts_permission)value=await a.contacts_permission();
      else value=(await core.call('contacts_state')).permission;
    }catch(nativeError){
      // The native bridge can be unavailable for a moment while a pywebview window
      // is becoming ready. Fall back to the core's read-only status instead of
      // making the button appear inert.
      if(!a?.contacts_permission)throw nativeError;
      try{value=(await core.call('contacts_state')).permission;}catch(_){throw nativeError;}
    }
    setPermission(value);
    if(announce){
      const text=value?.granted
        ? (value.limited?'Доступ к Контактам macOS подтверждён (только выбранные карточки).':'Доступ к Контактам macOS подтверждён.')
        : 'Доступ к Контактам macOS пока не подтверждён. Откройте «Настройки macOS», включите Dewiar и проверьте ещё раз.';
      message(text,!value?.granted,{popup:true,title:'Проверка доступа к Контактам'});
    }
    return value;
  }
  async function load(){
    try{const state=await core.call('contacts_state');$c('#ct-count').textContent=`В Dewiar: ${state.count}`;$c('#ct-suggest').checked=state.suggest;await refreshPermission();await loadLocal();await loadProposal();}
    catch(e){message(e.message,true);}
  }
  async function loadLocal(){
    const data=await core.call('contacts_list',{query:$c('#ct-local-search').value,offset:localOffset});const list=$c('#ct-local-list');list.replaceChildren();
    $c('#ct-local-total').textContent=`Найдено: ${data.total}. Показано: ${data.items.length?localOffset+1:0}–${localOffset+data.items.length}.`;$c('#ct-local-prev').disabled=localOffset===0;$c('#ct-local-next').disabled=localOffset+data.items.length>=data.total;
    for(const row of data.items){const box=document.createElement('details');box.className='ct-item';const summary=document.createElement('summary');summary.textContent=row.display_name||fullName(row);summary.title='Открыть локальную карточку: телефоны, почта и сведения. Данные не отправляются модели автоматически.';box.append(summary);const text=document.createElement('p');text.style.whiteSpace='pre-line';text.textContent=details(row)||'Только имя';box.append(text);const remove=document.createElement('button');remove.className='btn small danger';remove.textContent='Удалить копию из Dewiar';remove.title='Удалить только локальную копию. Системная карточка в Контактах macOS останется.';remove.onclick=()=>operation(async()=>{if(!await ask(`Удалить локальную копию «${row.display_name}» из Dewiar? Карточка macOS останется.`,{ok:'Удалить копию'}))return;await core.call('contacts_delete',{id:row.id,confirmed:true});await load();message('Локальная копия удалена; Контакты macOS не изменены.');});box.append(remove);list.append(box);}
  }
  function renderPreview(){
    if(!preview)return;
    const query=$c('#ct-source-search').value.toLocaleLowerCase('ru'),rows=preview.contacts.filter(r=>(fullName(r)+' '+details(r)).toLocaleLowerCase('ru').includes(query));
    const list=$c('#ct-source-list');list.replaceChildren();
    for(const r of rows.slice(0,shown)){const label=document.createElement('label');label.className='ct-item ct-choice';const check=document.createElement('input');check.type='checkbox';check.checked=selected.has(r.source_id);check.title='Включить эту карточку в выбранный импорт. Само выделение ничего не сохраняет.';check.onchange=()=>{check.checked?selected.add(r.source_id):selected.delete(r.source_id);updateSelected();};const content=document.createElement('span');const title=document.createElement('b');title.textContent=fullName(r);const text=document.createElement('span');text.className='hint';text.style.whiteSpace='pre-line';text.textContent=details(r)||'Только имя';content.append(title,text);label.append(check,content);list.append(label);}
    $c('#ct-more').hidden=rows.length<=shown;$c('#ct-source-total').textContent=`Доступно: ${preview.count}. Найдено фильтром: ${rows.length}.${preview.limited?' Системный доступ ограничен выбранными контактами.':''}`;updateSelected();
  }
  function updateSelected(){$c('#ct-import').textContent=`Импортировать выбранные (${selected.size})`;$c('#ct-import').disabled=!selected.size;}
  function openForm(draft={}){
    requestId=makeRequestId();$c('#ct-form').hidden=false;
    for(const key of ['given_name','family_name','middle_name','organization'])$c(`[data-ct="${key}"]`).value=draft[key]||'';
    $c('[data-ct=phones]').value='';$c('[data-ct=emails]').value='';$c('[data-ct=birthday]').value='';$c('#ct-to-mac').checked=true;
    $c('#ct-form').scrollIntoView({block:'nearest'});$c('[data-ct=family_name]').focus();
  }
  async function loadProposal(){
    pending=await core.call('contacts_pending');const box=$c('#ct-proposal');box.replaceChildren();box.hidden=!pending;if(!pending)return;
    if(pending.draft && lastDraftId!==pending.id){lastDraftId=pending.id;openForm(pending.draft);}
    const title=document.createElement('h2');title.textContent=`Контакт: ${pending.name}`;box.append(title);
    const note=document.createElement('p');note.textContent=pending.searching?'Поиск совпадений в Контактах macOS выполняется…':pending.local.length?'Найдена локальная карточка. Откройте её в списке Dewiar; повторный импорт не требуется.':pending.mac.length?`Найдены карточки macOS: ${pending.mac.length}. Просмотрите данные перед копированием.`:'Локального совпадения нет. Создать контакт? Для поиска в адресной книге Mac нужен системный доступ.';box.append(note);
    for(const match of pending.local){const button=document.createElement('button');button.className='btn';button.textContent='Открыть «'+match.name+'»';button.onclick=()=>operation(async()=>{const row=await core.call('contacts_get',{id:match.id});const text=document.createElement('p');text.style.whiteSpace='pre-line';text.textContent=fullName(row)+'\n'+details(row);button.replaceWith(text);});box.append(button);}
    if(pending.lookup_error){const warning=document.createElement('p');warning.textContent='Поиск macOS завершился ошибкой; отсутствие результата не означает отсутствие контакта.';box.append(warning);}
    if(pending.mac.length){const rows=await core.call('contacts_proposal_preview',{id:pending.id});for(const row of rows){const card=document.createElement('div');card.className='ct-item';const text=document.createElement('p');text.style.whiteSpace='pre-line';text.textContent=fullName(row)+'\n'+details(row);const button=document.createElement('button');button.className='btn';button.textContent='Скопировать эту карточку в Dewiar';button.onclick=()=>operation(async()=>{if(!await ask(`Скопировать карточку «${fullName(row)}» в Dewiar?\n${details(row)}`,{ok:'Скопировать'}))return;const r=await core.call('contacts_proposal_action',{id:pending.id,action:'copy',source_id:row.source_id});await load();message(`Добавлено: ${r.added}; уже было: ${r.skipped}.`);});card.append(text,button);box.append(card);}}
    const actions=document.createElement('div');actions.className='actions';const draft=document.createElement('button');draft.className='btn';draft.textContent='Создать новую карточку';draft.disabled=!!pending.searching;draft.onclick=()=>operation(async()=>{const r=await core.call('contacts_proposal_action',{id:pending.id,action:'draft'});lastDraftId=pending.id;openForm(r.draft);});const dismiss=document.createElement('button');dismiss.className='btn ghost';dismiss.textContent='Не предлагать этого человека в этой беседе';dismiss.onclick=()=>operation(async()=>{await core.call('contacts_proposal_action',{id:pending.id,action:'dismiss'});await loadProposal();});actions.append(draft,dismiss);box.append(actions);
  }
  $c('#ct-permit').onclick=()=>operation(async()=>{const a=window.api?.();if(!a?.request_contacts_access)throw Error('Откройте Dewiar.app на Mac');setPermission(await a.request_contacts_access());});
  $c('#ct-native-settings').onclick=()=>operation(async()=>{await window.api().open_contacts_settings();});
  $c('#ct-permission-refresh').onclick=()=>operation(()=>refreshPermission(true));
  $c('#ct-read').onclick=()=>operation(async()=>{message('Читаю доступные Контакты macOS…');preview=await core.call('contacts_prepare_import',{},120000);selected=new Set();shown=50;$c('#ct-preview').hidden=false;renderPreview();message('Выберите все или отдельные карточки, затем подтвердите импорт.');});
  $c('#ct-select-all').onclick=()=>{if(preview){selected=new Set(preview.contacts.map(r=>r.source_id));renderPreview();}};
  $c('#ct-select-none').onclick=()=>{selected.clear();renderPreview();};
  $c('#ct-more').onclick=()=>{shown+=50;renderPreview();};$c('#ct-source-search').oninput=()=>{shown=50;renderPreview();};
  $c('#ct-import').onclick=()=>operation(async()=>{if(!preview||!selected.size)return;const update=$c('#ct-update').checked;if(!await ask(`Импортировать ${selected.size} карточек в Dewiar?\n${update?'Совпавшие локальные копии будут обновлены из macOS.':'Существующие локальные копии не будут перезаписаны.'}\nКонтакты macOS не изменятся.`,{ok:'Импортировать'}))return;const r=await core.call('contacts_import',{token:preview.token,ids:[...selected],update});await load();message(`Добавлено: ${r.added}, обновлено: ${r.updated}, пропущено: ${r.skipped}.`);});
  let timer;$c('#ct-local-search').oninput=()=>{clearTimeout(timer);timer=setTimeout(()=>{localOffset=0;operation(loadLocal);},180);};
  $c('#ct-local-prev').onclick=()=>operation(async()=>{localOffset=Math.max(0,localOffset-100);await loadLocal();});
  $c('#ct-local-next').onclick=()=>operation(async()=>{localOffset+=100;await loadLocal();});
  $c('#ct-suggest').onchange=e=>operation(async()=>{try{await core.call('contacts_suggest_set',{enabled:e.target.checked});}catch(error){e.target.checked=!e.target.checked;throw error;}});
  $c('#ct-new').onclick=()=>openForm();$c('#ct-form-cancel').onclick=()=>{$c('#ct-form').hidden=true;};
  $c('#ct-form').onsubmit=e=>{e.preventDefault();return operation(async()=>{
    const row={};for(const key of ['given_name','family_name','middle_name','organization'])row[key]=$c(`[data-ct="${key}"]`).value.trim();
    for(const key of ['phones','emails'])row[key]=$c(`[data-ct="${key}"]`).value.split(/[\n;,]+/).map(x=>x.trim()).filter(Boolean);
    const birth=$c('[data-ct=birthday]').value;if(birth){const [year,month,day]=birth.split('-').map(Number);row.birthday={year,month,day};}
    if(!row.given_name&&!row.family_name&&!row.organization)throw Error('Укажите имя, фамилию или организацию');
    const toMac=$c('#ct-to-mac').checked,where=toMac?'в Dewiar и Контактах macOS':'только в Dewiar';
    if(!await ask(`Создать контакт «${fullName(row)}» ${where}?\n${details(row)}${toMac?'\nСистемные Контакты могут синхронизировать его через ваши учётные записи.':''}`,{ok:toMac?'Создать в обоих приложениях':'Создать в Dewiar'}))return;
    const result=await core.call('contacts_create',{contact:row,to_mac:toMac,request_id:requestId,confirmed:true});$c('#ct-form').hidden=true;requestId=makeRequestId();await load();message(result.already_exists?'Такая карточка уже есть в Dewiar; дубликат не создавался.':result.saved_mac?'Карточка сохранена в Dewiar и Контактах macOS.':'Карточка сохранена только в Dewiar.');
  });};
  document.querySelector('nav button[data-p=contacts]').addEventListener('click',load);
  core.on('hello',()=>{if(section.classList.contains('cur'))load();});
  core.on('contacts_changed',()=>{if(section.classList.contains('cur'))load();});
  core.on('contact_show',()=>{loadProposal().catch(e=>message(e.message,true));});
  core.on('contact_suggestion',()=>{if(section.classList.contains('cur'))loadProposal().catch(e=>message(e.message,true));});
})();
