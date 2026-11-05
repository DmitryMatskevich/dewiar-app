/* marker.js 3.10.0 adapter; all image coordinates are source pixels. */
(() => {
const $=id=>document.getElementById(id), id=new URLSearchParams(location.search).get('id');
let area,img,doc,timer,pending=null,started=false,restoring=false,busy=false,saved='',url,active='',fitMode=true;
const status=t=>$('status').textContent=t;
const call=(cmd,args={})=>core.call(cmd,{asset_id:id,...args});
const current=()=>({state:area.getState(),crop:doc.crop});
function changed(){if(restoring||!area)return;clearTimeout(timer);timer=setTimeout(()=>flush().catch(e=>status(e.message)),700);status('Сохраняю…');}
async function flush(){
 clearTimeout(timer);if(pending){await pending;return flush();}
 const data=current(),serialized=JSON.stringify(data);if(serialized===saved)return;
 pending=(async()=>{const r=await call('visual_save',{...data,base_revision:doc.revision});doc.revision=r.revision;saved=serialized;status('Сохранено'+(doc.crop?' · PNG будет обрезан':''));})();
 try{await pending;}finally{pending=null;}
 if(JSON.stringify(current())!==saved)return flush();
}
function style(editor,type){
 if(!editor)return;const k=Math.max(1,doc.source.width/1600),c=$('color').value,w=Number($('width').value)*k;
 editor.strokeColor=c;editor.strokeWidth=w;
 if(type==='CoverMarker'){editor.fillColor='#000000';editor.opacity=1;editor.strokeWidth=0;}
 if(type==='HighlighterMarker'){editor.strokeColor='#ffd43b';editor.strokeWidth=20*k;editor.opacity=.4;}
 if(type==='TextMarker'){editor.color=c;editor.fontSize={value:24*k,units:'px',step:1};}
}
const specs=[['Выбор','select','v'],['Стрелка','ArrowMarker','a'],['Рамка','FrameMarker','r'],['Овал','EllipseFrameMarker','e'],['Маркер','HighlighterMarker','h'],['Кисть','FreehandMarker','b'],['Текст','TextMarker','t'],['Номер','number','n'],['Скрыть','CoverMarker','x']];
function tool(type){if(!area||busy)return;active=type;
 if(type==='select')area.switchToSelectMode();
 else if(type==='number'){
  const state=structuredClone(area.getState()),n=Math.max(0,...state.markers.map(m=>Number(String(m.notes||'').match(/^dewiar:number=(\d+)$/)?.[1])||0))+1,k=Math.max(1,doc.source.width/1600);
  state.markers.push({typeName:'TextMarker',text:n<=20?String.fromCodePoint(0x2460+n-1):String(n),color:$('color').value,fontFamily:'Helvetica, Arial, sans-serif',fontSize:{value:40*k,units:'px',step:1},left:doc.source.width*.2,top:doc.source.height*.2,width:90*k,height:60*k,rotationAngle:0,opacity:1,notes:'dewiar:number='+n});area.restoreState(state,true);changed();
 }else{area.createMarker(type);style(area.currentMarkerEditor,type);}
 document.querySelectorAll('[data-tool]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tool===type)));
}
specs.forEach(([label,type,key])=>{const b=document.createElement('button');b.textContent=label;b.title=label+' ('+key.toUpperCase()+')';b.dataset.tool=type;b.onclick=()=>tool(type);$('tools').append(b);});
$('color').onchange=$('width').onchange=()=>{style(area?.currentMarkerEditor,area?.currentMarkerEditor?.getState()?.typeName||active);changed();};
$('undo').onclick=()=>{area?.undo();changed();};$('redo').onclick=()=>{area?.redo();changed();};$('delete').onclick=()=>{area?.deleteSelectedMarkers();changed();};
$('fit').onclick=()=>{fitMode=true;area?.autoZoom();};$('full').onclick=()=>{fitMode=false;if(area)area.zoomLevel=1;};
new ResizeObserver(()=>{if(area&&fitMode)area.autoZoom();}).observe($('area'));
$('reset').onclick=()=>{if(!area)return;if(confirm('Убрать все пометки? Действие можно отменить.')){const s=structuredClone(area.getState());s.markers=[];area.restoreState(s,true);changed();}};
async function output(cmd,extra={}){
 if(busy||!area)return;busy=true;document.querySelectorAll('header button').forEach(b=>b.disabled=true);
 try{
  area.switchToSelectMode();await flush();const rev=doc.revision,state=area.getState();status('Готовлю PNG…');
  const r=new markerjs3.Renderer();r.targetImage=img;r.naturalSize=true;r.markersOnly=true;r.imageType='image/png';
  const overlay=await r.rasterize(state);
  if(overlay.length>12_000_000)throw Error('Слишком много пометок для одной передачи (предел 12 МБ). Уменьшите число штрихов.');
  await flush();
  if(doc.revision!==rev)throw Error('Пометки изменились во время экспорта — повторите');
  await call('visual_publish',{revision:rev,overlay});const result=await call(cmd,extra);status(cmd==='visual_copy'?'Скопировано с пометками': 'Готово: '+(result.path||result.task_id||''));return result;
 }catch(e){status(e.message||String(e));throw e;}finally{busy=false;document.querySelectorAll('header button').forEach(b=>b.disabled=false);}
}
function invoke(cmd,extra){output(cmd,extra).catch(()=>{});}
$('copy').onclick=()=>invoke('visual_copy');$('save-as').onclick=()=>invoke('visual_save_as');$('note').onclick=()=>invoke('visual_to_note');
$('task').onclick=async()=>{try{const rows=await call('visual_tasks');$('task-target').replaceChildren(new Option('Новая задача',''));for(const row of rows)$('task-target').append(new Option(row.title,row.id));$('task-dialog').showModal();}catch(e){status(e.message);}};
$('task-close').onclick=()=>$('task-dialog').close();$('task-send').onclick=()=>{$('task-dialog').close();invoke('visual_to_task',{task_id:$('task-target').value,text:$('task-text').value});};
$('to-canvas').onclick=async()=>{try{const rows=await core.call('canvas_list');$('canvas-target').replaceChildren(new Option('Новый холст',''));for(const row of rows)$('canvas-target').append(new Option(row.title,row.id));$('canvas-dialog').showModal();}catch(e){status(e.message);}};
$('canvas-cancel').onclick=()=>$('canvas-dialog').close();
$('canvas-send').onclick=async()=>{$('canvas-dialog').close();try{const result=await output('visual_to_canvas',{canvas_id:$('canvas-target').value});if(!result)return;if(window.api?.()?.open_canvas)await window.api().open_canvas(result.canvas_id);else{const target=new URL('canvas.html',location.href);target.search=location.search;target.searchParams.set('id',result.canvas_id);window.open(target.href);}}catch(e){status(e.message);}};
$('original').onclick=()=>{const dialog=document.createElement('dialog'),picture=document.createElement('img'),close=document.createElement('button');picture.src=url;picture.style.maxWidth='80vw';picture.style.maxHeight='75vh';close.textContent='Закрыть';close.onclick=()=>{dialog.close();dialog.remove();};dialog.append(picture,close);document.body.append(dialog);dialog.showModal();};
$('crop').onclick=()=>{
 if(!area)return;
 const selected=area.currentMarkerEditor?.getState();
 const rect=selected?.typeName==='FrameMarker'&&!selected.rotationAngle?{x:Math.round(selected.left),y:Math.round(selected.top),width:Math.round(selected.width),height:Math.round(selected.height)}:null;
 const c=rect||doc.crop||{x:0,y:0,width:doc.source.width,height:doc.source.height};$('crop-fields').replaceChildren();
 for(const [k,label] of [['x','Слева'],['y','Сверху'],['width','Ширина'],['height','Высота']]){const l=document.createElement('label');l.textContent=label;const i=document.createElement('input');i.type='number';i.id='crop-'+k;i.value=c[k];l.append(i);$('crop-fields').append(l);}$('crop-dialog').showModal();};
$('crop-apply').onclick=()=>{const c={};for(const k of ['x','y','width','height'])c[k]=Number($('crop-'+k).value);if(!Object.values(c).every(Number.isInteger)||c.x<0||c.y<0||c.width<2||c.height<2||c.x+c.width>doc.source.width||c.y+c.height>doc.source.height){status('Обрезка вне снимка');return;}doc.crop=c;$('crop-dialog').close();changed();};
$('crop-clear').onclick=()=>{doc.crop=null;$('crop-dialog').close();changed();};$('crop-close').onclick=()=>$('crop-dialog').close();
document.addEventListener('keydown',e=>{
 if($('command-dialog').open)return;
 if(e.composedPath().some(x=>x.tagName==='INPUT'||x.tagName==='TEXTAREA'||x.isContentEditable)||!area)return;
 if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='z'){e.preventDefault();e.shiftKey?area.redo():area?.undo();changed();return;}
 if(e.metaKey||e.ctrlKey||e.altKey)return;
 const match=specs.find(x=>x[2]===e.key.toLowerCase());if(match){e.preventDefault();tool(match[1]);}
 if(e.key==='Delete'||e.key==='Backspace'){e.preventDefault();area?.deleteSelectedMarkers();changed();}
 if(e.key==='Escape')area.switchToSelectMode();
});
window.visualRequestClose=()=>{
 if(!area)return true;
 Promise.resolve(window.visualEditor?.stopVoice?.()).then(flush).then(()=>call('visual_checkpoint')).then(()=>window.api?.()?.close_visual_editor?.(id)).catch(e=>{
  status('Не удалось сохранить: '+e.message);$('close-error').textContent=e.message;$('close-dialog').showModal();
 });return false;
};
$('close-retry').onclick=()=>{$('close-dialog').close();window.visualRequestClose();};
$('close-stay').onclick=()=>$('close-dialog').close();
$('close-discard').onclick=()=>{clearTimeout(timer);saved=JSON.stringify(current());window.api?.()?.close_visual_editor?.(id);};
window.addEventListener('beforeunload',e=>{if(area&&JSON.stringify(current())!==saved){e.preventDefault();e.returnValue='';}});
async function start(){if(started)return;started=true;
 try{doc=await call('visual_open');img=new Image();const src=await call('visual_source');url=URL.createObjectURL(await(await fetch(src)).blob());img.src=url;await img.decode();
 markerjs3.TextMarker.DEFAULT_TEXT='Текст';area=new markerjs3.MarkerArea();area.targetWidth=img.naturalWidth;area.targetHeight=img.naturalHeight;area.targetImage=img;area.autoZoomOut=true;
 const shown=new Promise(resolve=>area.addEventListener('areashow',resolve,{once:true}));$('area').append(area);await shown;restoring=true;area.autoZoom();area.restoreState(doc.state,true);restoring=false;saved=JSON.stringify(current());
 for(const event of ['markercreate','markerchange','markerdelete','areastatechange'])area.addEventListener(event,changed);
 status('Сохранено');window.visualEditor={get area(){return area},flush,output,get doc(){return doc}};
 window.installVisualCommands(window.visualEditor,{call,img,status});
 }catch(e){status('Не удалось открыть: '+e.message);}}
core.on('hello',start);if(core.snapshot)start();
})();
