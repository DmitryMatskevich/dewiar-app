"""Content-addressed local originals. Never serve imported HTML as active content."""
import hashlib
import os
import re
import tempfile


def put(store,data,mime):
    if not isinstance(data,bytes) or not 0<len(data)<=1048576:raise ValueError('Оригинал: размер от 1 байта до 1 МиБ')
    digest=hashlib.sha256(data).hexdigest();root=store.root/'assets';root.mkdir(mode=0o700,exist_ok=True)
    path=root/digest
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Повреждён сохранённый оригинал')
    else:
        fd,name=tempfile.mkstemp(prefix='.incoming-',dir=root)
        try:
            with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
    with store.db() as c:
        c.execute('INSERT OR IGNORE INTO content_assets VALUES (?,?,?)',(digest,mime,len(data)))
    return digest


def read(store,digest):
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Некорректный ID оригинала')
    with store.db() as c:
        row=c.execute('SELECT size FROM content_assets WHERE sha256=?',(digest,)).fetchone()
        if not row:raise ValueError('Оригинал не зарегистрирован')
    path=store.root/'assets'/digest
    if path.is_symlink():raise ValueError('Недопустимая ссылка оригинала')
    try:
        with path.open('rb') as f:data=f.read(1048577)
    except OSError as e:raise ValueError('Оригинал недоступен') from e
    if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=digest:raise ValueError('Контрольная сумма оригинала не совпадает')
    return data
