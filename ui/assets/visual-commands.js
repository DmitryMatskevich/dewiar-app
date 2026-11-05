/* Local OCR commands. Preview is drawn separately and never enters saved/exported state. */
window.installVisualCommands=(editor,{call,img,status})=>{
 const $=id=>document.getElementById(id);
 let running=false,proposal=null,baseline='',voiceSession=null,voicePending=false,voiceStart=null;
 const fingerprint=()=>JSON.stringify({state:editor.area.getState(),crop:editor.doc.crop});
 function lock(value){running=value;$('command-run').disabled=value||voiceSession!==null;$('private-find').disabled=value||voiceSession!==null;$('command-mic').disabled=value||voicePending;}
 function chosen(){return [...$('command-options').querySelectorAll('input:checked')].map(input=>proposal.candidates[Number(input.value)]);}
 function preview(){
  const canvas=$('command-preview'),scale=Math.min(1,760/img.naturalWidth,400/img.naturalHeight);canvas.width=Math.round(img.naturalWidth*scale);canvas.height=Math.round(img.naturalHeight*scale);
  const ctx=canvas.getContext('2d');ctx.drawImage(img,0,0,canvas.width,canvas.height);ctx.lineWidth=2;ctx.font='bold 14px Helvetica';
  const selected=new Set(chosen());
  proposal.candidates.forEach((c,n)=>{const [x,y,w,h]=c.box.map(v=>v*scale);ctx.strokeStyle=selected.has(c)?'#e5484d':'#1971c2';ctx.fillStyle=ctx.strokeStyle;ctx.strokeRect(x-2,y-2,w+4,h+4);ctx.fillRect(x,Math.max(0,y-17),24,17);ctx.fillStyle='white';ctx.fillText(String(n+1),x+3,Math.max(14,y-3));});
  $('command-apply').disabled=selected.size===0;
 }
 async function apply(candidates){
  if(fingerprint()!==baseline)throw Error('Снимок изменился. Повторите команду для актуальной разметки.');
  const state=structuredClone(editor.area.getState());
  if(state.markers.length+candidates.length>500)throw Error('Допускается до 500 пометок.');
  state.markers.push(...candidates.map(c=>c.marker));editor.area.restoreState(state,true);
  await editor.flush();status('Добавлено пометок: '+candidates.length+' · ⌘Z отменяет всю команду');
 }
 async function execute(text){
  if(running||voiceSession!==null)return;
  lock(true);
  try{
   editor.area.switchToSelectMode();await editor.flush();baseline=fingerprint();status('Ищу текст на снимке локально…');
   proposal=await call('visual_command',{text,base_revision:editor.doc.revision});
   if(fingerprint()!==baseline)throw Error('Пометки изменились во время поиска. Повторите команду.');
   if(!proposal.candidates.length){status(proposal.message);return;}
   if(!proposal.review){await apply(proposal.candidates);return;}
   $('command-title').textContent=proposal.op==='private'?'Проверьте области для скрытия':'Выберите нужную надпись';
   $('command-description').textContent=proposal.op==='private'?'Это подсказки OCR, а не полная проверка личных данных. Отметьте области, которые нужно закрасить.':'Номера на снимке соответствуют вариантам. Нечёткие совпадения требуют вашего выбора.';
   $('command-options').replaceChildren();
   proposal.candidates.forEach((candidate,n)=>{const label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');input.type='checkbox';input.value=n;input.checked=proposal.op==='private';input.onchange=preview;text.textContent=(n+1)+'. '+candidate.text+(candidate.exact?'':' (похожий текст)');label.append(input,text);$('command-options').append(label);});
   preview();$('command-dialog').showModal();status('Выберите области в окне просмотра');
  }catch(e){status(e.message||String(e));}finally{lock(false);}
 }
 $('command-form').onsubmit=e=>{e.preventDefault();execute($('command-input').value);};
 $('private-find').onclick=()=>execute('Скрыть личное');
 $('command-cancel').onclick=()=>$('command-dialog').close();
 $('command-apply').onclick=async()=>{lock(true);$('command-apply').disabled=true;try{await apply(chosen());$('command-dialog').close();}catch(e){status(e.message);$('command-dialog').close();}finally{lock(false);}};
 function voiceReset(){voiceSession=null;voicePending=false;$('command-mic').textContent='Продиктовать';lock(running);}
 core.on('visual_dictation',event=>{
  if(event.asset_id!==editor.doc.asset_id)return;
  if(event.state==='recording'){voiceSession=event.session;$('command-mic').textContent='Остановить диктовку';status('Говорите команду. Затем остановите диктовку и нажмите «Выполнить».');lock(running);return;}
  if(event.session!==voiceSession)return;
  if(event.state==='partial'&&event.text){$('command-input').value=($('command-input').value+' '+event.text).trim().slice(0,2000);}
  if(event.state==='finishing'){$('command-mic').textContent='Распознаю…';voicePending=true;lock(running);}
  if(event.state==='done'||event.state==='cancelled'){voiceReset();status('Диктовка завершена. Проверьте команду и нажмите «Выполнить».');}
 });
 $('command-mic').onclick=async()=>{
  if(voicePending||running)return;voicePending=true;lock(running);
  try{
   if(voiceSession===null){voiceStart=call('visual_dictation_start');const r=await voiceStart;voiceSession=r.session;$('command-mic').textContent='Остановить диктовку';}
   else {const r=await call('visual_dictation_finish',{session:voiceSession});if(!r.ok)voiceReset();}
  }catch(e){status(e.message);}finally{voicePending=false;voiceStart=null;lock(running);}
 };
 editor.stopVoice=async()=>{if(voiceStart)await voiceStart.catch(()=>{});if(voiceSession!==null){await call('visual_dictation_cancel',{session:voiceSession});voiceReset();}};
 core.on('offline',()=>{voiceReset();status('Связь с ядром потеряна. Диктовка в ядре ограничена двумя минутами.');});
 lock(false);
 editor.executeCommand=execute;
};
