"""Complete local scene memory, paged for models with finite input windows."""
import json,hashlib,math
from visual_assets import save
from visual_commands import get_index

def build(folder,doc,numbers):
    objects=[];ocr=[]
    for element in doc['elements']:
        if element.get('isDeleted'):continue
        row={k:v for k,v in element.items() if k not in ('dataURL',)}
        row['number']=numbers[element['id']];objects.append(row)
        if element['type']!='image':continue
        item=doc['files'][element['fileId']]
        cache=folder/'ocr'/item['id'];cache.mkdir(parents=True,exist_ok=True)
        index=get_index(folder/item['path'],cache,item)
        if index.get('status')=='error':raise ValueError('OCR картинки #'+str(row['number'])+' не выполнено: '+str(index.get('error','')))
        ocr.append({'image_id':element['id'],'file_sha256':item['sha256'],'status':index['status'],'engine':index.get('engine',''),
                    'source_width':item['width'],'source_height':item['height'],'lines':index['lines']})
    memory={'schema':1,'canvas_id':doc['id'],'revision':doc['revision'],'appState':doc['appState'],'objects':objects,'ocr':ocr}
    save(folder/'context.json',memory)
    return memory

def pages(memory,limit=16000):
    # Text is split only at transport boundaries, never discarded or paraphrased.
    records=[{'kind':'scene','canvas_id':memory['canvas_id'],'revision':memory['revision'],'appState':memory['appState']}]
    records += [{'kind':'object','value':e} for e in memory['objects']]
    for image in memory['ocr']:
        records.append({'kind':'ocr_image',**{k:v for k,v in image.items() if k!='lines'}})
        records += [{'kind':'ocr_line','image_id':image['image_id'],'value':line} for line in image['lines']]
    chunks=[];current=[];size=0
    for record_id,record in enumerate(records):
        raw=json.dumps(record,ensure_ascii=False,separators=(',',':'))
        # Very long labels / brush point arrays also remain completely retrievable.
        parts=[raw[i:i+limit] for i in range(0,len(raw),limit)]
        for j,part in enumerate(parts):
            entry={'record':record_id,'part':j+1,'parts':len(parts),'json_fragment':part} if len(parts)>1 else record
            length=len(json.dumps(entry,ensure_ascii=False))
            if current and size+length>limit:chunks.append(current);current=[];size=0
            current.append(entry);size+=length
    if current:chunks.append(current)
    return chunks
