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
  function video(url) {
    const u=new URL(url), host=u.hostname.toLowerCase().replace(/^www\./,'');
    return ['youtube.com','youtu.be','rutube.ru','vkvideo.ru','vimeo.com','dailymotion.com'].some(d=>host===d||host.endsWith('.'+d))
      || (host==='vk.com'&&u.pathname.includes('/video'))
      || /\.(mp4|webm|mov|mkv|m4v)$/i.test(u.pathname);
  }
  function fromMessage(item) { return item?.att?.find(a=>a.kind==='link_actions') || null; }
  function placeMenu(toggle, list) {
    const gap = 6;
    list.style.left = list.style.top = '0px';
    list.style.maxHeight = '';
    const anchor = toggle.getBoundingClientRect();
    const menu = list.getBoundingClientRect();
    const zoom = menu.width / list.offsetWidth || 1;
    const below = innerHeight - anchor.bottom - gap;
    const above = anchor.top - gap;
    const opensBelow = below >= menu.height || below >= above;
    const height = Math.min(menu.height, Math.max(0, (opensBelow ? below : above) - gap));
    const left = Math.max(gap, Math.min(anchor.left, innerWidth - menu.width - gap));
    const top = opensBelow ? anchor.bottom + gap : anchor.top - gap - height;
    list.style.maxHeight = `${height / zoom}px`;
    list.style.left = `${(left - menu.left) / zoom}px`;
    list.style.top = `${(top - menu.top) / zoom}px`;
  }
  function card(value, options={}) {
    const root=document.createElement('article');root.className='link-actions';root.setAttribute('aria-label','Действия со ссылкой');
    const title=document.createElement('b');title.textContent='Что сделать со ссылкой?';
    const close=document.createElement('button');close.type='button';close.className='la-close';close.textContent='×';close.title='Закрыть варианты действий';close.onclick=()=>{root.remove();options.dismiss?.();};
    const header=document.createElement('header');header.append(title,close);
    const source=document.createElement('div');source.className='la-source';source.textContent=value.urls.length===1 ? value.urls[0] : `${value.urls.length} ссылок`;
    const actions=document.createElement('div');actions.className='la-buttons';
    const status=document.createElement('div');status.className='la-status';status.setAttribute('role','status');
    let isMedia=value.media ?? value.urls.every(media), isVideo=value.video ?? value.urls.every(video), busy=false;
    const launched=new Map();
    async function runAction(action) {
      if(busy) return;
      if(action==='transcribe') { window.openTranscription(value.urls); return; }
      if(launched.has(action)){openTask(launched.get(action).id);return;}
      busy=true;status.textContent='Создаю задачу…';render();
      try {
        const task=await core.call('link_action',{urls:value.urls,action,media:isMedia});
        launched.set(action,task);status.textContent=task.status==='done' ? 'Результат уже готов.' : 'Задача создана. Ход выполнения и файлы — в задачах.';
        options.started?.(task); const open=document.createElement('button');open.type='button';open.textContent='Открыть задачу';
        open.onclick=()=>openTask(task.id);status.append(' ',open);
      } catch(error) {status.textContent=error.message || String(error);}
      finally {busy=false;render();}
    }
    function render() {
      actions.replaceChildren();
      const button=(action,label,parent=actions)=>{const b=document.createElement('button');b.type='button';b.textContent=label;b.dataset.linkAction=action;b.disabled=busy||!!options.readOnly;b.onclick=()=>runAction(action);if(launched.has(action))b.textContent+=' · задача создана';parent.append(b);return b;};
      const menu=(label,choices,icon=false)=>{const details=document.createElement('div');details.className='la-menu';const toggle=document.createElement('button');toggle.type='button';toggle.className='la-menu-toggle'+(icon?' icon':'');toggle.title=label;toggle.setAttribute('aria-label',label);toggle.setAttribute('aria-haspopup','menu');toggle.setAttribute('aria-expanded','false');
        if(icon) toggle.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><text x="2" y="16" fill="currentColor" stroke="none" font-size="14" font-weight="700">T</text><text x="13" y="16" fill="currentColor" stroke="none" font-size="10" font-weight="700">т</text><path d="M3 20h18"/></svg>';
        else toggle.textContent=label+' ▾';
        const list=document.createElement('div');list.className='la-menu-options';list.setAttribute('role','menu');list.setAttribute('aria-label',label);list.hidden=true;
        toggle.onclick = () => {
          const open = list.hidden;
          for (const menu of actions.querySelectorAll('.la-menu-options')) {
            menu.hidden = true;
            menu.previousElementSibling.setAttribute('aria-expanded', 'false');
          }
          if (open) {
            list.hidden = false;
            toggle.setAttribute('aria-expanded', 'true');
            placeMenu(toggle, list);
          }
        };
        choices.forEach(([action,text])=>{const item=button(action,text,list);item.setAttribute('role','menuitem');});
        details.append(toggle,list);actions.append(details);};
      if(isMedia) {
        if(isVideo) menu('Видео',[['video','Скачать видео'],['transcribe','Транскрибировать']]);
        else menu('Аудио',[['audio','Извлечь звук'],['transcribe','Транскрибировать']]);
      } else {
        menu('Текст',[['text','Извлечь текст'],['html','Извлечь HTML и картинки'],['contacts','Извлечь контакты'],['images','Извлечь картинки'],['screenshot','Снимок страницы']],false);
        menu('SEO',[['seo-page','SEO страницы'],['seo-site','SEO всего сайта']]);
      }
      if(isMedia) {
        const mode=document.createElement('button');mode.type='button';mode.className='la-mode';mode.textContent='Это страница сайта';mode.disabled=busy || !!options.readOnly;
        mode.onclick=()=>{isMedia=false;isVideo=false;render();};actions.append(mode);
      }
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
