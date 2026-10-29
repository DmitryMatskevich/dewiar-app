/* Non-blocking name prompt: do not replace the user's original task or expose card details to a model. */
(() => {
  let proposal=null;
  const box=document.createElement('aside');box.id='contact-suggestion';box.hidden=true;box.setAttribute('aria-label','Предложение контакта');
  box.style.cssText='position:fixed;left:12px;right:12px;bottom:92px;z-index:65;padding:12px 14px;border-radius:14px;background:var(--contact-prompt-bg,#edf2f8);color:var(--contact-prompt-ink,#253650);box-shadow:0 5px 24px rgba(0,0,0,.2);font:14px/1.45 system-ui;max-width:560px;';
  const text=document.createElement('p');text.style.margin='0 0 9px';text.setAttribute('role','status');const actions=document.createElement('div');actions.style.cssText='display:flex;gap:8px;flex-wrap:wrap';
  const open=document.createElement('button');open.type='button';open.className='contact-prompt-action';open.textContent='Посмотреть карточки';open.title='Открыть предложение контакта для просмотра. Само открытие ничего не создаёт и не импортирует.';
  const dismiss=document.createElement('button');dismiss.type='button';dismiss.className='contact-prompt-action';dismiss.textContent='Не предлагать здесь';dismiss.title='Больше не предлагать этого человека в текущей беседе. Контакты и сообщения не удаляются.';
  for(const b of [open,dismiss])b.style.cssText='font:inherit;padding:7px 10px;border-radius:8px;border:1px solid #bac7da;background:transparent;color:inherit;cursor:pointer';
  actions.append(open,dismiss);box.append(text,actions);document.body.append(box);
  document.querySelectorAll('nav button[data-p]').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.p==='contacts')box.hidden=true;else refresh();}));
  function show(p){if(!p?.name||!Array.isArray(p.local)||!Array.isArray(p.mac))p=null;proposal=p;box.hidden=!p||!!document.querySelector('#p-contacts.cur');if(!p)return;
    text.textContent=p.searching?`${p.name}: ищу совпадения в Контактах macOS…`:p.local.length?`${p.name}: контакт уже есть в Dewiar. Открыть карточку?`:p.mac.length?`${p.name}: найдено в Контактах macOS — ${p.mac.length}. Скопировать карточку в Dewiar?`:`Упомянут ${p.name}. Создать контакт или проверить совпадения в Контактах macOS?`;
  }
  async function refresh(){try{show(await core.call('contacts_pending'));}catch{} }
  open.onclick=async()=>{const nav=document.querySelector('nav button[data-p=contacts]');if(nav)nav.click();else{const api=window.api?.();if(api?.open_settings)await api.open_settings('contacts');}box.hidden=true;};
  dismiss.onclick=async()=>{if(!proposal)return;try{await core.call('contacts_proposal_action',{id:proposal.id,action:'dismiss'});show(null);}catch(e){text.textContent=e.message||String(e);}};
  core.on('contact_show',()=>{open.onclick().catch(()=>{});});
  core.on('contact_suggestion',refresh);core.on('hello',refresh);core.on('chat_changed',refresh);core.on('contacts_changed',refresh);
})();
