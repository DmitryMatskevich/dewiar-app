/* Link menus are local until the user selects an action. */
(() => {
  const updates=new WeakMap();
  function urlsOnly(text) {
    const parts = String(text || '').trim().split(/\s+/).filter(Boolean), result = [];
    if (!parts.length || parts.length > 200) return [];
    for (const part of parts) {
      try { const u = new URL(part); if (!/^https?:$/.test(u.protocol) || !u.hostname || u.username || u.password) return []; }
      catch (_) { return []; }
      if (!result.includes(part)) result.push(part);
    }
    return result;
  }
  function media(url) {
    const u = new URL(url), host = u.hostname.toLowerCase();
    return ['youtube.com','youtu.be','rutube.ru','vkvideo.ru','vimeo.com','dailymotion.com'].some(d=>host===d || host.endsWith('.'+d))
      || ((host==='vk.com' || host==='www.vk.com') && u.pathname.includes('/video'))
      || /\.(mp4|webm|mov|mkv|m4v|mp3|m4a|aac|wav|ogg|flac)$/i.test(u.pathname);
  }
  function fromMessage(item) { return item?.att?.find(a=>a.kind==='link_actions') || null; }
  function card(value, options={}) {
    const root=document.createElement('article');root.className='link-actions';root.setAttribute('aria-label','Действия со ссылкой');
    const title=document.createElement('b');title.textContent='Что сделать со ссылкой?';
    const close=document.createElement('button');close.type='button';close.className='la-close';close.textContent='×';close.title='Закрыть варианты действий';close.onclick=()=>{root.remove();options.dismiss?.();};
    const header=document.createElement('header');header.append(title,close);
    const source=document.createElement('div');source.className='la-source';source.textContent=value.urls.length===1 ? value.urls[0] : `${value.urls.length} ссылок`;
    const actions=document.createElement('div');actions.className='la-buttons';
    const status=document.createElement('div');status.className='la-status';status.setAttribute('role','status');
    let isMedia=value.media ?? value.urls.every(media), busy=false;
    const launched=new Map();
    function render() {
      actions.replaceChildren();
      const choices=isMedia ? [['text','Извлечь текст'],['video','Скачать видео'],['audio','Извлечь звук'],['batch','В пакетную обработку']]
        : [['text','Извлечь текст'],['seo-page','SEO страницы'],['seo-site','SEO всего сайта']];
      for (const [action,label] of choices) {
        const b=document.createElement('button');b.type='button';b.textContent=label;b.dataset.linkAction=action;b.disabled=busy || !!options.readOnly;
        b.onclick=async()=>{
          if(busy) return;
          if(launched.has(action)){openTask(launched.get(action).id);return;}
          if(action==='batch' || (action==='text' && isMedia)) {
            window.openTranscription(value.urls); // Review sources before launch.
            return;
          }
          busy=true;status.textContent='Создаю задачу…';render();
          try {
            const task=await core.call('link_action',{urls:value.urls,action,media:isMedia});
            launched.set(action,task);status.textContent=task.status==='done' ? 'Результат уже готов.' : 'Задача создана. Ход выполнения и файлы — в задачах.';
            options.started?.(task); const open=document.createElement('button');open.type='button';open.textContent='Открыть задачу';
            open.onclick=()=>openTask(task.id);status.append(' ',open);
          } catch(error) {status.textContent=error.message || String(error);}
          finally {busy=false;render();}
        };
        actions.append(b);
      }
      const mode=document.createElement('button');mode.type='button';mode.className='la-mode';mode.textContent=isMedia?'Это страница сайта':'Это видео или аудио';mode.disabled=busy || !!options.readOnly;
      mode.onclick=()=>{isMedia=!isMedia;render();};actions.append(mode);
      for(const b of actions.querySelectorAll('[data-link-action]')) if(launched.has(b.dataset.linkAction)) b.textContent+=' · задача создана';
    }
    root.append(header,source,actions,status);render();return root;
  }
  function openTask(id) {
    const a=window.api?.();
    if(a?.open_settings) a.open_settings('tasks','&task='+encodeURIComponent(id));
    else if(typeof window.openTask==='function') {document.querySelector('nav button[data-p="tasks"]')?.click();window.openTask(id);}
  }
  function bind(input, options={}) {
    const box=document.createElement('div');box.className='link-draft-actions';
    const parent=input.closest(options.parent || 'div');if(options.before)parent.before(box);else parent.after(box);
    let previous='',dismissed='';
    function update() {
      const text=input.value.trim();if(text===previous)return;previous=text;box.replaceChildren();
      const urls=urlsOnly(text),visible=!!urls.length && text!==dismissed;
      input.closest('#card')?.classList.toggle('has-link-draft',visible);
      if(!visible)return;
      box.append(card({urls},{dismiss:()=>{dismissed=text;input.closest('#card')?.classList.remove('has-link-draft');},started:()=>{
        if(input.value.trim()===text){input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));}
      }}));
    }
    updates.set(input,update);
    input.addEventListener('input',update);input.addEventListener('paste',()=>setTimeout(update,0));update();
  }
  window.DewiarLinks={urlsOnly,card,bind,fromMessage,refresh:input=>updates.get(input)?.()};
})();
