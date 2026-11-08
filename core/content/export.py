"""Portable text-only package; no remote resources or active HTML."""
import base64
import hashlib
import html
import io
import json
import zipfile
from .models import plain_blocks, render_html, render_markdown


def package(store,mid,revision):
    row=store.revision(mid,revision)
    md='# '+render_markdown(plain_blocks(row['title'])).replace('\n',' ')+'\n\n'+('Автор: '+render_markdown(plain_blocks(row['author']))+'\n\n' if row['author'] else '')+render_markdown(row['blocks'])+'\n'
    page='<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>'+html.escape(row['title'])+'</title><style>body{max-width:800px;margin:auto;padding:24px;font:18px/1.6 system-ui}article{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>'+html.escape(row['title'])+'</h1><p>'+html.escape(row['author'])+'</p><article>'+render_html(row['blocks'])+'</article></html>'
    bio=(row.get('author_credit') or {}).get('bio','')
    if bio:
        md+='\n## Об авторе\n\n'+render_markdown(plain_blocks(bio))+'\n'
        page=page.replace('</article>','</article><aside><h2>Об авторе</h2><p style="white-space:pre-wrap">'+html.escape(bio)+'</p></aside>')
    sources=store.sources(mid,revision)
    files={'article.md':md.encode(),'article.html':page.encode(),
           'material.json':json.dumps(row,ensure_ascii=False,indent=2).encode(),
           'sources.json':json.dumps(sources,ensure_ascii=False,indent=2).encode()}
    from .assets import read
    for source in sources:
        if source.get('asset_id'):
            files['assets/'+source['asset_id']+'.html.txt']=read(store,source['asset_id'])
    # Opaque blocks still require the original even if its source was detached.
    for block in row['blocks']:
        if block['type']=='unsupported':files['assets/'+block['original_ref']+'.html.txt']=read(store,block['original_ref'])
    with store.db() as c:
        review=c.execute('SELECT snapshot FROM editorial_reviews WHERE material_id=? AND revision=?',(mid,revision)).fetchone()
    files['editorial-review.json']=(review['snapshot'] if review else 'null').encode()
    manifest={'schema_version':1,'material_id':mid,'revision':revision,'content_type':row['kind'],
              'format':'block-editor-export-v1','files':{k:hashlib.sha256(v).hexdigest() for k,v in files.items()},
              'publication_status':'not_published'}
    if len(sources)>1:manifest.update(schema_version=2,format='block-editor-export-v2')
    if row.get('author_credit'):manifest.update(schema_version=3,format='block-editor-export-v3')
    if any(s.get('segments_v1') for s in sources):manifest.update(schema_version=4,format='block-editor-export-v4')
    files['manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
    buf=io.BytesIO()
    if sum(map(len,files.values()))>32*1024*1024:raise ValueError('Экспорт больше 32 МиБ распакованных данных; создайте материал с меньшим набором источников')
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for name,data in files.items():
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,data)
    if len(buf.getvalue())>8*1024*1024:raise ValueError('Экспорт больше 8 МиБ ZIP; создайте материал с меньшим набором источников')
    return {'filename':f'material-{mid}-v{revision}.zip','mime':'application/zip','b64':base64.b64encode(buf.getvalue()).decode(),'manifest':manifest}
