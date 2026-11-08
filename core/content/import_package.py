"""Bounded ZIP round-trip. Imported publication/readiness state is never trusted."""
import base64
import hashlib
import io
import json
import re
import zipfile
from .assets import put
from .models import with_blocks, validate_blocks
from .store import FIELDS

FIXED={'article.md','article.html','material.json','sources.json','editorial-review.json','manifest.json'}
REQUIRED={'article.md','article.html','material.json','sources.json','manifest.json'}


def import_package(store,args):
    encoded=args.get('b64')
    if not isinstance(encoded,str) or len(encoded)>12*1024*1024:raise ValueError('Пакет слишком большой: максимум 8 МиБ ZIP')
    try:raw=base64.b64decode(encoded,validate=True)
    except ValueError as e:raise ValueError('Некорректный файл ZIP') from e
    if len(raw)>8*1024*1024:raise ValueError('Пакет слишком большой: максимум 8 МиБ ZIP')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if len(infos)>64 or len(set(names))!=len(names) or not REQUIRED<=set(names):raise ValueError('Неверный состав пакета')
            if any(n not in FIXED and not re.fullmatch(r'assets/[0-9a-f]{64}\.html\.txt',n) for n in names):raise ValueError('Недопустимый путь в пакете')
            if sum(i.file_size for i in infos)>32*1024*1024 or any(i.file_size>(24 if i.filename=='sources.json' else 8)*1024*1024 or i.flag_bits&1 for i in infos):raise ValueError('Пакет превышает лимит распаковки или зашифрован')
            files={i.filename:z.read(i) for i in infos}
    except (zipfile.BadZipFile,RuntimeError,NotImplementedError,OSError) as e:raise ValueError('Повреждённый или неподдерживаемый ZIP') from e
    try:
        manifest=json.loads(files['manifest.json']);row=json.loads(files['material.json']);sources=json.loads(files['sources.json'])
    except (ValueError,UnicodeDecodeError,RecursionError) as e:raise ValueError('Повреждён JSON пакета') from e
    if not isinstance(manifest,dict) or manifest.get('schema_version') not in (1,2,3,4) or manifest.get('format') not in ('block-editor-export-v1','block-editor-export-v2','block-editor-export-v3','block-editor-export-v4','plain-text-editor-export'):raise ValueError('Версия формата пакета не поддерживается')
    checks=manifest.get('files')
    if not isinstance(checks,dict) or set(checks)!=set(files)-{'manifest.json'}:raise ValueError('Manifest не соответствует файлам')
    if any(not isinstance(d,str) or hashlib.sha256(files[n]).hexdigest()!=d for n,d in checks.items()):raise ValueError('Контрольная сумма файла пакета не совпадает')
    if not isinstance(row,dict) or not isinstance(row.get('body'),str):raise ValueError('Не найден материал пакета')
    if row.get('author_credit') is not None and (manifest['schema_version'] not in (3,4) or manifest['format']!=f"block-editor-export-v{manifest['schema_version']}"):raise ValueError('Сведения об авторе требуют пакет версии 3 или 4')
    row=with_blocks(row);validate_blocks(row['blocks'])
    if row.get('id')!=manifest.get('material_id') or row.get('revision')!=manifest.get('revision'):raise ValueError('Ревизия пакета не совпадает с материалом')
    from .source_records import validate_many
    validate_many(sources)
    if manifest['schema_version']==1 and len(sources)>1:raise ValueError('Несколько источников требуют пакет версии 2')
    imported=[]
    for original in sources:
        source={**original,'quality':'requires_editor_review','attribution':'package_claimed'}
        if source.get('segments_v1'):
            if manifest['schema_version']!=4 or manifest['format']!='block-editor-export-v4':raise ValueError('Разметка источников требует пакет версии 4')
            source['segments_v1']={**source['segments_v1'],'timing_origin':'package_claimed'}
        if source.get('asset_id'):
            digest=source['asset_id'];data=files.get('assets/'+digest+'.html.txt')
            if data is None or hashlib.sha256(data).hexdigest()!=digest:raise ValueError('В пакете нет ожидаемого оригинала')
        if source.get('source_kind')=='html' and 'asset_id' not in source:raise ValueError('HTML-источник потерял оригинал')
        imported.append(source)
    for name,data in files.items():
        if name.startswith('assets/'):
            digest=name.split('/')[1].split('.')[0]
            if not 0<len(data)<=1048576 or hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Размер или имя asset не соответствует содержимому')
    asset_ids={name.split('/')[1].split('.')[0] for name in files if name.startswith('assets/')}
    if any(b['type']=='unsupported' and b['original_ref'] not in asset_ids for b in row['blocks']):raise ValueError('В пакете отсутствует оригинал защищённого блока')
    data={k:row[k] for k in FIELDS if k in row}
    if data.get('author_credit') is not None:
        from .author_credit import imported as imported_credit
        data['author_credit']=imported_credit(data['author_credit'],data.get('author',''))
    # Validate before creating files; asset references are registered just below.
    with store.db() as c:
        if not any(b['type']=='unsupported' for b in row['blocks']):store._validate(c,data)
    for name,content in files.items():
        if name.startswith('assets/'):put(store,content,'text/html')
    return store.create(data,args.get('request_key'),sources=imported)
