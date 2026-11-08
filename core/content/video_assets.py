"""Exact private render verification shared by preview and editorial acceptance."""
import hashlib
import os
import re

def fingerprint(info):
    return [info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]

def path_for(store,receipt):
    relative=receipt.get('asset_path','')
    if not isinstance(relative,str) or not re.fullmatch(r'renders/[0-9a-f]{64}\.(mp4|mov|webm|mkv)',relative):raise ValueError('Некорректный путь сохранённого видео')
    root=store.root.resolve();path=root/relative
    if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('Недопустимая ссылка видео')
    return path

def verify(store,receipt):
    path=path_for(store,receipt)
    try:
        with os.fdopen(os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)),'rb') as source:
            stamp=fingerprint(os.fstat(source.fileno()));digest=hashlib.file_digest(source,'sha256').hexdigest()
            if fingerprint(os.fstat(source.fileno()))!=stamp:raise ValueError('Видео изменилось при проверке')
    except OSError as exc:raise ValueError('Сохранённый файл недоступен') from exc
    if digest!=receipt['sha256'] or stamp[1]!=receipt['bytes']:raise ValueError('Контрольная сумма видео не совпала')
    return path,digest,stamp
