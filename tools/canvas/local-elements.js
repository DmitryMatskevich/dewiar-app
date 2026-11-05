// Dewiar's local scene element factory.
//
// This is deliberately independent of Excalidraw.  The objects it returns
// currently use the same persisted scene shape so the renderer can be
// replaced incrementally without rewriting stored canvases in one release.

const uid=()=>crypto.randomUUID();

export function localElement(spec={}){
  const now=Date.now();
  const id=spec.id||uid();
  return {
    type:spec.type||'rectangle', x:Number(spec.x||0), y:Number(spec.y||0),
    width:Number(spec.width||0), height:Number(spec.height||0), angle:Number(spec.angle||0),
    strokeColor:spec.strokeColor||'#1e1e1e', backgroundColor:spec.backgroundColor||'transparent',
    fillStyle:spec.fillStyle||'solid', strokeWidth:Number(spec.strokeWidth??2),
    strokeStyle:spec.strokeStyle||'solid', roughness:Number(spec.roughness??0),
    opacity:Number(spec.opacity??100), groupIds:spec.groupIds||[], frameId:spec.frameId||null,
    roundness:spec.roundness||null, seed:Number(spec.seed||Math.floor(Math.random()*2147483647)),
    version:1, versionNonce:Math.floor(Math.random()*2147483647),
    link:null, locked:false, boundElements:null, customData:spec.customData,
    ...spec, id, updated:now, isDeleted:Boolean(spec.isDeleted||false),
  };
}

export function localImageElement({fileId,x=0,y=0,width,height}={}){
  if(!fileId)throw new Error('Для изображения нужен fileId');
  return localElement({type:'image',fileId,x,y,width,height,strokeColor:'transparent',backgroundColor:'transparent',fillStyle:'solid',strokeWidth:1,roughness:0,status:'saved',scale:[1,1],crop:null});
}

export function localEllipseLabel({text,x=0,y=0,width=44,height=44,strokeColor='#e5484d',backgroundColor='#ffffff',customData}={}){
  return localElement({type:'ellipse',x,y,width,height,strokeColor,backgroundColor,fillStyle:'solid',strokeWidth:2,roughness:0,label:{text:String(text),fontFamily:2,fontSize:24,strokeColor},customData});
}

export function localRectangle(spec={}){return localElement({type:'rectangle',...spec});}
export function localText({text,x=0,y=0,width=200,height=32,...spec}={}){return localElement({type:'text',text:String(text),x,y,width,height,...spec});}
