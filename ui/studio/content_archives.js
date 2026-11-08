/* Full local media archives; paths and progress, never binary Bridge payloads. */
(function(){
  'use strict';
  const editor=document.getElementById('content-app');if(!editor)return;
  const root=document.createElement('details');root.id='content-archives';root.className='ct-archives';
  root.innerHTML=`<summary>Резервная копия статей и видео</summary><p>Полный локальный ZIP: сохранённые статьи, история, каталог соцсетей и все импортированные видеорезультаты, в том числе больше 100 МиБ. Оригиналы внешнего монтажного проекта, модели, настройки и пароли не включаются. Архив не зашифрован.</p>
  <p id="ca-workspace"></p><label>Папка для архива или восстановленной копии<input id="ca-destination" placeholder="Полный путь к существующей папке" autocomplete="off"></label><button class="btn" id="ca-folder" type="button">Выбрать папку</button>
  <button class="btn primary" id="ca-create" type="button">Создать полный архив</button>
  <label>Файл полного архива ZIP<input id="ca-path" placeholder="Полный путь к сохранённому архиву" autocomplete="off"></label><button class="btn" id="ca-file" type="button">Выбрать архив</button><button class="btn" id="ca-restore" type="button">Восстановить в отдельную папку</button>
  <p>Копируются только сохранённые версии. Восстановление оставляет текущую рабочую папку и открытые редакторы без изменений. Отправки в восстановленной копии остановлены; аккаунты потребуют повторного входа.</p>
  <p id="ca-status" role="status" aria-live="polite"></p><button class="btn" id="ca-refresh" type="button">Обновить журнал архивов</button><div id="ca-jobs" aria-label="Операции с архивами"></div>`;
  editor.before(root);const $=id=>root.querySelector('#ca-'+id);
  const names={running:'Выполняется',cancel_requested:'Отмена запрошена',completed:'Готово',failed:'Ошибка',canceled:'Отменено',interrupted:'Прервано'};
  let timer=null,busy=false,loading=false,request=null;
  const mib=n=>(n/1048576).toLocaleString('ru-RU',{maximumFractionDigits:1})+' МиБ';
  function message(text,error=false){$('status').textContent=text;$('status').classList.toggle('ct-error',error);}
  async function act(fn){if(busy)return;busy=true;try{await fn();}catch(e){message(e.message,true);}finally{busy=false;}}
  function render(rows){
    const fragment=document.createDocumentFragment();
    for(const row of rows){
      const card=document.createElement('article');card.className='ca-job';
      const title=document.createElement('h3');title.textContent=(row.kind==='create'?'Создание архива':'Восстановление')+' · '+(names[row.state]||row.state);
      const date=document.createElement('p');date.textContent=new Date(row.created_at).toLocaleString('ru-RU');
      const status=document.createElement('p');status.textContent=row.message;
      card.append(title,date,status);
      if(row.total_bytes){const meter=document.createElement('progress');meter.max=row.total_bytes;meter.value=row.done_bytes;meter.setAttribute('aria-label','Прогресс '+row.id);const count=document.createElement('p');count.textContent=mib(row.done_bytes)+' из '+mib(row.total_bytes);card.append(meter,count);}
      if(row.result){const path=document.createElement('code');path.textContent=row.result.path||row.result.workspace;card.append(path);const count=document.createElement('p');count.textContent='Проверено медиафайлов: '+row.result.media_count;card.append(count);if(row.result.path){const use=document.createElement('button');use.type='button';use.className='btn';use.textContent='Выбрать для восстановления';use.onclick=()=>{$('path').value=row.result.path;message('Архив выбран. Укажите папку для восстановленной копии.');};card.append(use);}}
      if(['running','cancel_requested'].includes(row.state)){const button=document.createElement('button');button.type='button';button.className='btn';button.textContent='Отменить операцию';button.disabled=row.state==='cancel_requested';button.onclick=()=>act(async()=>{const result=await core.call('content_archive_cancel',{id:row.id});message(result.state==='cancel_requested'?'Отмена запрошена. Дождитесь завершения текущего шага.':result.message);await load();});card.append(button);}
      fragment.append(card);
    }
    if(!rows.length){const empty=document.createElement('p');empty.textContent='Операций с архивами пока нет.';fragment.append(empty);}
    $('jobs').replaceChildren(fragment);
  }
  async function load(){
    if(loading)return;loading=true;clearTimeout(timer);
    try{const result=await core.call('content_archive_jobs',{});$('workspace').textContent='Текущая рабочая папка: '+result.workspace;render(result.items);if(result.items.some(r=>['running','cancel_requested'].includes(r.state)))timer=setTimeout(()=>load().catch(e=>message(e.message,true)),1000);}
    finally{loading=false;}
  }
  async function start(kind){
    const input={kind,destination:$('destination').value.trim(),...(kind==='restore'?{path:$('path').value.trim()}:{})};
    if(!input.destination)throw Error('Укажите существующую папку для результата.');
    if(kind==='restore'&&!input.path)throw Error('Выберите файл полного архива.');
    const signature=JSON.stringify(input);if(request?.signature!==signature)request={signature,key:crypto.randomUUID()};
    const row=await core.call('content_archive_start',{...input,request_key:request.key});request=null;message(row.message);await load();
  }
  $('create').onclick=()=>act(()=>start('create'));$('restore').onclick=()=>act(()=>start('restore'));$('refresh').onclick=()=>act(async()=>{await load();message('Журнал обновлён.');});
  $('folder').onclick=()=>act(async()=>{const api=window.pywebview?.api;if(!api?.pick_folder)throw Error('В браузере введите полный путь. Выбор папки доступен в установленной Студии.');const path=await api.pick_folder();if(path)$('destination').value=path;});
  $('file').onclick=()=>act(async()=>{const api=window.pywebview?.api;if(!api?.pick_file)throw Error('В браузере введите полный путь. Выбор файла доступен в установленной Студии.');const path=await api.pick_file('Архив контента (*.zip)');if(path)$('path').value=path;});
  root.addEventListener('toggle',()=>{if(root.open)load().catch(e=>message(e.message,true));});
  window.addEventListener('beforeunload',()=>clearTimeout(timer));
})();
