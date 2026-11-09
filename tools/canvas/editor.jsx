import React from 'react';
import {installCanvasChat} from './chat.js';
import {createRoot} from 'react-dom/client';
import {Excalidraw,MainMenu,exportToBlob,convertToExcalidrawElements,CaptureUpdateAction,getCommonBounds} from '@excalidraw/excalidraw';
import '@excalidraw/excalidraw/index.css';
import {parseMermaidToExcalidraw} from '@excalidraw/mermaid-to-excalidraw';
import {installPalette,installSelection,selectionPatch} from './selection.js';
import {localImageElement,localEllipseLabel} from './local-elements.js';

const $=id=>document.getElementById(id),core=window.core;
const keys=['viewBackgroundColor','gridSize','gridStep','gridModeEnabled','currentItemStrokeColor','currentItemBackgroundColor','currentItemFillStyle','currentItemStrokeWidth','currentItemStrokeStyle','currentItemRoughness','currentItemOpacity','currentItemFontFamily','currentItemFontSize','currentItemTextAlign','currentItemStartArrowhead','currentItemEndArrowhead','currentItemRoundness'];
let api,doc,reactRoot,library=[],saved='',timer,pending=null,loaded=false,busy=false,transfer=null,known=new Set(),started=false,initializing=null,clipboardReading=false;
let chat;
const status=text=>$('status').textContent=text;
const call=(cmd,args={})=>core.call(cmd,{canvas_id:doc?.id,...args});
const scene=()=>({elements:api.getSceneElementsIncludingDeleted(),appState:Object.fromEntries(keys.filter(k=>api.getAppState()[k]!==undefined).map(k=>[k,api.getAppState()[k]])),libraryItems:library});
const snapshot=()=>JSON.stringify({scene:scene(),title:$('title').value});
function changed(){if(!loaded)return;chat?.changed();clearTimeout(timer);if(snapshot()===saved)return;status('Сохраняю…');timer=setTimeout(()=>flush().catch(e=>status(e.message)),700);}
function controls(){document.querySelectorAll('#actions button,#actions select,#outputs button,#title,#fit').forEach(b=>b.disabled=!loaded||busy||clipboardReading);$('cancel-upload').hidden=!transfer;for(const id of ['images','paste-image','import','new','documents'])$(id).disabled=busy||clipboardReading||!core.connected;chat?.setEnabled(!busy&&!clipboardReading&&core.connected);}
function base64(bytes){let text='';for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(text);}
async function upload(blob,purpose,extra={}){
 if(blob.size>64*1024*1024)throw Error('Файл больше 64 МиБ');
 const bytes=new Uint8Array(await blob.arrayBuffer());const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].map(n=>n.toString(16).padStart(2,'0')).join('');
 const start=await call('canvas_upload_begin',{purpose,size:bytes.length,sha256:hash,...extra});
 const job={id:start.upload_id,cancel:false};transfer=job;controls();
 try{
  for(let offset=0;offset<bytes.length;offset+=524288){if(job.cancel)throw Error('Загрузка отменена');await call('canvas_upload_chunk',{upload_id:job.id,offset,data:base64(bytes.subarray(offset,offset+524288))});status('Передаю файл: '+Math.round(Math.min(bytes.length,offset+524288)/bytes.length*100)+'%');}
  if(job.cancel)throw Error('Загрузка отменена');return await call('canvas_upload_finish',{upload_id:job.id});
 }catch(e){await call('canvas_upload_cancel',{upload_id:job.id}).catch(()=>{});throw e;}finally{if(transfer===job)transfer=null;controls();}
}
async function persistFiles(){
 for(const file of Object.values(api.getFiles())){
  if(known.has(file.id))continue;
  if(!/^data:image\/(png|jpeg|webp);base64,/.test(file.dataURL))throw Error('На холст можно добавить PNG, JPEG или WebP');
  await upload(await(await fetch(file.dataURL)).blob(),'image',{file_id:file.id});known.add(file.id);
 }
}
async function flush(){
 clearTimeout(timer);if(!api||!loaded)return;
 if(pending){await pending;return flush();}
 pending=(async()=>{await persistFiles();const value=snapshot();if(value===saved)return;const data=JSON.parse(value);const r=await call('canvas_save',{base_revision:doc.revision,run_id:doc.applyRunId,...data});doc.revision=r.revision;delete doc.applyRunId;saved=value;status('Сохранено');})();
 try{await pending;}finally{pending=null;}
 if(snapshot()!==saved)return flush();
}
async function list(){const rows=await core.call('canvas_list');$('documents').replaceChildren(new Option('Сохранённые холсты',''));for(const r of rows)$('documents').append(new Option(r.title,r.id));$('documents').value=doc?.id||'';}
async function navigate(id){if(busy||clipboardReading)throw Error('Дождитесь завершения операции');await flush();if(id===doc?.id)return;const target=new URL(location.href);target.searchParams.set('id',id);location.href=target.href;}
window.addEventListener('canvas-open',e=>navigate(e.detail).catch(e=>status(e.message)));
$('new').onclick=async()=>{try{await flush();const next=await core.call('canvas_create',{title:'Новый холст'});await navigate(next.id);}catch(e){status(e.message);}};
$('documents').onchange=()=>{if($('documents').value)navigate($('documents').value).catch(e=>status(e.message));};
$('title').oninput=changed;
$('fit').onclick=()=>{if(api?.getSceneElements().length)api.scrollToContent(api.getSceneElements(),{fitToContent:true});};
$('cancel-upload').onclick=()=>{if(transfer)transfer.cancel=true;};
function center(){const state=api.getAppState();return {x:-state.scrollX+state.width/(2*state.zoom.value),y:-state.scrollY+state.height/(2*state.zoom.value)};}
async function addImages(files){
 if(busy){status('Дождитесь завершения текущей операции');return;}if(!files.length)return;busy=true;controls();
 try{
  await ensureDocument();await flush();let offset=0;const point=center();let last=[];
  for(const file of files){
   const item=await upload(file,'image',{name:file.name});const data=await call('canvas_file',{file_id:item.id});known.add(item.id);api.addFiles([data]);
   const elements=last=[localImageElement({fileId:item.id,x:point.x+offset,y:point.y+offset,width:item.width,height:item.height})];
   api.updateScene({elements:[...api.getSceneElementsIncludingDeleted(),...elements],captureUpdate:CaptureUpdateAction.IMMEDIATELY});offset+=50;
  }
  api.updateScene({appState:selectionPatch(api.getSceneElements(),last.map(e=>e.id)),captureUpdate:CaptureUpdateAction.NEVER});
  await flush();api.scrollToContent(api.getSceneElements(),{fitToContent:true});status('Изображения добавлены без уменьшения');
 }catch(e){status(e.message);}finally{busy=false;controls();}
}
$('images').onclick=()=>$('image-files').click();$('image-files').onchange=()=>{addImages([...$('image-files').files]);$('image-files').value='';};
const editingText=e=>e.composedPath().some(n=>n.tagName==='INPUT'||n.tagName==='TEXTAREA'||n.isContentEditable);
async function pasteImage(){
 if(clipboardReading||busy){status('Дождитесь завершения текущей операции');return;}
 clipboardReading=true;controls();
 try{
  const native=window.api?.();let files=[];
  if(native?.clipboard_image){
   const url=await native.clipboard_image();
   if(url){if(!/^data:image\/(png|jpeg|webp);base64,/.test(url))throw Error('Формат картинки в буфере не поддерживается');files=[await(await fetch(url)).blob()];}
  }else if(navigator.clipboard?.read){
   const items=await navigator.clipboard.read();
   for(const item of items){const type=item.types.find(t=>['image/png','image/jpeg','image/webp'].includes(t));if(type)files.push(await item.getType(type));}
  }else throw Error('Вставьте картинку сочетанием ⌘V / Ctrl+V или выберите файл');
  if(files.length)await addImages(files);else status('В буфере нет картинки. Скопируйте само изображение или выберите файл.');
 }catch(e){status('Не удалось вставить картинку: '+e.message);}finally{clipboardReading=false;controls();}
}
$('paste-image').onclick=pasteImage;
document.addEventListener('paste',e=>{
 const files=[...(e.clipboardData?.files||[])];
 if(editingText(e)&&!(e.target.id==='canvas-chat-input'&&files.length))return;
 if(files.length){e.preventDefault();e.stopImmediatePropagation();addImages(files);}
 else if(!e.clipboardData?.getData('text/plain')&&window.api?.()?.clipboard_image){e.preventDefault();e.stopImmediatePropagation();pasteImage();}
},true);
// On macOS Ctrl+V is not the system paste shortcut. Support it explicitly;
// Cmd+V and the Edit menu keep their native paste event and text editing.
document.addEventListener('keydown',e=>{
 if(e.ctrlKey&&!e.metaKey&&!e.altKey&&e.code==='KeyV'&&!editingText(e)&&window.api?.()?.clipboard_image){e.preventDefault();e.stopImmediatePropagation();if(!e.repeat)pasteImage();}
},true);
document.addEventListener('dragover',e=>{if(e.dataTransfer?.types.includes('Files'))e.preventDefault();},true);
document.addEventListener('drop',e=>{const files=[...(e.dataTransfer?.files||[])];if(files.length){e.preventDefault();e.stopImmediatePropagation();addImages(files);}},true);
$('instruction').onclick=()=>{api.updateScene({appState:{currentItemStrokeColor:'#e5484d',currentItemStrokeWidth:2,currentItemRoughness:0,currentItemBackgroundColor:'transparent',currentItemFontFamily:2},captureUpdate:CaptureUpdateAction.IMMEDIATELY});changed();status('Стиль «Инструкция»: красный, 2 px, ровные фигуры');};
$('number').onclick=()=>{
 const n=Math.max(0,...api.getSceneElements().map(e=>e.customData?.dewiarNumber||0))+1,p=center(),group=crypto.randomUUID();
 const elements=convertToExcalidrawElements([localEllipseLabel({text:n,x:p.x,y:p.y,customData:{dewiarNumber:n}})]);
 elements.forEach(e=>{e.groupIds=[group];e.customData={dewiarNumber:n};});api.updateScene({elements:[...api.getSceneElementsIncludingDeleted(),...elements],appState:selectionPatch(elements,elements.map(e=>e.id)),captureUpdate:CaptureUpdateAction.IMMEDIATELY});changed();
};
function blocks(){const all=api.getSceneElementsIncludingDeleted();return all.filter(e=>e.customData?.mermaidSource&&all.some(other=>!other.isDeleted&&other.customData?.mermaidBlock===e.customData.mermaidBlock));}
$('mermaid').onclick=()=>{$('diagram-block').replaceChildren(new Option('Добавить новую',''));blocks().forEach((e,i)=>$('diagram-block').append(new Option('Схема '+(i+1),e.customData.mermaidBlock)));$('diagram-error').textContent='';$('diagram-dialog').showModal();};
$('diagram-block').onchange=()=>{const e=blocks().find(e=>e.customData.mermaidBlock===$('diagram-block').value);if(e)$('diagram-source').value=e.customData.mermaidSource;};
$('diagram-close').onclick=()=>$('diagram-dialog').close();
async function insertMermaid(source,blockId=''){
 if(source.length>20000||!/^\s*(flowchart|graph)\s+(TD|TB|LR|RL|BT)\b/.test(source))throw Error('Используйте Mermaid flowchart LR или TD, до 20 000 знаков.');
 if(/%%\{|\bclick\s|<\s*(script|iframe|img|a)\b/i.test(source))throw Error('В схеме нельзя использовать настройки, внешние ссылки или HTML-вставки.');
 const before=api.getSceneElementsIncludingDeleted(),stamp=JSON.stringify(before);
 const parsed=await parseMermaidToExcalidraw(source,{startOnLoad:false,maxEdges:200,maxTextSize:20000});
 if(parsed.files&&Object.keys(parsed.files).length)throw Error('Этот тип схемы превращается в картинку. Используйте блоки и стрелки flowchart.');
 if(JSON.stringify(api.getSceneElementsIncludingDeleted())!==stamp)throw Error('Холст изменился. Повторите построение схемы.');
 const converted=convertToExcalidrawElements(parsed.elements);
 if(!converted.length)throw Error('В схеме нет объектов');
 const old=before.filter(e=>e.customData?.mermaidBlock===blockId);const id=blockId||crypto.randomUUID();
 const point=old.length?{x:Math.min(...old.map(e=>e.x)),y:Math.min(...old.map(e=>e.y))}:center();
 const left=Math.min(...converted.map(e=>e.x)),top=Math.min(...converted.map(e=>e.y));
 converted.forEach((e,i)=>{e.x+=point.x-left;e.y+=point.y-top;e.customData={mermaidBlock:id,...(i===0?{mermaidSource:source}:{})};});
 api.updateScene({elements:[...before.filter(e=>!blockId||e.customData?.mermaidBlock!==blockId),...converted],appState:selectionPatch(converted,converted.map(e=>e.id)),captureUpdate:CaptureUpdateAction.IMMEDIATELY});await flush();api.scrollToContent(converted,{fitToContent:true});return converted;
}
$('diagram-apply').onclick=async()=>{$('diagram-apply').disabled=true;try{await insertMermaid($('diagram-source').value,$('diagram-block').value);$('diagram-dialog').close();}catch(e){$('diagram-error').textContent=e.message;}finally{$('diagram-apply').disabled=false;}};
async function output(command,args={}){
 if(busy||!loaded)return;busy=true;controls();
 try{
  await flush();const revision=doc.revision,stamp=snapshot(),elements=api.getSceneElements();if(!elements.length)throw Error('На холсте пока нет объектов');
  const [x1,y1,x2,y2]=getCommonBounds(elements);if((x2-x1+40)*(y2-y1+40)>50_000_000)throw Error('Экспорт больше 50 мегапикселей. Сблизьте объекты или разделите холст.');
  status('Готовлю PNG…');const blob=await exportToBlob({elements,appState:{...api.getAppState(),exportBackground:true,exportEmbedScene:false,exportScale:1},files:api.getFiles(),mimeType:'image/png',exportPadding:20});
  await flush();if(doc.revision!==revision||snapshot()!==stamp)throw Error('Холст изменился во время экспорта. Повторите.');
  await upload(blob,'export',{revision});const r=await call(command,args);status(command==='canvas_copy'?'PNG скопирован':'Готово: '+(r.path||r.task_id||''));return r;
 }catch(e){status(e.message);throw e;}finally{busy=false;controls();}
}
$('copy').onclick=()=>output('canvas_copy').catch(()=>{});$('save-as').onclick=()=>output('canvas_save_as').catch(()=>{});$('note').onclick=()=>output('canvas_to_note').catch(()=>{});
$('document').onclick=async()=>{try{await flush();const r=await call('canvas_document');status('Редактируемый исходник сохранён: '+r.path);}catch(e){status(e.message);}};
$('task').onclick=async()=>{try{const rows=await call('canvas_tasks');$('task-target').replaceChildren(new Option('Новая задача',''));rows.forEach(r=>$('task-target').append(new Option(r.title,r.id)));$('task-dialog').showModal();}catch(e){status(e.message);}};
$('task-close').onclick=()=>$('task-dialog').close();$('task-send').onclick=()=>{$('task-dialog').close();output('canvas_to_task',{task_id:$('task-target').value,text:$('task-text').value}).catch(()=>{});};
$('annotations').onchange=()=>{const id=$('annotations').value;if(!id)return;$('annotations').selectedIndex=0;if(window.api?.()?.open_visual_editor)window.api().open_visual_editor('screenshot',id);else{const target=new URL('visual-editor.html',location.href);target.search=location.search;target.searchParams.set('id',id);window.open(target.href);}};
$('import').onclick=()=>$('document-file').click();
$('document-file').onchange=async()=>{
 const file=$('document-file').files[0];$('document-file').value='';if(!file||busy)return;
 const previous=doc,wasLoaded=loaded;let nextId='';
 busy=true;controls();
 try{
  if(file.size>64*1024*1024)throw Error('Исходник больше 64 МиБ');const value=JSON.parse(await file.text());if(value.type!=='excalidraw'||!Array.isArray(value.elements))throw Error('Нужен файл .excalidraw');
  await flush();loaded=false;clearTimeout(timer);$('canvas-root').style.pointerEvents='none';
  const next=await core.call('canvas_create',{title:file.name.replace(/\.excalidraw$/i,'')});doc=next;
  for(const asset of Object.values(value.files||{})){if(!/^data:image\/(png|jpeg|webp);base64,/.test(asset.dataURL))throw Error('Исходник содержит неподдерживаемую картинку');await upload(await(await fetch(asset.dataURL)).blob(),'image',{file_id:asset.id});}
  await call('canvas_save',{base_revision:0,scene:{elements:value.elements,appState:value.appState||{},libraryItems:[]},title:next.title});nextId=next.id;
 }catch(e){status(e.message);}finally{doc=previous;loaded=wasLoaded;busy=false;$('canvas-root').style.pointerEvents='';controls();}
 if(nextId)await navigate(nextId);
};
window.canvasRequestClose=()=>{
 if(transfer){transfer.cancel=true;status('Дождитесь отмены загрузки, затем закройте окно');return false;}if(busy||clipboardReading){status('Дождитесь завершения операции');return false;}if(!api||!loaded)return true;
 flush().then(()=>window.api?.()?.close_canvas?.()).catch(e=>{$('close-error').textContent=e.message;$('close-dialog').showModal();});return false;
};
$('close-retry').onclick=()=>{$('close-dialog').close();window.canvasRequestClose();};$('close-stay').onclick=()=>$('close-dialog').close();$('close-discard').onclick=()=>{clearTimeout(timer);loaded=false;window.api?.()?.close_canvas?.();};
window.addEventListener('beforeunload',e=>{if(loaded&&(pending||snapshot()!==saved)){e.preventDefault();e.returnValue='';}});
core.on('canvas_changed',e=>{if(loaded&&doc?.id===e.id&&e.revision!==doc.revision){if(!busy&&!clipboardReading&&!pending&&snapshot()===saved)location.reload();else status('Холст дополнен из другого окна. Сохраните свою копию перед перезагрузкой.');}});
async function ensureDocument(){
 if(!core.connected)throw Error('Нет связи с ядром. Дождитесь подключения и повторите вставку.');
 if(initializing)await initializing;
 if(loaded)return;
 if(new URLSearchParams(location.search).get('id'))throw Error('Холст не загружен. Откройте его заново.');
 const next=await core.call('canvas_create',{title:'Новый холст'});
 const url=new URL(location.href);url.searchParams.set('id',next.id);history.replaceState(null,'',url);
 await mountDocument(next.id);await list();
}
async function mountDocument(id){
  doc=await core.call('canvas_open',{canvas_id:id});$('title').value=doc.title;$('documents').value=id;library=doc.libraryItems||[];const files={};
  for(const item of Object.values(doc.files)){files[item.id]=await call('canvas_file',{file_id:item.id});known.add(item.id);}
  let resolve;const ready=new Promise(r=>resolve=r);reactRoot=createRoot($('canvas-root'));
  reactRoot.render(<Excalidraw langCode="ru-RU" initialData={{elements:doc.elements,appState:doc.appState,files,libraryItems:library,scrollToContent:true}} excalidrawAPI={value=>{api=value;resolve();}} onChange={(_elements,state)=>{chat.selectionChanged(state);changed();}} onLibraryChange={items=>{library=items;changed();}} handleKeyboardGlobally={false} AIEnabled={false} onLinkOpen={(element,event)=>event.preventDefault()} validateEmbeddable={()=>false} UIOptions={{tools:{image:false},canvasActions:{loadScene:false,export:false,saveToActiveFile:false,saveAsImage:false,toggleTheme:false}}}>
   <MainMenu><MainMenu.DefaultItems.ClearCanvas/><MainMenu.DefaultItems.ChangeCanvasBackground/></MainMenu>
  </Excalidraw>);
  await ready;installSelection(api);installPalette(api,status);await new Promise(r=>setTimeout(r,150));loaded=true;if(api.getSceneElements().length)api.scrollToContent(api.getSceneElements(),{fitToContent:true});saved=snapshot();controls();$('annotations').hidden=!doc.sources.length;for(const source of new Map(doc.sources.map(s=>[s.screenshot_id,s])).values())$('annotations').append(new Option(source.title,source.screenshot_id));status('Сохранено · вставка ⌘V, файлы и схемы работают локально');
  window.canvasEditor={get api(){return api},get doc(){return doc},flush,output,upload,addImages,insertMermaid,get library(){return library}};
  await chat.mount();
}
async function start(){
 if(started)return initializing;started=true;controls();
 initializing=(async()=>{
  const id=new URLSearchParams(location.search).get('id');await list();
  if(id)await mountDocument(id);else status('Вставьте картинку или выберите файл — холст создастся автоматически');
 })();
 try{await initializing;}catch(e){status('Не удалось открыть холст: '+e.message);console.error(e);}finally{controls();}
}
chat=installCanvasChat({get api(){return api},get doc(){return doc},call,flush,scene,async run(fn){if(busy||clipboardReading)throw Error('Дождитесь завершения текущей операции');busy=true;controls();try{await ensureDocument();await flush();return await fn();}finally{busy=false;controls();}}});
core.on('hello',()=>{start();chat.profiles().catch(e=>status(e.message));});if(core.snapshot){start();chat.profiles().catch(e=>status(e.message));}
