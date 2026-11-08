/* Website monitoring is an independent process. Preserve existing song tab lifecycle. */
(() => {
  const button=document.getElementById('tasks-sites-tab'), tasks=document.getElementById('p-tasks');
  if(!button||!tasks)return;
  const panel=document.createElement('div');panel.id='p-sites';panel.hidden=true;
  const note=document.createElement('p');note.className='hint';note.textContent='Сайты проверяются отдельной фоновой службой. Закрытие вкладки не останавливает наблюдение.';
  const open=document.createElement('button');open.className='btn';open.textContent='Открыть в отдельном окне';
  const frame=document.createElement('iframe');frame.title='Сайты — мониторинг Dewiar';frame.style.cssText='width:100%;height:calc(100vh - 210px);min-height:600px;border:0;border-radius:14px;margin-top:14px';
  panel.append(note,open,frame);tasks.append(panel);let url='';
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
    try{if(!url){const r=await core.call('sites_service');url=r.url;frame.src=url;}note.textContent='Мониторинг работает отдельным процессом. Настройки и состояние доступны во вкладках ниже.';}
    catch(e){note.textContent=e.message;}
  };
  open.onclick=()=>{if(url)window.open(url,'_blank','noopener');};
  document.addEventListener('DOMContentLoaded',()=>{if(location.hash==='#sites'){document.querySelector('nav button[data-p="tasks"]')?.click();button.click();}});
})();
