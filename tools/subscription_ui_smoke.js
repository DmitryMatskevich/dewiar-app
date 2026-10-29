(async () => {
  const originalCall = core.call, originalS = JSON.parse(JSON.stringify(S));
  const originalTimeout = window.setTimeout, originalTerminalLogin = window.pywebview.api.subscription_claude_login;
  const originalNativeApi = window.api;
  const originalChat = {...CH}, originalAsk = window.ask;
  let fakeTask = null, createdTeam = null, rejectTeam = false;
  let reportMode='queued', reportRetries=0;
  let strataRunning=false,strataStarts=0,strataLowMemory=false;
  let contactSuggestions=true,contactRows=[],contactCreates=0,contactImports=[],contactPermissionChecks=0,contactPermissionRequests=0;
  const nativeContacts=[{source_id:'fixture-a',family_name:'Иванов',given_name:'Иван',middle_name:'Иванович',phones:['+70000000001'],emails:['ivan@example.test']}, {source_id:'fixture-b',family_name:'Иванов',given_name:'Иван',middle_name:'Иванович',phones:['+70000000002'],emails:[]}];
  let fakeChat = [
    {id:'q1',dialog_id:'q1',ts:1,role:'user',text:'Проверка поиска <script>'},
    {id:'a1',dialog_id:'q1',ts:2,role:'assistant',text:'Ответ про ёлку'},
    {id:'q2',dialog_id:'q2',ts:3,role:'user',text:'Другой вопрос'}
  ].map(x => ({...x,chat_id:x.dialog_id})), fakeArchive = [], rejectChat = false, fakeActive = 'q1';
  const pendingLoginPolls = []; let terminalLaunches = 0;
  let modelTestMode = 'success', finishModelTest;
  const cards = [...document.querySelector('#subscription-cards').children];
  let authenticated = true, rejectSave = false, modelLoads = 0, rejectNetwork = false, permissionCalls = 0, metadataCalls = 0;
  const check = (value, message) => { if (!value) throw Error(message); };
  window.__subscriptionSmoke = {done: false};
  try {
    check(document.querySelector('#p-dw').classList.contains('services-disabled') && !document.querySelector('#dw-disabled').hidden, 'Disabled vendor services still offer a connection');
    check(annShow({id:999,title:'Fixture vendor news'}) === false && !document.querySelector('#ann-dlg').open, 'Disabled vendor announcement still opens');
    core.call = async (cmd, a) => {
      if(cmd==='contacts_suggest_set'){contactSuggestions=a.enabled;return {}; }
      if(cmd==='contacts_state')return {count:contactRows.length,suggest:contactSuggestions,permission:{state:'authorized',granted:true,limited:false}};
      if(cmd==='contacts_pending')return null;
      if(cmd==='contacts_list')return {items:contactRows,total:contactRows.length};
      if(cmd==='contacts_prepare_import')return {token:'contacts-fixture-token',contacts:nativeContacts,count:2,limited:true};
      if(cmd==='contacts_import') {check(a.token==='contacts-fixture-token','Wrong contact preview token');contactImports.push(a.ids);const old=contactRows.length;for(const id of a.ids){const row=nativeContacts.find(x=>x.source_id===id);if(!contactRows.some(x=>x.id===id))contactRows.push({...row,id,display_name:[row.family_name,row.given_name,row.middle_name].join(' ')});}return {added:contactRows.length-old,skipped:a.ids.length-(contactRows.length-old),updated:0};}
      if(cmd==='contacts_create'){check(a.confirmed===true && a.to_mac===true,'Creation not explicitly confirmed in both stores');contactCreates++;contactRows.push({...a.contact,id:'created-fixture',display_name:'Тест Создания'});return {saved_mac:true,id:'created-fixture'};}
      if(cmd==='contacts_delete'){contactRows=contactRows.filter(x=>x.id!==a.id);return {deleted:true,mac_unchanged:true};}

      if (cmd === 'strata_status') return {runtime:true,available_gib:64,catalog:[{id:'pack-fixture',name:'Strata package',ready:true,required_gib:43}],owned:strataRunning,state:strataRunning?'running':'stopped',model:strataRunning?'owned-fixture':'',error:''};
      if (cmd === 'strata_probe') return {name:'Strata',kind:'openai',base_url:a.base_url,api_key:a.api_key,local_backend:'strata',models:['strata-a','strata-b'],model:'strata-a'};
      if (cmd === 'strata_start') {if(strataLowMemory)throw Error('Недостаточно свободной памяти');strataRunning=true;strataStarts++;return {};}
      if (cmd === 'strata_stop') {strataRunning=false;return {};}
      if (cmd === 'strata_connection') return {name:'Strata',kind:'openai',local_backend:'strata',base_url:`http://127.0.0.1:${18080+strataStarts}/v1`,api_key:'fixture-owned-key',model:'owned-fixture',strata_owned:true,strata_package_id:'pack-fixture'};
      if (cmd === 'report_delivery_status') return {server_configured:false,mail_configured:reportMode !== 'queued',pending:reportMode === 'queued' ? [{queue_id:'fixture',reason:'Почта не подключена <script>'}] : []};
      if (cmd === 'complaint_send') return {id:'fixture',queue_id:'fixture',delivery:reportMode,reason:'Подключите почту'};
      if (cmd === 'report_delivery_retry') {reportRetries++;reportMode='email';return {id:'fixture',delivery:'email'};}
      if (cmd === 'tasks_list') return fakeTask ? [fakeTask] : [];
      if (cmd === 'task_get') return {...fakeTask};
      if (cmd === 'task_create') {
        createdTeam = a.team;
        fakeTask = {id:'teamFixture',title:'Проверка команды',goal:a.goal,project:'',status:'paused',status_ru:'на паузе',created:new Date().toISOString(),steps:0,
                    team:a.team,plan:'# План',journal:'',notebook:'',outputs:[],discussion:'Совет предложил два варианта плана.',team_review:'',council:{status:'no_consensus',round:2,calls:4,votes:{}}};
        return fakeTask;
      }
      if (cmd === 'task_team_set' || cmd === 'task_team_restart') {
        if (rejectTeam) throw Error('Fixture team save denied');
        fakeTask = {...fakeTask,team:a.team}; return fakeTask;
      }
      if (cmd === 'models_routing_status') return {automatic:!!S.llm.auto_select,next:'pickerFixture',timezone:'Europe/Moscow',profiles:Object.keys(S.llm.profiles).map(id => ({id,available:true,quota:{buckets:[{id:'fixture',name:'Общий лимит',windows:[{name:'primary',minutes:300,remaining_percent:0,resets_at:2000000000}]}],checked_at:1900000000}}))};
      if (cmd === 'chat_list') {
        const items = (a?.archived ? fakeArchive : fakeChat).filter(x => (!a?.chat_id || x.chat_id === a.chat_id) && (!a?.q || x.text.toLowerCase().includes(a.q.toLowerCase())));
        return {items,more:false,matched:items.length};
      }
      if (cmd === 'chat_threads') {
        const groups = new Map();
        for (const x of a.archived ? fakeArchive : fakeChat) {
          if (!groups.has(x.chat_id)) groups.set(x.chat_id, {id:x.chat_id,title:x.text,count:0});
          groups.get(x.chat_id).count++;
        }
        if (!a.archived && !groups.has(fakeActive)) groups.set(fakeActive,{id:fakeActive,title:'Новый чат',count:0});
        return {items:[...groups.values()],active:fakeActive};
      }
      if (cmd === 'chat_select') { fakeActive = a.chat_id || 'newFixture'; return {active:fakeActive}; }
      if (cmd === 'chat_manage') {
        if (rejectChat) throw Error('Fixture archive disk full');
        const rows = (a.archived ? fakeArchive : fakeChat).filter(x => x.dialog_id === a.dialog_id);
        if (a.archived) fakeArchive = fakeArchive.filter(x => x.dialog_id !== a.dialog_id);
        else fakeChat = fakeChat.filter(x => x.dialog_id !== a.dialog_id);
        if (a.action === 'archive') fakeArchive.push(...rows);
        if (a.action === 'restore') fakeChat.push(...rows);
        if (!a.archived && fakeActive === a.dialog_id) fakeActive = fakeChat[0]?.chat_id || 'emptyFixture';
        return {count:rows.length};
      }
      if (cmd === 'llm_test') {
        if (modelTestMode === 'pending') return new Promise(resolve => { finishModelTest = resolve; });
        if (modelTestMode === 'throw') throw Error('Fixture connection lost');
        if (modelTestMode === 'error') return {ok:false,error:'Fixture model unavailable <script>'};
        return {ok:true,model:'qwen3:30b',api:'ollama',total_ms:5100,reply:'готово'};
      }
      if (cmd === 'local_models_status') return [
        {name:'Ollama',installed:true,running:true,models:['a','b']},
        {name:'LM Studio',installed:true,running:false,models:[]}
      ];
      if (cmd === 'subscription_login') return {terminal_login:true};
      if (cmd === 'hello') return {settings:S};
      if (cmd === 'security_set') {
        permissionCalls++;
        check(a.local_only === false, 'Wrong security setting');
        if (rejectNetwork) throw Error('Fixture network permission denied');
        S = {...S,privacy:{...S.privacy,local_only:false}};
        return {state:{local_only:false}};
      }
      if (cmd === 'subscription_status') { metadataCalls++; return {authenticated, plan: 'fixture', models: ['fixture-model','fixture-alt'].map(id => ({id,name:id,efforts:a.provider === 'codex' ? ['medium','high'] : []}))}; }
      if (cmd === 'llm_models') { modelLoads++; return ['deepseek-r1:70b', 'qwen3.6:35b', 'gemma4:12b']; }
      if (cmd === 'settings_save') {
        if (rejectSave) throw Error('Fixture save denied');
        return {...S,llm:{...S.llm,...a.patch.llm}};
      }
      return originalCall.call(core, cmd, a);
    };
    S = {...S,privacy:{...S.privacy,local_only:true}}; refreshStatic();
    const networkButtons = cards.map(c => c.querySelector('[data-a=network]'));
    check(networkButtons.every(b => !b.hidden && getComputedStyle(b).display !== 'none'), 'Inline permission missing in local mode');
    check(permissionCalls === 0, 'Opening models silently changes security');
    rejectNetwork = true;
    await networkButtons[0].onclick();
    check(S.privacy.local_only === true && metadataCalls === 0, 'Failed permission attempts connect anyway');
    check(cards[0].querySelector('.res').textContent.includes('Fixture network permission denied'), 'Permission error hidden');
    rejectNetwork = false;
    await networkButtons[0].onclick();
    check(S.privacy.local_only === false && metadataCalls === 1, 'Permission does not refresh models');
    check(networkButtons.every(b => b.hidden && getComputedStyle(b).display === 'none'), 'Permission controls remain visible after allowing network');
    S = {...S,privacy:{...S.privacy,local_only:true}}; refreshStatic();
    check(networkButtons.every(b => !b.hidden), 'External security change not reflected');
    await networkButtons[1].onclick();
    check(S.privacy.local_only === false && metadataCalls === 2, 'Claude inline permission does not refresh models');
    const metadataBeforeLogin = metadataCalls;
    core.emit('subscription_login_completed', {provider:'codex',success:true});
    await new Promise(resolve => setTimeout(resolve,0));
    check(metadataCalls === metadataBeforeLogin + 1, 'Browser login completion does not refresh Codex');
    check(!cards[0].querySelector('[data-a=use]').disabled, 'Successful login leaves model selection disabled');
    core.emit('subscription_login_completed', {provider:'codex',success:false,error:'Fixture login rejected'});
    check(cards[0].querySelector('.res').textContent === 'Fixture login rejected', 'Rejected login displayed as success');
    document.querySelector('nav button[data-p="brain"]').click();
    await new Promise(resolve => setTimeout(resolve,0));
    const metadataBeforeFocus = metadataCalls;
    window.dispatchEvent(new Event('focus'));
    await new Promise(resolve => setTimeout(resolve,0));
    check(metadataCalls === metadataBeforeFocus + 2, 'Returning from browser does not refresh account state');
    // Terminal login must refresh even when WebKit does not fire a focus event.
    window.pywebview.api.subscription_claude_login = async () => { terminalLaunches++; return true; };
    window.setTimeout = (fn, ms, ...args) => {
      if (ms === 3000 && fn.constructor.name === 'AsyncFunction') { pendingLoginPolls.push(fn); return -1000; }
      return originalTimeout(fn, ms, ...args);
    };
    const claudeLogin = cards[1].querySelector('[data-a=login]');
    authenticated = false;
    await claudeLogin.onclick({target:claudeLogin});
    check(terminalLaunches === 1 && pendingLoginPolls.length === 1, 'Terminal login watcher not started');
    await pendingLoginPolls.shift()();
    check(cards[1].querySelector('[data-a=use]').disabled && pendingLoginPolls.length === 1, 'Watcher stops before login completes');
    authenticated = true;
    await pendingLoginPolls.shift()();
    check(!cards[1].querySelector('[data-a=use]').disabled && pendingLoginPolls.length === 0, 'Terminal login does not populate models');
    await claudeLogin.onclick({target:claudeLogin});
    S = {...S,privacy:{...S.privacy,local_only:true}};
    const metadataBeforeBlockedPoll = metadataCalls;
    await pendingLoginPolls.shift()();
    check(metadataCalls === metadataBeforeBlockedPoll && pendingLoginPolls.length === 0, 'Watcher ignores local-only privacy');
    S = {...S,privacy:{...S.privacy,local_only:false}};
    window.setTimeout = originalTimeout;
    window.pywebview.api.subscription_claude_login = originalTerminalLogin;
    for (const card of cards) {
      const refresh = card.querySelector('[data-a=refresh]'), use = card.querySelector('[data-a=use]');
      await refresh.onclick({target:refresh});
      check(!use.disabled, 'Authenticated model cannot be selected');
      rejectSave = true;
      await use.onclick();
      check(card.querySelector('.res').textContent.includes('Fixture save denied'), 'Failed save displayed as success');
      rejectSave = false;
      await use.onclick();
      const profile = S.llm.profiles[S.llm.active];
      check(profile.model === 'fixture-model', 'Selected model not saved');
      check(!profile.api_key, 'Subscription profile contains API key');
      const el = document.querySelector(`.prof[data-id="${S.llm.active}"]`);
      check(getComputedStyle(el.querySelector('[data-f=api_key]').closest('label')).display === 'none', 'Subscription asks for API key');
      const firstId = S.llm.active;
      S.llm.profiles[firstId].priority = 3;
      S.llm.profiles[firstId].schedule_end = '24:00';
      const providerModel = card.querySelector('[data-a=model]');
      providerModel.value = 'fixture-alt'; await use.onclick();
      check(S.llm.active !== firstId && S.llm.profiles[firstId].model === 'fixture-model', 'Second subscription model overwrites first model');
      providerModel.value = 'fixture-model'; await use.onclick();
      check(S.llm.active === firstId && S.llm.profiles[firstId].priority === 3 && S.llm.profiles[firstId].schedule_end === '24:00', 'Reusing model erases its routing settings');
      authenticated = false;
      await refresh.onclick({target:refresh});
      check(use.disabled, 'Unauthenticated profile can be selected');
      authenticated = true;
    }
    const activeBeforeLocalStatus = S.llm.active;
    await loadLocalModelStatus();
    const localStatus = document.getElementById('local-model-installations').textContent;
    check(localStatus.includes('Ollama: установлена · сервер работает · моделей в списке: 2'), 'Running installed Ollama status incorrect');
    check(localStatus.includes('LM Studio: установлена · API на стандартном порту не отвечает'), 'Installed stopped LM Studio shown as missing');
    check(!document.querySelector('#lc-how summary').textContent.includes('Ещё не установлено'), 'Installation guide still asserts a missing app');
    check(document.querySelector('#lc-steps').textContent.includes('macOS') && !document.querySelector('#lc-steps').textContent.includes('install.sh'), 'Mac receives Linux installation instructions');
    check(S.llm.active === activeBeforeLocalStatus, 'Status check changes active model');
    // Regression: a prefilled DeepSeek name must not hide unrelated Ollama models.
    S.llm.profiles.pickerFixture = {name:'Ollama fixture',kind:'ollama',base_url:'http://127.0.0.1:11434',model:'deepseek-r1:70b'};
    openProf = 'pickerFixture'; renderProfiles();
    check(modelLoads > 0, 'Opening Ollama profile does not automatically load models');
    let el = document.querySelector('.prof[data-id="pickerFixture"]');
    await loadProfileModels('pickerFixture', el);
    let picker = el.querySelector('[data-model-picker]');
    check([...picker.options].some(o => o.value === 'gemma4:12b'), 'Prefilled name filters the model list');
    check(picker.value === 'deepseek-r1:70b', 'Loading model list changes selection');
    rejectSave = true; picker.value = 'gemma4:12b';
    await picker.onchange({target:picker});
    check(S.llm.profiles.pickerFixture.model === 'deepseek-r1:70b', 'Failed save changes selected model');
    rejectSave = false; picker.value = 'gemma4:12b';
    await picker.onchange({target:picker});
    el = document.querySelector('.prof[data-id="pickerFixture"]');
    check(el.querySelector('[data-model-picker]').value === 'gemma4:12b', 'Model selection not retained after redraw');
    check(S.llm.profiles.pickerFixture.model === 'gemma4:12b', 'Model selection not saved');
    let priority = el.querySelector('[data-f=priority]');
    priority.value = '1'; await priority.onchange();
    check(S.llm.profiles.pickerFixture.priority === 1, 'Routing priority not saved');
    el = document.querySelector('.prof[data-id="pickerFixture"]');
    const endTime = el.querySelector('[data-f=schedule_end]');
    endTime.value = '24:00'; await endTime.onchange();
    check(S.llm.profiles.pickerFixture.schedule_end === '24:00', 'Midnight schedule boundary not saved');
    el = document.querySelector('.prof[data-id="pickerFixture"]');
    priority = el.querySelector('[data-f=priority]'); rejectSave = true;
    priority.value = '4'; await priority.onchange(); rejectSave = false;
    check(S.llm.profiles.pickerFixture.priority === 1 && priority.value === '1', 'Failed routing save changes settings');
    await loadModelRoutingStatus();
    check(el.querySelector('[data-quota]').textContent.includes('осталось 0%') && el.querySelector('[data-quota]').textContent.includes('сброс'), 'Zero quota/reset hidden in profile header');
    check(modelQuotaText({},'claude_subscription').includes('нет данных'), 'Unknown Claude quota shown as zero or unlimited');
    const testButton = el.querySelector('[data-a=test]'), notice = document.getElementById('toast');
    modelTestMode = 'pending';
    const pendingTest = testButton.onclick({target:testButton});
    check(testButton.disabled && el.querySelector('.head .res').textContent.includes('проверяю'), 'Pending check has no progress');
    finishModelTest({ok:true,model:'qwen3:30b',api:'ollama',total_ms:5100,reply:'готово'});
    await pendingTest;
    check(notice.classList.contains('show') && notice.classList.contains('success') && notice.textContent.includes('qwen3:30b') && notice.textContent.includes('5.1'), 'Successful model check has no popup');
    check(!testButton.disabled && !el.querySelector('.head .res').textContent, 'Model result still stretches profile header');
    for (const mode of ['error','throw']) {
      modelTestMode = mode;
      await testButton.onclick({target:testButton});
      check(notice.classList.contains('error') && notice.textContent.includes(mode === 'error' ? 'Fixture model unavailable <script>' : 'Fixture connection lost'), 'Model failure has no safe popup');
      check(!notice.querySelector('script') && !testButton.disabled && !el.querySelector('.head .res').textContent, 'Failure leaves unsafe markup or busy profile');
    }
    toast('Сохранено');
    check(!notice.classList.contains('error') && !notice.classList.contains('success'), 'Ordinary notification inherits model error style');
    document.querySelector('nav button[data-p="chat"]').click();
    await loadChat();
    check(document.querySelector('#ch-msgs').textContent.includes('Проверка поиска <script>') && !document.querySelector('#ch-msgs script'), 'Chat search fixture executes markup');
    document.querySelector('#ch-search').value = 'ЁЛКУ'; document.querySelector('#ch-search').oninput();
    clearTimeout(chatSearchTimer); await loadChat();
    check(CH.items.length === 1 && CH.items[0].id === 'a1', 'Chat search does not find answer text');
    rejectChat = true;
    await chatManage(document.querySelector('[data-chat-action=archive]'));
    check(CH.items.length === 1 && notice.textContent.includes('disk full'), 'Failed archive silently removes messages');
    rejectChat = false;
    await chatManage(document.querySelector('[data-chat-action=archive]'));
    check(CH.items.length === 0 && fakeArchive.length === 2 && fakeChat.length === 1, 'Archive does not move the whole chat');
    document.querySelector('#ch-search-clear').onclick();
    document.querySelector('#ch-scope').value = 'archive'; document.querySelector('#ch-scope').onchange(); await loadChat();
    check(CH.items.length === 2 && getComputedStyle(document.querySelector('#ch-box .cl-send')).display === 'none', 'Archive view shows active composer or missing messages');
    await chatManage(document.querySelector('[data-chat-action=restore]'));
    check(fakeArchive.length === 0 && fakeChat.length === 3, 'Restore loses replies');
    document.querySelector('#ch-scope').value = 'active'; document.querySelector('#ch-scope').onchange(); await loadChat();
    window.ask = async () => false;
    await chatManage(document.querySelector('[data-chat-action=delete]'));
    check(fakeChat.length === 3, 'Cancelled delete removes messages');
    window.ask = async () => true;
    await chatManage(document.querySelector('[data-chat-action=delete]'));
    check(fakeChat.length === 2, 'Delete removes the wrong exchange');
    await document.querySelector('#ch-new').onclick();
    check(CH.thread === 'newFixture' && CH.items.length === 0 && !CH.archived, 'New chat does not start an empty conversation');
    document.querySelector('nav button[data-p="tasks"]').click();
    document.querySelector('#t-new').onclick();
    const newTeam = document.querySelector('[data-task-team=new]');
    newTeam.querySelector('[data-team=enabled]').checked = true; newTeam.querySelector('[data-team=enabled]').onchange();
    const ids = Object.keys(S.llm.profiles);
    check(newTeam.querySelector('[data-team=judge]').value === '', 'Evaluator should default to active model');
    newTeam.querySelector('[data-team=judge]').value=ids[1];
    newTeam.querySelector('[data-planner="0"]').value = ids[0]; newTeam.querySelector('[data-planner="1"]').value = ids[1];
    newTeam.querySelector('[data-team=developer]').value = ids[0]; newTeam.querySelector('[data-team=max_rounds]').value='2';
    document.querySelector('#t-goal').value='Разработка тестового приложения';
    await document.querySelector('#t-go').onclick();
    check(createdTeam.enabled && createdTeam.planners.length === 2 && createdTeam.max_rounds === 2 && createdTeam.developer === ids[0] && createdTeam.judge === ids[1], 'Task creation loses evaluator, roles or budget');
    await openTask('teamFixture'); tTab='discussion'; await openTask('teamFixture');
    check(document.querySelector('.task-council-status').textContent.includes('Консенсус не достигнут') && document.querySelector('#t-view').textContent.includes('два варианта'), 'Discussion UI claims consensus or hides transcript');
    fakeTask.status='running'; tTab='team'; await openTask('teamFixture');
    check(document.querySelector('[data-task-team=edit] [data-planner]').disabled && document.querySelector('#task-team-save').disabled, 'Running task team is editable');
    fakeTask.status='paused'; await openTask('teamFixture');
    const developer = document.querySelector('[data-task-team=edit] [data-team=developer]');
    developer.value=ids[1]; developer.onchange();
    rejectTeam=true; await document.querySelector('#task-team-save').onclick();
    check(fakeTask.team.developer===ids[0] && !document.querySelector('#task-team-save').disabled, 'Failed team save accepted');
    rejectTeam=false; await document.querySelector('#task-team-save').onclick();
    check(fakeTask.team.developer===ids[1], 'Updated developer assignment not saved');
    document.querySelector('nav button[data-p="brain"]').click();
    await loadStrataStatus();check(strataStarts===0,'Strata metadata loads a model');
    document.querySelector('#strata-key').value='fixture-key';await document.querySelector('#strata-probe').onclick();
    check(document.querySelectorAll('#strata-api-model option').length===2,'Strata model catalog incomplete');
    await document.querySelector('#strata-use').onclick();
    const strataA=Object.entries(S.llm.profiles).find(([,p])=>p.model==='strata-a');
    check(strataA && !strataA[1].models,'Strata profile not saved cleanly');strataA[1].priority=2;
    document.querySelector('#strata-api-model').value='strata-b';await document.querySelector('#strata-use').onclick();
    check(S.llm.profiles[strataA[0]].priority===2 && Object.values(S.llm.profiles).some(p=>p.model==='strata-b'),'Strata models overwrite each other');
    await document.querySelector('#strata-start').onclick();await document.querySelector('#strata-use-owned').onclick();
    const ownedId=Object.keys(S.llm.profiles).find(id=>S.llm.profiles[id].strata_package_id==='pack-fixture');
    check(ownedId,'Owned Strata connection missing');S.llm.profiles[ownedId].priority=3;
    await document.querySelector('#strata-stop').onclick();await document.querySelector('#strata-start').onclick();await document.querySelector('#strata-use-owned').onclick();
    check(Object.values(S.llm.profiles).filter(p=>p.strata_package_id==='pack-fixture').length===1 && S.llm.profiles[ownedId].priority===3,'Restart duplicates Strata profile or loses priority');
    await document.querySelector('#strata-stop').onclick();strataLowMemory=true;await document.querySelector('#strata-start').onclick();
    check(document.querySelector('#strata-status').textContent.includes('Недостаточно свободной памяти') && !strataRunning,'Strata memory failure hidden');
    document.querySelector('nav button[data-p="about"]').click();
    await loadReportDelivery();
    check(document.querySelector('#report-pending').textContent.includes('ожидает отправки') && !document.querySelector('#report-pending script'), 'Pending report is hidden or unsafe');
    document.querySelector('#rep-note').value='Синтетическая проверка формы'; bugShow('');
    const reportSend=document.querySelector('#bug-send');
    await reportSend.onclick({currentTarget:reportSend});
    check(!document.querySelector('#rep-res').classList.contains('ok') && document.querySelector('#rep-res').textContent.includes('локально'), 'Queued report presented as sent');
    await document.querySelector('[data-report-retry]').onclick();
    check(reportRetries === 1 && document.querySelector('#rep-res').textContent.includes('dmatskevich@gmail.com') && document.querySelector('#rep-res').textContent.includes('Ошибка обработки персональный агент'), 'Email retry result missing recipient/subject');
    check(!document.querySelector('[data-report-retry]'), 'Sent report still offers retry');
    reportMode='server'; document.querySelector('#rep-note').value='Синтетическая проверка сервера';
    await reportSend.onclick({currentTarget:reportSend});
    check(document.querySelector('#rep-res').textContent.includes('сервером'), 'Server delivery result missing');
    let appAccessGranted=false,appSettingsOpened=0;
    window.api=()=>({accessibility_permission:async()=>({granted:appAccessGranted,packaged:true}),open_accessibility_settings:async()=>{appSettingsOpened++;}});
    document.querySelector('nav button[data-p="hands"]').click();
    await document.querySelector('#mac-app-refresh').onclick();
    check(document.querySelector('#mac-app-access').style.display==='block' && document.querySelector('#mac-app-status').textContent.includes('включите Dewiar'),'Accessibility denied state missing');
    await document.querySelector('#mac-app-settings').onclick();
    check(appSettingsOpened===1,'Accessibility settings button not wired');
    appAccessGranted=true;await document.querySelector('#mac-app-refresh').onclick();
    check(document.querySelector('#mac-app-status').textContent.includes('Доступ разрешён'),'Accessibility granted state missing');
    window.api=originalNativeApi;
    window.api=()=>({contacts_permission:async()=>{contactPermissionChecks++;return {state:'authorized',granted:true,limited:false,request_supported:true};},request_contacts_access:async()=>{contactPermissionRequests++;return {state:'authorized',granted:true,request_supported:true};},open_contacts_settings:async()=>true});
    document.querySelector('nav button[data-p=contacts]').click();
    for(let i=0;i<100 && !document.querySelector('#ct-count').textContent.includes('0');i++)await new Promise(r=>originalTimeout(r,10));
    await document.querySelector('#ct-read').onclick();
    check(document.querySelectorAll('#ct-source-list input[type=checkbox]').length===2,'Native contact preview missing');
    check(document.querySelector('#ct-source-total').textContent.includes('ограничен'),'Limited Contacts scope hidden');
    const firstContact=document.querySelector('#ct-source-list input');firstContact.checked=true;firstContact.dispatchEvent(new Event('change'));
    await document.querySelector('#ct-import').onclick();check(contactImports[0].length===1 && contactRows.length===1,'Partial import includes unselected cards');
    document.querySelector('#ct-select-all').click();await document.querySelector('#ct-import').onclick();check(contactRows.length===2,'Import-all duplicate handling failed');
    document.querySelector('#ct-new').click();check(document.querySelector('#ct-to-mac').checked,'New contact does not default to both stores');
    document.querySelector('[data-ct=family_name]').value='Тест';document.querySelector('[data-ct=given_name]').value='Создания';
    window.ask=async()=>false;await document.querySelector('#ct-form').onsubmit({preventDefault(){}});check(contactCreates===0,'Declined contact is created');
    window.ask=async()=>true;await document.querySelector('#ct-form').onsubmit({preventDefault(){}});check(contactCreates===1 && document.querySelector('#ct-status').textContent.includes('Контактах macOS'),'Confirmed contact result missing');
    const suggestionToggle=document.querySelector('#ct-suggest');suggestionToggle.checked=false;await suggestionToggle.onchange({target:suggestionToggle});check(contactSuggestions===false,'Contact suggestion toggle not saved');suggestionToggle.checked=true;await suggestionToggle.onchange({target:suggestionToggle});
    check(contactPermissionRequests===0 && contactPermissionChecks>0,'Contacts prompts on page open');
    window.api=originalNativeApi;
    refreshHelpTitles();
    check(DEWIAR_VIDEO_HELP_REFERENCE.chapters.length===30,'Video chapter reference incomplete');
    check(DEWIAR_HELP.memory.video_refs.some(v=>v.id==='V15'),'Memory chapter not attached');
    showHelp('soul');check(document.querySelector('#help-body').textContent.includes('раз в неделю'),'Soul schedule from code missing');
    const helpKeys=Object.keys(DEWIAR_HELP),savedHelpCall=core.call;let helpCalls=0;
    core.call=async()=>{helpCalls++;throw Error('Help must not call the core');};
    try {
      for(const key of helpKeys){showHelp(key);check(document.querySelector('#help-body').textContent.length>200,'Empty help article: '+key);}
      check(helpCalls===0,'Reading help calls model/core');
      showHelp('brain');
      const helpInput=document.querySelector('#help-search');helpInput.value='микрофон';helpInput.dispatchEvent(new Event('input'));
      check(document.querySelectorAll('#help-results button').length>0,'Help search empty');
      helpInput.value='<script>invalid</script>';helpInput.dispatchEvent(new Event('input'));
      check(document.querySelector('#help-results').textContent.includes('Ничего не найдено'),'Help search no-results state');
      document.querySelector('#help-current').click();check(document.querySelector('#help-body').textContent.length>200,'Help current section missing');
      document.querySelector('#help-x').click();check(document.querySelector('#help').hidden,'Help close failed');
      check([...document.querySelectorAll('main section[id^="p-"]')].every(s=>s.querySelectorAll('[data-section-help]').length===1),'Missing or duplicated help buttons');
      check([...document.querySelectorAll('button,input,select,textarea,summary')].every(e=>e.title.trim()),'Missing detailed control title');
      check(document.querySelectorAll('main .hbtn,#help .tplay').length===0,'Extra question buttons/executable help examples');
    } finally {core.call=savedHelpCall;helpClose();}
    window.__subscriptionSmoke = {done:true,passed:true,contacts_ui:true,context_help:true,help_articles:helpKeys.length,video_help_chapters:DEWIAR_VIDEO_HELP_REFERENCE.chapters.length,mac_app_controls:true,strata_controls:true,report_email_fallback:true,task_council_controls:true,model_routing_controls:true,chat_search_archive:true,model_test_popup:true,model_picker:true,inline_network_permission:true,login_refresh:true,terminal_login_refresh:true,local_installation_status:true};
  } catch(e) { window.__subscriptionSmoke = {done:true,passed:false,error:e.message}; }
  finally { window.api=originalNativeApi; clearTimeout(chatSearchTimer); window.ask = originalAsk; Object.assign(CH, originalChat); document.querySelector('#ch-search').value = CH.query; document.querySelector('#ch-scope').value = CH.archived ? 'archive' : 'active'; chatRender(); window.setTimeout = originalTimeout; window.pywebview.api.subscription_claude_login = originalTerminalLogin; core.call = originalCall; S = originalS; openProf = null; profileModelLists.delete("pickerFixture"); refreshStatic(); renderProfiles(); }
})();
