/* Website monitoring is an independent process. Preserve existing song tab lifecycle. */
(() => {
  const button=document.getElementById('tasks-sites-tab'), tasks=document.getElementById('p-tasks');
  if(!button||!tasks)return;
  const panel=document.createElement('div');panel.id='p-sites';panel.hidden=true;
  const note=document.createElement('p');note.className='hint';note.textContent='Сайты проверяются отдельной фоновой службой. Закрытие вкладки не останавливает наблюдение.';
  const open=document.createElement('button');open.className='btn';open.textContent='Открыть в отдельном окне';
  const frame=document.createElement('iframe');frame.title='Сайты — мониторинг Dewiar';frame.style.cssText='width:100%;height:calc(100vh - 210px);min-height:600px;border:0;border-radius:14px;margin-top:14px';
  panel.append(note,open,frame);tasks.append(panel);let url='';
  let requestedFilter=new URLSearchParams(location.search).get('sites');
  function withRequestedFilter(value){
    const target=new URL(value), fragment=new URLSearchParams(target.hash.slice(1));
    if(requestedFilter==='unavailable')fragment.set('state','DOWN');
    target.hash=fragment.toString();return target.toString();
  }
  const hide=()=>{panel.hidden=true;button.setAttribute('aria-pressed','false');};
  document.getElementById('tasks-work-tab')?.addEventListener('click',hide);
  document.getElementById('tasks-songs-tab')?.addEventListener('click',hide);
  document.getElementById('tasks-articles-tab')?.addEventListener('click',hide);
  document.getElementById('tasks-video-tab')?.addEventListener('click',hide);
  button.onclick=async()=>{
    document.getElementById('tasks-work-tab')?.click();
    document.getElementById('tasks-work-content').hidden=true;
    document.getElementById('tasks-work-tab').setAttribute('aria-pressed','false');
    panel.hidden=false;button.setAttribute('aria-pressed','true');
    try{if(!url){const r=await core.call('sites_service');url=withRequestedFilter(r.url);frame.src=url;}const scan=await core.call('sites_scan_ports');const bits=[];if(scan.queued_sites)bits.push('SSH и FTP · '+scan.queued_sites+' сайтов');if(scan.queued_consoles)bits.push('консоли · '+scan.queued_consoles+' провайдеров');note.textContent=bits.length?'В фоне проверяются '+bits.join(' · ')+'. Проверки не занимают планировщик мониторинга.':'Фоновые проверки уже выполняются или недавно завершились.';}
    catch(e){note.textContent=e.message;}
  };
  open.onclick=()=>{if(url)window.open(url,'_blank','noopener');};
  const selectSites=()=>{
    if(location.hash!=='#sites'&&!requestedFilter)return;
    document.querySelector('nav button[data-p="tasks"]')?.click();
    button.click();
  };
  // Existing Studio window: let the HUD route to Sites without destroying/recreating the window.
  window.openSitesMonitor=(filter)=>{
    requestedFilter=filter||'';
    if(url){
      const target=new URL(url),fragment=new URLSearchParams(target.hash.slice(1));
      if(requestedFilter==='unavailable')fragment.set('state','DOWN');else fragment.delete('state');
      target.hash=fragment.toString();url=target.toString();frame.src=url;
    }
    document.querySelector('nav button[data-p="tasks"]')?.click();
    if(core.connected)button.click();
    else {let fired=false;core.on('hello',()=>{if(!fired){fired=true;button.click();}});}
  };
  const openWhenReady=()=>{
    if(location.hash!=='#sites'&&!requestedFilter)return;
    if(core.connected){selectSites();return;}
    let fired=false;core.on('hello',()=>{if(!fired){fired=true;selectSites();}});
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',openWhenReady,{once:true});
  else openWhenReady();
})();
