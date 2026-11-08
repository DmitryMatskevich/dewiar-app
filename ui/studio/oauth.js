/* Connection management; credentials never enter the browser. */
(function(){
  const root=document.getElementById('video-app');if(!root)return;
  const panel=document.createElement('details');panel.id='hf-oauth';panel.className='vd-higgsfield';
  panel.innerHTML=`<summary>Аккаунты Higgsfield</summary>
    <p>Вход используется для чтения доступных инструментов. Генерация подключается отдельным этапом.</p>
    <p>После переноса рабочей папки или восстановления копии может потребоваться новый вход. Сохранённый доступ привязан к этому компьютеру и рабочей папке.</p>
    <p id="ho-runtime" role="status"></p><div class="vd-actions"><button class="btn" id="ho-check">Проверить модуль входа</button><button class="btn" id="ho-install" hidden>Установить модуль входа</button><button class="btn" id="ho-install-cancel" hidden>Отменить установку</button></div>
    <p id="ho-message" role="status" aria-live="polite"></p>
    <div class="vd-actions"><label>Подключение<select id="ho-select"><option value="">Новое подключение</option></select></label>
    <label class="ho-archive-toggle"><input type="checkbox" id="ho-archived"> Архив подключений</label><button class="btn" id="ho-new">Новое подключение</button><button class="btn" id="ho-reload">Загрузить заново</button></div>
    <label>Название подключения<input id="ho-label" maxlength="120" placeholder="Например, личный аккаунт или компания"></label>
    <div class="vd-actions"><button class="btn" id="ho-save">Сохранить подключение</button><button class="btn" id="ho-archive" hidden>В архив</button></div>
    <p id="ho-state"></p><p id="ho-detail"></p>
    <div class="vd-actions"><button class="btn" id="ho-login" hidden>Войти в Higgsfield</button>
    <a class="btn" id="ho-open" hidden target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer">Открыть вход Higgsfield</a>
    <button class="btn" id="ho-cancel" hidden>Отменить вход</button><button class="btn" id="ho-verify" hidden>Проверить подключение</button>
    <button class="btn" id="ho-disconnect" hidden>Отключить локально</button></div>
    <p id="ho-identity" hidden>Название задано вами. Личность владельца аккаунта этим соединением ещё не подтверждена.</p>
    <p id="ho-revoke" hidden>Локальное отключение удаляет доступ из Dewiar. Оно не отзывает разрешение у Higgsfield; его можно убрать в настройках самого сервиса.</p>
    <details><summary>Последние события подключения</summary><ul id="ho-history"></ul></details>`;
  root.append(panel);
  const $=id=>document.getElementById('ho-'+id);
  let row=null,runtime=null,dirty=false,busy=false,timer=null,runtimeTimer=null,sequence=0,request=null;
  const names={disconnected:'Вход не выполнен',authenticating:'Ожидание входа в браузере',verifying:'Проверка подключения',connected:'Подключено',needs_login:'Требуется новый вход'};
  const events={authorization_denied:'Вход отклонён на странице провайдера',created:'Подключение создано',login_started:'Вход начат',verification_started:'Проверка начата',connected:'Подключение проверено',disconnected:'Доступ отключён локально',cancelled:'Операция отменена',timeout:'Время ожидания истекло',interrupted:'Операция прервана',operation_failed:'Не удалось завершить операцию',runtime_unavailable:'Модуль входа недоступен',credential_unavailable:'Сохранённый доступ недоступен',protocol_error:'Ответ подключения не прошёл проверку'};
  function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('vd-error',error);}
  async function discard(){return !dirty||await window.contentConfirm('Название подключения не сохранено. Перейти без сохранения?');}
  function locks(){
    const running=row&&['authenticating','verifying'].includes(row.state);
    for(const id of ['select','archived','new','reload','save','check'])$(id).disabled=busy;
    $('archive').disabled=busy||running||row?.state==='connected';
    $('label').disabled=busy||running||!!row?.archived;
    $('save').disabled=busy||running||!!row?.archived;
    $('login').disabled=busy||!runtime?.ready||dirty;
    $('verify').disabled=busy||!runtime?.ready||dirty;
    $('cancel').disabled=busy||!row?.intent_id;
    $('disconnect').disabled=busy||dirty;
    $('check').disabled=busy||runtime?.installing;
    $('install').disabled=busy||runtime?.installing;
    $('install-cancel').disabled=busy;
  }
  function paintRuntime(value){
    const wasInstalling=runtime?.installing;runtime=value;
    const phases={preparing:'Подготавливается отдельный модуль MCP…',installing:'Устанавливаются зависимости MCP…',checking:'Проверяется установленный модуль…',ready:'Отдельный модуль MCP готов.',cancelled:'Установка отменена. Её можно повторить.',failed:'Не удалось установить модуль.'};
    const errors={foreign_runtime:'Папка занята другим окружением; её файлы не изменены.',installation_busy:'Установка уже выполняется другим экземпляром.',timeout:'Истекло время ожидания.',runtime_unavailable:'Нужен установленный Python 3.12.',installation_failed:'Проверьте доступность PyPI и свободное место.'};
    $('runtime').textContent=(runtime.test_only?'ТЕСТ — имитация провайдера. ':'')+(phases[runtime.install_state]||runtime.message)+(runtime.install_error?' '+(errors[runtime.install_error]||''): '');
    $('install').hidden=!runtime.can_install||runtime.ready||runtime.installing;$('install-cancel').hidden=!runtime.installing;
    if(wasInstalling&&!runtime.installing)message(runtime.ready?'Модуль установлен и проверен.':phases[runtime.install_state]||'Установка не завершена.',!runtime.ready);
    locks();clearTimeout(runtimeTimer);if(panel.open&&runtime.installing)runtimeTimer=setTimeout(()=>runtimeRefresh().catch(e=>message(e.message,true)),1000);
  }
  async function runtimeRefresh(){paintRuntime(await core.call('content_oauth_runtime',{}));}
  function paint(value,reset=false){
    row=value;if(reset){dirty=false;request=null;$('label').value=row?.label||'';}
    const running=row&&['authenticating','verifying'].includes(row.state);
    $('state').textContent=row?(names[row.state]||row.state)+(row.archived?' · архив':''):'Новая запись подключения';
    const meta=row?.metadata||{};
    $('detail').textContent=[row?.last_error?events[row.last_error]||'Не удалось завершить операцию':'',
      meta.tool_count!==undefined?'Доступно инструментов: '+meta.tool_count:'',meta.checked_at?'Проверено: '+new Date(meta.checked_at).toLocaleString('ru-RU'):'',
      meta.expires_at?'Срок токена: '+new Date(meta.expires_at*1000).toLocaleString('ru-RU'):''].filter(Boolean).join(' · ');
    $('archive').hidden=!row;$('archive').textContent=row?.archived?'Восстановить подключение':'В архив';$('archive').disabled=busy||running||row?.state==='connected';
    $('login').hidden=!row||row.archived||running||row.state==='connected';
    $('verify').hidden=!row||row.archived||row.state!=='connected';
    $('cancel').hidden=!running;$('cancel').textContent=row?.state==='verifying'?'Отменить проверку':'Отменить вход';
    $('disconnect').hidden=!row||row.state==='disconnected';
    $('identity').hidden=!row||row.state!=='connected';$('revoke').hidden=!row;
    $('open').hidden=!row?.authorization_url;
    if(row?.authorization_url){$('open').href=row.authorization_url;$('open').textContent=runtime?.test_only?'ТЕСТ — открыть имитацию входа':'Открыть вход Higgsfield';}
    else $('open').removeAttribute('href');
    $('history').replaceChildren();for(const event of row?.events||[]){const li=document.createElement('li');li.textContent=new Date(event.created_at).toLocaleString('ru-RU')+' · '+(events[event.event]||'Состояние обновлено');$('history').append(li);}
    locks();clearTimeout(timer);if(panel.open&&running)timer=setTimeout(()=>poll().catch(e=>message(e.message,true)),800);
  }
  async function list(){
    const rows=await core.call('content_oauth_list',{archived:$('archived').checked});
    $('select').replaceChildren(new Option('Новое подключение',''));
    for(const item of rows)$('select').append(new Option(item.label+' · '+(names[item.state]||item.state),item.id));
    $('select').value=row?.id||'';
  }
  async function poll(){
    if(!row)return;const id=row.id,stamp=sequence;
    const value=await core.call('content_oauth_get',{id});if(stamp!==sequence||row?.id!==id)return;
    const wasRunning=['authenticating','verifying'].includes(row.state);
    paint(value);if(!['authenticating','verifying'].includes(value.state)){
      await list();if(wasRunning)message(value.last_error?(events[value.last_error]||'Операция не завершена'):'Подключение проверено. Генерация пока не запускается.',!!value.last_error);
    }
  }
  async function load(){await runtimeRefresh();await list();if(row)paint(await core.call('content_oauth_get',{id:row.id}),true);else paint(null,true);}
  async function act(fn){if(busy)return;busy=true;locks();try{await fn();}catch(e){message(e.message,true);}finally{busy=false;locks();}}
  $('label').oninput=()=>{dirty=true;request=null;locks();};
  $('check').onclick=()=>act(async()=>{try{paintRuntime(await core.call('content_oauth_check_runtime',{}));message('Модуль входа готов.');}finally{await runtimeRefresh();}});
  $('install').onclick=()=>act(async()=>{paintRuntime(await core.call('content_oauth_install_runtime',{}));message('Устанавливается отдельный модуль. Модели и настройки Dewiar сохраняются.');});
  $('install-cancel').onclick=()=>act(async()=>{paintRuntime(await core.call('content_oauth_cancel_install',{}));message('Установка остановлена.');});
  $('new').onclick=()=>act(async()=>{if(!await discard())return;sequence++;paint(null,true);$('select').value='';message('Введите название нового подключения.');});
  $('reload').onclick=()=>act(async()=>{if(!await discard())return;sequence++;await load();message('Подключения загружены.');});
  $('archived').onchange=()=>act(async()=>{if(!await discard()){$('archived').checked=!$('archived').checked;return;}sequence++;paint(null,true);await list();});
  $('select').onchange=()=>act(async()=>{const id=$('select').value;if(!await discard()){$('select').value=row?.id||'';return;}sequence++;paint(id?await core.call('content_oauth_get',{id}):null,true);message('');});
  $('save').onclick=()=>act(async()=>{const label=$('label').value;const value=row?await core.call('content_oauth_update',{id:row.id,expected_revision:row.revision,label}):await core.call('content_oauth_create',{label,request_key:request||(request=crypto.randomUUID())});paint(value,true);await list();message('Название подключения сохранено.');});
  $('archive').onclick=()=>act(async()=>{if(!await discard())return;const archived=!row.archived;paint(await core.call('content_oauth_update',{id:row.id,expected_revision:row.revision,archived}),true);$('archived').checked=archived;await list();message(archived?'Подключение в архиве.':'Подключение восстановлено.');});
  for(const [id,operation] of [['login','authorize'],['verify','refresh']])$(id).onclick=()=>act(async()=>{
    paint(await core.call('content_oauth_start',{id:row.id,expected_revision:row.revision,operation,request_key:crypto.randomUUID()}));message(operation==='authorize'?'Дождитесь ссылки и завершите вход в отдельной вкладке.':'Проверяется сохранённый доступ.');await list();
  });
  $('cancel').onclick=()=>act(async()=>{paint(await core.call('content_oauth_cancel',{id:row.id,intent_id:row.intent_id}));await list();message('Операция отменена.');});
  $('disconnect').onclick=()=>act(async()=>{paint(await core.call('content_oauth_disconnect',{id:row.id,expected_revision:row.revision}));await list();message('Доступ отключён в Dewiar. Разрешение в Higgsfield не отозвано.');});
  panel.addEventListener('toggle',()=>{if(panel.open)act(async()=>{if(!dirty)await load();else {await runtimeRefresh();if(row)await poll();}});else {clearTimeout(timer);clearTimeout(runtimeTimer);}});
  window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
})();
