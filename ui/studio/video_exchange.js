(function(){
  'use strict';
  const root=document.getElementById('video-app');if(!root)return;
  const section=document.createElement('section');section.className='vd-exchange';section.hidden=true;
  section.innerHTML='<h2>Монтаж и готовые файлы</h2><p>Передайте сценарий и сцены в OpenMontage, затем примите готовый файл. Проект монтажа остаётся в OpenMontage.</p><button type="button" class="btn" id="vx-export">Скачать пакет для OpenMontage</button><p id="vx-message" role="status" aria-live="polite"></p><label>Пакет, по которому смонтирован файл<select id="vx-handoff"></select></label><label>Локальный видеофайл<input id="vx-path" maxlength="4096" placeholder="/полный/путь/к/видео.mp4"></label><p class="vd-note">MP4, MOV, WebM или MKV, до 4 ГиБ. Dewiar сохранит копию и проверит декодирование. Содержимое и звук нужно проверить при просмотре.</p><div class="vd-actions"><button type="button" class="btn" id="vx-pick">Выбрать файл…</button><button type="button" class="btn" id="vx-import">Принять и проверить файл</button><button type="button" class="btn" id="vx-refresh">Обновить результаты</button></div><div id="vx-jobs"></div>';
  root.append(section);
  const $=s=>document.getElementById('vx-'+s);let row=null,pending=false,dirty=false,request=null,timer=null,generation=0;
  function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('vd-error',error);}
  window.contentVideoExchangeDirty=()=>dirty;
  window.contentVideoExchangeBusy=()=>pending;
  window.contentVideoExchangeDiscard=()=>{dirty=false;$('path').value='';};
  function locked(){section.querySelectorAll('button,input,select').forEach(e=>e.disabled=pending);$('export').disabled=pending||!!row?.archived;$('import').disabled=pending||!!row?.archived||!$('handoff').options.length;}
  async function act(fn){if(pending)return;pending=true;locked();try{await fn();}catch(e){message(e.message,true);}finally{pending=false;locked();}}
  function download(pack){const raw=Uint8Array.from(atob(pack.base64),c=>c.charCodeAt(0));const url=URL.createObjectURL(new Blob([raw],{type:pack.mime}));const a=document.createElement('a');a.href=url;a.download=pack.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
  async function loadHandoffs(){const selected=$('handoff').value;const list=await core.call('content_video_handoffs',{id:row.id});$('handoff').replaceChildren();list.forEach(item=>$('handoff').append(new Option(`Версия ${item.video_revision} · ${item.hash.slice(0,16)}…`,item.hash)));if([...$('handoff').options].some(o=>o.value===selected))$('handoff').value=selected;}
  async function jobs(){
    if(!row)return;const current=generation;const list=await core.call('content_video_import_list',{id:row.id});if(current!==generation)return;
    $('jobs').replaceChildren();const labels={running:'Проверяется',imported:'Файл принят',failed:'Ошибка проверки',interrupted:'Проверка прервана',canceled:'Отменено'};
    for(const job of list){const card=document.createElement('div');card.className='vd-scene';const title=document.createElement('h3');title.textContent=`${labels[job.state]||job.state} · версия видео ${job.video_revision}`;card.append(title);
      const note=document.createElement('p');note.textContent=job.error||(job.stale?'Сценарий изменён. Файл закреплён за прежней версией.':job.state==='imported'?'Декодирование проверено. Решение о содержании и звуке находится в редакционной приёмке файла.':'');card.append(note);
      if(job.receipt){const info=document.createElement('p');const stream=job.receipt.streams?.find(s=>s.codec_type==='video');info.textContent=`${stream?.width||'?'} × ${stream?.height||'?'} · ${(job.receipt.duration_us/1000000).toFixed(3)} с · ${(job.receipt.bytes/1048576).toFixed(2)} МиБ · SHA256 ${job.receipt.sha256}`;info.style.overflowWrap='anywhere';card.append(info);
        const b=document.createElement('button');b.type='button';b.className='btn';b.textContent='Подготовить просмотр';const link=document.createElement('a');link.hidden=true;link.target='_blank';link.rel='noopener';link.textContent='Открыть видеоплеер';link.className='btn';
        b.onclick=()=>act(async()=>{const result=await core.call('content_video_preview',{id:job.id});const url=new URL(result.url);if(url.protocol!=='http:'||url.hostname!=='127.0.0.1')throw Error('Некорректный адрес локального просмотра');link.href=result.url;link.hidden=false;message('Просмотр подготовлен. Откройте видеоплеер по ссылке.');});card.append(b,link);
        const captions=document.createElement('button');captions.type='button';captions.className='btn';captions.textContent='Редактировать субтитры файла';captions.onclick=()=>window.dispatchEvent(new CustomEvent('dewiar:caption-open',{detail:job}));card.append(captions);
        const review=document.createElement('button');review.type='button';review.className='btn';review.textContent='Редакционная приёмка файла';review.onclick=()=>window.dispatchEvent(new CustomEvent('dewiar:video-review-open',{detail:job}));card.append(review);
      }
      if(job.state==='running'){const b=document.createElement('button');b.type='button';b.className='btn';b.textContent='Отменить проверку файла';b.onclick=()=>act(async()=>{await core.call('content_video_import_cancel',{id:job.id});await jobs();});card.append(b);}
      $('jobs').append(card);
    }
    if(!list.length){const p=document.createElement('p');p.textContent='Готовые видеофайлы ещё не приняты.';$('jobs').append(p);}
    clearTimeout(timer);if(list.some(job=>job.state==='running'))timer=setTimeout(()=>{if(current===generation)jobs().catch(e=>message(e.message,true));},1000);
  }
  $('export').onclick=()=>act(async()=>{if(window.contentVideoDirty?.())throw Error('Сначала сохраните сценарий и сцены.');const pack=await core.call('content_video_handoff',{id:row.id,revision:row.revision});download(pack);await loadHandoffs();$('handoff').value=pack.hash;message('Пакет скачан. Это материалы для монтажа; видеоролик ещё не создан.');});
  $('path').oninput=()=>{dirty=!!$('path').value;request=null;};
  $('handoff').onchange=()=>{request=null;};
  $('pick').onclick=()=>act(async()=>{const api=window.pywebview?.api;if(!api?.pick_file){message('В браузере укажите полный локальный путь. Диалог выбора доступен в окне Dewiar.');return;}const path=await api.pick_file('Видео (*.mp4;*.mov;*.webm;*.mkv)');if(path){$('path').value=path;dirty=true;request=null;}});
  $('import').onclick=()=>act(async()=>{if(window.contentVideoDirty?.())throw Error('Сначала сохраните изменения сценария.');const args={path:$('path').value,handoff_hash:$('handoff').value};const signature=JSON.stringify(args);if(request?.signature!==signature)request={signature,key:crypto.randomUUID()};await core.call('content_video_import',{...args,request_key:request.key});dirty=false;$('path').value='';request=null;message('Файл копируется и проверяется. Оригинал остаётся на месте.');await jobs();});
  $('refresh').onclick=()=>act(async()=>{await loadHandoffs();await jobs();});
  window.addEventListener('dewiar:video-selected',e=>{const value=e.detail;const changed=row?.id!==value?.id;row=value;section.hidden=!row;generation++;clearTimeout(timer);if(changed){dirty=false;request=null;$('path').value='';$('handoff').replaceChildren();message('');}if(row)act(async()=>{await loadHandoffs();await jobs();});});
})();
