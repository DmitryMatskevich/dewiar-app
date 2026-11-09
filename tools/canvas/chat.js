import {activeIds,selectionPatch} from './selection.js';
import {CaptureUpdateAction,convertToExcalidrawElements,getCommonBounds} from '@excalidraw/excalidraw';
import {localElement} from './local-elements.js';
const $=id=>document.getElementById(id);
export function installCanvasChat(host){
 let scopeTimer,scopeRequest=null,stopped=false,lastText='';
 let pending=null,stamp='',running=false,refreshTimer,enabled=false,objects=[];
 const log=$('canvas-chat-log');
 function line(role,text){const el=document.createElement('p');el.className='chat-message '+role;const label=document.createElement('strong');label.textContent=role==='user'?'Вы':'Холст';const body=document.createElement('span');body.textContent=text;el.append(label,body);log.append(el);log.scrollTop=log.scrollHeight;}
 async function say(role,text){line(role,text);await host.call('canvas_chat_log',{role,text:text.slice(0,4000)});}
 function stateStamp(){return JSON.stringify({elements:host.api.getSceneElementsIncludingDeleted(),background:host.api.getAppState().viewBackgroundColor});}
 function controls(){for(const id of ['canvas-chat-send','canvas-chat-input','canvas-chat-apply','canvas-chat-dismiss'])$(id).disabled=!enabled||running;}
 function showProposal(answer){
  pending=answer;host.call('canvas_chat_pending',{pending:{answer,stamp,text:lastText}}).catch(()=>{});$('canvas-chat-proposal').hidden=false;
  const names={outline:'Обвести',underline:'Подчеркнуть',delete:'Удалить',move:'Переместить',color:'Изменить цвет',fill:'Закрасить',text:'Добавить текст',background:'Изменить фон',select:'Выбрать'};
  $('canvas-chat-changes').replaceChildren();
  for(const op of answer.operations){const row=document.createElement('li');row.textContent=(names[op.op]||op.op)+(op.target?' · '+op.target.ids.map(id=>'#'+objects.find(e=>e.id===id)?.number).join(', '):'')+(op.color?' · '+op.color:'')+(op.text?' · '+op.text:'')+(op.x!==undefined?` · (${op.x}, ${op.y})`:'');$('canvas-chat-changes').append(row);}
  $('canvas-chat-candidates').replaceChildren();
  if(answer.ocr){
   const heading=document.createElement('p');heading.textContent=`Найдено совпадений: ${answer.ocr.candidates.length}. ${answer.ocr.op==='cover'?'Закрасить надпись':'Выберите надпись'}`;$('canvas-chat-candidates').append(heading);
   const option=(value,text,checked)=>{const label=document.createElement('label'),input=document.createElement('input');input.type='radio';input.name='ocr-candidate';input.value=value;input.checked=checked;label.append(input,document.createTextNode(text));$('canvas-chat-candidates').append(label);};
   for(const [i,c] of answer.ocr.candidates.entries())option(String(i),`${i+1}. ${c.context||c.text} · X ${Math.round(c.x)}, Y ${Math.round(c.y)}`,i===0);
   if(answer.ocr.candidates.length>1)option('all',`Все совпадения (${answer.ocr.candidates.length})`,false);
  }
  $('canvas-chat-proposal').scrollIntoView({block:'nearest'});
 }
 async function refresh(){
  if(!host.doc||running)return;await host.flush();const data=await host.call('canvas_chat_state');objects=data.objects;const selected=host.api.getAppState().selectedElementIds||{};
  const list=$('canvas-object-list');list.replaceChildren();
  for(const e of objects){const button=document.createElement('button');button.type='button';button.className='canvas-object';button.textContent=`#${e.number} · ${e.text||({image:'Картинка',rectangle:'Прямоугольник',ellipse:'Овал',arrow:'Стрелка',text:'Текст',line:'Линия',freedraw:'Кисть'}[e.type]||e.type)}`;button.title=`X ${Math.round(e.x)}, Y ${Math.round(e.y)} · ${Math.round(e.width)}×${Math.round(e.height)}`;button.setAttribute('aria-pressed',String(!!selected[e.id]));button.dataset.elementId=e.id;button.onclick=event=>{
   const all=host.api.getSceneElements(),old=activeIds(host.api.getAppState()),target=all.find(x=>x.id===e.id);
   if(!target||target.locked)return;
   const group=selectionPatch(all,[e.id]).selectedElementIds;
   const ids=old.includes(e.id)?old.filter(id=>!group[id]):event.shiftKey?[...old,e.id]:[e.id];
   host.api.updateScene({appState:selectionPatch(all,ids),captureUpdate:CaptureUpdateAction.NEVER});
   if(!old.includes(e.id))host.api.scrollToContent([target],{fitToContent:false});
  };list.append(button);}
  $('canvas-object-count').textContent=String(objects.length);
 }
 function modify(answer){
  if(stopped)throw Error('Запрос остановлен.');
  if(stateStamp()!==stamp)throw Error('Холст изменился после запроса. Повторите команду.');
  let elements=structuredClone(host.api.getSceneElementsIncludingDeleted()),appState={};
  let created=[];
  const add=spec=>{const newElements=convertToExcalidrawElements([spec],{regenerateIds:false});elements.push(...newElements);created=newElements.map(e=>e.id);};
  const updated=e=>({...e,version:e.version+1,versionNonce:Math.floor(Math.random()*2147483647),updated:Date.now()});
  let ops=answer.operations;
  if(answer.ocr){const value=(pending===answer?document.querySelector('#canvas-chat-candidates input:checked')?.value:null)||'0';const candidates=value==='all'?answer.ocr.candidates:[answer.ocr.candidates[Number(value)]];ops=candidates.map(c=>({op:answer.ocr.op,color:c.color||answer.ocr.color,box:[c.x,c.y,c.x+c.width,c.y+c.height],shape:c.shape,corners:c.corners,imageId:c.image_id}));}
  for(const op of ops){
   if(op.op==='background'){appState.viewBackgroundColor=op.color;continue;}
   if(op.op==='text'){add({type:'text',text:op.text,x:op.x,y:op.y,fontSize:24,fontFamily:2,strokeColor:op.color});continue;}
   let ids=new Set(op.target?.ids||[]),targets=elements.filter(e=>ids.has(e.id)&&!e.isDeleted);
   if(op.op!=='select_clear'&&!op.box&&!targets.length)throw Error('Цель команды больше не существует.');
   if(targets.some(e=>e.locked)&&!op.op.startsWith('select'))throw Error('Объект заблокирован.');
   if(['select','select_add','select_remove','select_clear'].includes(op.op)){
    const old=activeIds({...host.api.getAppState(),...appState});
    const group=selectionPatch(elements,[...ids]).selectedElementIds;
    Object.assign(appState,selectionPatch(elements,op.op==='select_clear'?[]:op.op==='select_add'?[...old,...ids]:op.op==='select_remove'?old.filter(id=>!group[id]):[...ids]));
    created=[];continue;
   }
   if(op.op==='cover'){
    const image=elements.find(e=>e.id===op.imageId&&!e.isDeleted);if(!image||image.locked)throw Error('Картинка отсутствует или заблокирована.');
    const mask=[localElement({type:'rectangle',...op.shape,strokeColor:op.color,backgroundColor:op.color,strokeWidth:0,fillStyle:'solid',roughness:0,opacity:100,customData:{dewiarTextCover:true}})];
    // Insert directly above the raster so existing captions/marks stay visible.
    elements.splice(elements.indexOf(image)+1,0,...mask);created=mask.map(e=>e.id);continue;
   }
   if(op.op==='outline'||op.op==='underline'){
    if(op.shape){
     if(op.op==='outline')add({type:'rectangle',...op.shape,x:op.shape.x-6,y:op.shape.y-6,width:op.shape.width+12,height:op.shape.height+12,strokeColor:op.color,strokeWidth:3,roughness:0,backgroundColor:'transparent'});
     else {const a=op.corners[3],b=op.corners[2],offset=[-Math.sin(op.shape.angle)*3,Math.cos(op.shape.angle)*3];add({type:'line',x:a[0]+offset[0],y:a[1]+offset[1],points:[[0,0],[b[0]-a[0],b[1]-a[1]]],strokeColor:op.color,strokeWidth:3,roughness:0});}continue;
    }
    const [x,y,x2,y2]=op.box||getCommonBounds(targets);
    add(op.op==='outline'?{type:'rectangle',x:x-6,y:y-6,width:x2-x+12,height:y2-y+12,strokeColor:op.color,strokeWidth:3,roughness:0,backgroundColor:'transparent'}:{type:'line',x,y:y2+3,points:[[0,0],[x2-x,0]],strokeColor:op.color,strokeWidth:3,roughness:0});continue;
   }
   // Preserve group members and bound labels when moving/deleting a container.
   if(['move','delete'].includes(op.op)){
    const groups=new Set(targets.flatMap(e=>e.groupIds||[]));
    elements.forEach(e=>{if((e.groupIds||[]).some(g=>groups.has(g))||ids.has(e.containerId))ids.add(e.id);});targets=elements.filter(e=>ids.has(e.id)&&!e.isDeleted);
    if(targets.some(e=>e.locked))throw Error('В группе есть заблокированный объект.');
    if(op.op==='move'&&elements.some(e=>!ids.has(e.id)&&((e.startBinding&&ids.has(e.startBinding.elementId))||(e.endBinding&&ids.has(e.endBinding.elementId)))))throw Error('К объекту привязана внешняя стрелка. Переместите его вручную, чтобы сохранить связь.');
   }
   if(op.op==='move'){
    const [x,y]=getCommonBounds(targets);elements=elements.map(e=>ids.has(e.id)?updated({...e,x:e.x+op.x-x,y:e.y+op.y-y}):e);
   }else if(op.op==='delete'){
    elements=elements.map(e=>ids.has(e.id)?updated({...e,isDeleted:true}):updated({...e,boundElements:e.boundElements?.filter(b=>!ids.has(b.id))||null,startBinding:ids.has(e.startBinding?.elementId)?null:e.startBinding,endBinding:ids.has(e.endBinding?.elementId)?null:e.endBinding}));appState.selectedElementIds={};
   }else if(op.op==='color'||op.op==='fill'){
    if(targets.some(e=>e.type==='image'))throw Error('Цвет пикселей картинки этой командой не меняется. Выделите фигуру или подпись.');
    if(op.op==='fill'&&targets.some(e=>!['rectangle','ellipse','diamond'].includes(e.type)))throw Error('Заливка доступна для прямоугольника, овала или ромба.');
    elements=elements.map(e=>ids.has(e.id)?updated(op.op==='color'?{...e,strokeColor:op.color}:{...e,backgroundColor:op.color,fillStyle:'solid'}):e);
   }else throw Error('Неподдерживаемое действие.');
  }
  if(elements.length>2000)throw Error('На холсте допускается до 2000 объектов.');
  if(created.length)Object.assign(appState,selectionPatch(elements,created));
  return {elements,appState};
 }
 async function apply(answer){
  const update=modify(answer);
  // The server checks the final scene before it can enter the editor or its undo stack.
  await host.call('canvas_chat_validate',{base_revision:host.doc.revision,scene:{...host.scene(),elements:update.elements,appState:{...host.scene().appState,...update.appState}}});
  if(stateStamp()!==stamp)throw Error('Холст изменился. Повторите команду.');
  host.doc.applyRunId=crypto.randomUUID();host.api.updateScene({...update,captureUpdate:CaptureUpdateAction.IMMEDIATELY});await host.flush();pending=null;await host.call('canvas_chat_pending',{pending:null});$('canvas-chat-proposal').hidden=true;
  await host.call('canvas_scope_done');scopeRequest=null;
  await say('assistant',answer.ocr?.op==='cover'?'Готово. Надпись закрыта сплошной заливкой. Исходная картинка сохранена; «Отмени» вернёт надпись.':'Готово. Правки применены; ⌘Z / Ctrl+Z на холсте отменяет всю команду.');
 }
 async function execute(){
  if(running||!enabled)return;const text=$('canvas-chat-input').value.trim();if(!text)return;
  stopped=false;lastText=text;running=true;controls();$('canvas-chat-progress').textContent='Обрабатываю…';pending=null;$('canvas-chat-proposal').hidden=true;
  try{
   await host.run(async()=>{
    await host.call('canvas_scope_begin',{request_id:scopeRequest?.id});await say('user',text);$('canvas-chat-input').value='';stamp=stateStamp();
    const profile=null;$('canvas-model-settings').hidden=true;
    if(!profile&&/^(отмени(?: последнюю команду)?|верни(?: последнюю команду)?)$/i.test(text)){
     const root=document.querySelector('.excalidraw');root.focus();
     const mac=/Mac|iPhone|iPad/.test(navigator.platform);
     root.dispatchEvent(new KeyboardEvent('keydown',{key:'z',code:'KeyZ',metaKey:mac,ctrlKey:!mac,shiftKey:/^верни/i.test(text),bubbles:true,cancelable:true}));
     await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));await host.flush();
     await say('assistant',stateStamp()===stamp?'Нет действия для отмены или повтора.':'Готово. История холста обновлена.');return;
    }
    const answer=await host.call('canvas_chat_plan',{text,base_revision:host.doc.revision,selected_ids:Object.keys(host.api.getAppState().selectedElementIds||{}),auto_model:true});
    if(stopped)throw Error('Запрос остановлен.');
    if(stateStamp()!==stamp)throw Error('Холст изменился во время ответа. Повторите запрос.');
    if(answer.needs_model_setup)$('canvas-model-settings').hidden=false;
    if(!answer.operations.length&&!answer.ocr){await say('assistant',answer.reply);return;}
    if(answer.model_used||(answer.ocr&&(answer.ocr.candidates.length>1||answer.ocr.review))){await say('assistant',answer.reply);showProposal(answer);}
    else await apply(answer);
   });
  }catch(e){$('canvas-chat-input').value=text;if(host.doc)await say('assistant',e.message).catch(()=>{});else line('assistant',e.message);}
  finally{if(!pending){await host.call('canvas_scope_done').catch(()=>{});scopeRequest=null;}running=false;controls();$('canvas-chat-progress').textContent='';await refresh().catch(()=>{});if(pending){$('canvas-chat-apply').focus();$('canvas-chat-proposal').scrollIntoView({block:'nearest'});}else $('canvas-chat-input').focus();}
 }
 $('canvas-chat-form').onsubmit=e=>{e.preventDefault();execute();};
 $('canvas-chat-input').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();execute();}};
 $('canvas-chat-apply').onclick=async()=>{if(!pending||running)return;running=true;controls();try{await host.run(()=>apply(pending));}catch(e){await say('assistant',e.message);}finally{running=false;controls();refresh().catch(()=>{});}};
 $('canvas-chat-dismiss').onclick=()=>{host.call('canvas_chat_pending',{pending:null}).catch(()=>{});host.call('canvas_scope_done').catch(()=>{});scopeRequest=null;pending=null;$('canvas-chat-proposal').hidden=true;say('assistant','Предложение отменено. Холст не изменён.').catch(()=>{});};
 $('canvas-chat-toggle').onclick=()=>{const hidden=$('canvas-assistant').hidden; $('canvas-assistant').hidden=!hidden;$('canvas-chat-toggle').setAttribute('aria-expanded',String(hidden));if(hidden)$('canvas-chat-input').focus();};
 document.querySelectorAll('[data-canvas-example]').forEach(button=>button.onclick=()=>{$('canvas-chat-input').value=button.dataset.canvasExample;$('canvas-chat-input').focus();});
 $('canvas-stop').onclick=()=>{stopped=true;pending=null;$('canvas-chat-proposal').hidden=true;host.call('canvas_scope_stop').catch(()=>{});host.call('canvas_chat_pending',{pending:null}).catch(()=>{});};
 $('canvas-recalculate').onclick=async()=>{if(!running&&lastText){await host.call('canvas_scope_done');pending=null;scopeRequest=null;await host.call('canvas_chat_pending',{pending:null});$('canvas-chat-input').value=lastText;execute();}};
 $('canvas-model-settings').onclick=()=>{const native=window.api?.();if(native?.open_settings)native.open_settings('models');else line('assistant','Откройте настройки Dewiar → Модели → назначение «Изображения».');};
 window.addEventListener('pagehide',()=>{if(host.doc)host.call('canvas_scope_closed').catch(()=>{});});
 return {
  selectionChanged(state){const ids=state.selectedElementIds||{};for(const button of document.querySelectorAll('.canvas-object'))button.setAttribute('aria-pressed',String(!!ids[button.dataset.elementId]));},
  setEnabled(value){enabled=value;controls();},
  changed(){clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refresh().catch(()=>{}),1000);},
  async mount(){
   clearInterval(scopeTimer);
   const poll=async()=>{if(!host.doc)return;await host.call('canvas_scope_active');if(running||pending)return;const request=await host.call('canvas_scope_take');if(request){scopeRequest=request;$('canvas-chat-input').value=request.text;execute();}};
   await host.call('canvas_scope_active');scopeTimer=setInterval(()=>poll().catch(()=>{}),1000);
   log.replaceChildren();const data=await host.call('canvas_chat_state');for(const m of data.messages)line(m.role,m.text);if(data.pending){lastText=data.pending.text||'';stamp=data.pending.stamp;showProposal(data.pending.answer);}if(!data.messages.length)line('assistant','Я помогу изменить этот холст. Выделите объект или укажите его номер. Простые команды выполняются локально; модели Художника подключаются автоматически.');await refresh();},
  async profiles(){},
 };
}
