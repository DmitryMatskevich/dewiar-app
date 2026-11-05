(()=>{
 const root=document.createElement('div');root.id='assistant-topbar';
 const label=document.createElement('span'),select=document.createElement('select'),log=document.createElement('div');
 label.id='canvas-scope-status';label.setAttribute('role','status');log.id='canvas-scope-log';
 select.setAttribute('aria-label','Область работы Лиры');for(const [v,t] of [['general','Общий разговор'],['follow','Работа с холстом'],['pinned','Закрепить холст']])select.add(new Option(t,v));
 const input=document.getElementById('askin'),model=document.getElementById('ask-model');
 if(input&&model){root.append(select,model);const card=document.getElementById('card');card.classList.add('has-assistant-topbar');card.prepend(root);input.parentElement.parentElement.prepend(label,log);}else{root.append(select,label,log);document.body.append(root);}
 // Native selects may consume pointerup. Never let their pointerdown start window dragging.
 root.addEventListener('pointerdown',e=>e.stopPropagation());
 let latest=null;
 async function refresh(){const s=await core.call('canvas_scope_state');latest=s;select.value=s.mode;label.textContent=s.canvas_id?`Холст «${s.title}»${s.busy?' · выполняется':''}`:'';label.hidden=!s.canvas_id;log.hidden=!s.canvas_id;log.replaceChildren();if(s.canvas_id)for(const m of s.messages.slice(-4)){const p=document.createElement('p');p.textContent=(m.role==='user'?'Вы: ':'Лира: ')+m.text;log.append(p);}}
 select.onchange=async()=>{select.blur();try{await core.call('canvas_scope_mode',{mode:select.value});await refresh();}catch(e){label.hidden=false;label.textContent=e.message;await refresh();}};
 // Capture submit before general chat UI changes its answer state.
 if(input){const send=document.getElementById('a-send');const dispatch=async e=>{if(!latest?.canvas_id)return;e.preventDefault();e.stopImmediatePropagation();const text=input.value.trim();if(!text)return;try{await core.call('canvas_scope_submit',{text});input.value='';await refresh();}catch(err){label.textContent=err.message;}};
 if(send)send.addEventListener('click',dispatch,true);
 }
 setInterval(()=>refresh().catch(()=>{}),1200);refresh().catch(()=>{});
})();
