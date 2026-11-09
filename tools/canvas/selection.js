import {CaptureUpdateAction} from '@excalidraw/excalidraw';

export const idsOf=elements=>Object.fromEntries(elements.map(e=>[e.id,true]));
export const activeIds=state=>Object.keys(state.selectedElementIds||{}).filter(id=>state.selectedElementIds[id]);

export function selectionPatch(elements, ids){
 const wanted=new Set(ids),groups=new Set();
 for(const e of elements)if(wanted.has(e.id)&&!e.locked)for(const g of e.groupIds||[])groups.add(g);
 const selected=elements.filter(e=>!e.isDeleted&&!e.locked&&(wanted.has(e.id)||(e.groupIds||[]).some(g=>groups.has(g))));
 return {selectedElementIds:idsOf(selected),selectedGroupIds:Object.fromEntries([...groups].map(g=>[g,true])),editingGroupId:null};
}

export function installSelection(api){
 let before;
 api.onPointerDown((tool,state,event)=>{
  before=tool.type==='selection'&&event.button===0&&event.pointerType==='mouse'&&!event.shiftKey&&!event.altKey&&!event.ctrlKey&&!event.metaKey
   ?{ids:activeIds(api.getAppState()),x:event.clientX,y:event.clientY,pointer:event.pointerId}:null;
 });
 api.onPointerUp((tool,state,event)=>{
  const start=before;before=null;
  const brush=api.getAppState().newElement;
  if(brush?.type==='freedraw'&&event.button===0){
   requestAnimationFrame(()=>{
    const all=api.getSceneElements();
    if(!all.some(e=>e.id===brush.id))return;
    if(api.getAppState().activeTool.type==='freedraw')api.setActiveTool({type:'selection'});
    api.updateScene({appState:selectionPatch(all,[brush.id]),captureUpdate:CaptureUpdateAction.NEVER});
   });
   return;
  }
  if(!start||event.pointerId!==start.pointer||state.drag.hasOccurred||state.boxSelection.hasOccurred||state.resize.handleType||
     Math.hypot(event.clientX-start.x,event.clientY-start.y)>4)return;
  const hit=state.hit.element;
  if(!hit||!start.ids.includes(hit.id)||hit.locked)return;
  // Text and linear point editing keep their native double-click action.
  // A second ordinary click removes the object (or its group) from the selection.
  requestAnimationFrame(()=>{
   if(api.getAppState().editingTextElement||api.getAppState().editingLinearElement)return;
   const group=selectionPatch(api.getSceneElements(),[hit.id]).selectedElementIds;
   api.updateScene({appState:selectionPatch(api.getSceneElements(),start.ids.filter(id=>!group[id])),captureUpdate:CaptureUpdateAction.NEVER});
  });
 });
}

const palettes={
 'Базовая':['#1e1e1e','#ffffff','#868e96','#e03131','#f08c00','#ffd43b','#2f9e44','#1971c2','#7048e8','#c2255c'],
 'Пастельная':['#ffc9c9','#ffd8a8','#ffec99','#d8f5a2','#b2f2bb','#99e9f2','#a5d8ff','#d0bfff','#fcc2d7','#dee2e6'],
 'Контрастная':['#000000','#ffffff','#a50000','#b24c00','#ffee00','#00663b','#0056b3','#471e80','#8a1249','#00ffff'],
 'Для пометок':['#e5484d','#ff6b35','#ffb000','#ffd43b','#2f9e44','#0c8599','#1971c2','#7048e8','#495057','#ffffff'],
};

export function installPalette(api,status){
 const el=id=>document.getElementById(id),picker=el('palette-picker'),hex=el('palette-hex'),set=el('palette-set'),swatches=el('palette-swatches');
 const rgb=['r','g','b'].map(channel=>el('palette-'+channel));
 const sync=color=>{
  picker.value=hex.value=color.toLowerCase();
  rgb.forEach((input,i)=>input.value=parseInt(color.slice(i*2+1,i*2+3),16));
 };
 const syncFromRgb=()=>{
  if(rgb.some(input=>!input.value||!input.checkValidity()))return false;
  picker.value=hex.value='#'+rgb.map(input=>Number(input.value).toString(16).padStart(2,'0')).join('');
  hex.setCustomValidity('');return true;
 };
 const apply=color=>{
  if(!/^#[0-9a-f]{6}$/i.test(color)){hex.setCustomValidity('Укажите цвет в формате #1971c2');hex.reportValidity();return;}
  hex.setCustomValidity('');color=color.toLowerCase();sync(color);
  const state=api.getAppState(),ids=state.selectedElementIds||{},all=api.getSceneElementsIncludingDeleted(),fill=el('palette-target').value==='fill';
  const key=fill?'backgroundColor':'strokeColor',eligible=e=>!e.isDeleted&&!e.locked&&ids[e.id]&&(fill?['rectangle','ellipse','diamond'].includes(e.type):!['image','frame'].includes(e.type));
  const elements=all.map(e=>eligible(e)&&e[key]!==color?{...e,[key]:color,version:e.version+1,versionNonce:Math.floor(Math.random()*2147483647),updated:Date.now()}:e);
  const count=all.filter(eligible).length;
  if(activeIds(state).length&&!count){status('Это свойство недоступно для выделенных объектов');return;}
  api.updateScene({elements,appState:{[fill?'currentItemBackgroundColor':'currentItemStrokeColor']:color},captureUpdate:CaptureUpdateAction.IMMEDIATELY});
  status(count?'Цвет применён · объектов: '+count:'Цвет установлен для новых объектов');
 };
 for(const name of Object.keys(palettes))set.add(new Option(name,name));
 const render=()=>swatches.replaceChildren(...palettes[set.value].map(color=>{
  const button=document.createElement('button');button.type='button';button.style.backgroundColor=color;
  button.title=color;button.setAttribute('aria-label','Цвет '+color);button.onclick=()=>apply(color);return button;
 }));
 set.onchange=render;render();picker.oninput=()=>sync(picker.value);picker.onchange=()=>apply(picker.value);
 hex.oninput=()=>{hex.setCustomValidity('');if(/^#[0-9a-f]{6}$/i.test(hex.value))sync(hex.value);};
 rgb.forEach(input=>{input.oninput=syncFromRgb;input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();if(syncFromRgb())apply(hex.value);else input.reportValidity();}};});
 el('palette-apply').onclick=()=>{const invalid=rgb.find(input=>!input.value||!input.checkValidity());if(invalid){invalid.focus();invalid.reportValidity();return;}apply(hex.value.trim());};
 hex.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();apply(hex.value.trim());}};
}
