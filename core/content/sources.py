"""Read only explicitly registered text outputs of finished tasks."""
import hashlib
from pathlib import Path

WORKFLOWS={'media-transcription','video-translation','text-extraction'}
MAX_BYTES=1048576


def _path(task, relative):
    if not isinstance(relative,str) or not relative.startswith('outputs/'):
        raise ValueError('Источник должен быть зарегистрированным результатом задачи')
    allowed={o.get('path') for o in task.data.get('outputs',[]) if isinstance(o,dict)}
    if relative not in allowed:
        raise ValueError('Результат не зарегистрирован в задаче')
    base=Path(task.dir).resolve();out=base/'outputs';path=(base/relative).resolve()
    if not path.is_relative_to(out) or path.suffix.lower() not in ('.txt','.md','.srt') or not path.is_file():
        raise ValueError('Недоступный текстовый результат')
    return path


def available(tasks):
    rows=[]
    for tid,task in list(tasks.items()):
        if task.data.get('workflow') not in WORKFLOWS or task.data.get('status')!='done':continue
        for output in task.data.get('outputs',[]):
            rel=output.get('path') if isinstance(output,dict) else None
            try:path=_path(task,rel)
            except (ValueError,OSError):continue
            if path.stat().st_size>MAX_BYTES:continue
            rows.append({'task_id':tid,'path':rel,'title':f"{task.data.get('title') or tid} — {path.name}",'bytes':path.stat().st_size})
    return rows[:500]


def snapshot(tasks,tid,relative):
    # Exact ID, never TaskManager's fuzzy name lookup.
    task=tasks.get(tid)
    if task is None or task.data.get('workflow') not in WORKFLOWS or task.data.get('status')!='done':
        raise ValueError('Нужна завершённая задача с текстовым результатом')
    path=_path(task,relative)
    with path.open('rb') as f:data=f.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise ValueError('Источник больше 1 МиБ; выберите меньший результат')
    try:text=data.decode('utf-8-sig')
    except UnicodeDecodeError as e:raise ValueError('Для импорта нужен текст UTF-8') from e
    if not text.strip():raise ValueError('Источник пуст')
    return {'task_id':tid,'path':relative,'task_title':task.data.get('title',''),
            'source_updated':task.data.get('updated'), 'sha256':hashlib.sha256(data).hexdigest(),
            'text':text,'timing':'source_text_only','quality':'requires_editor_review'}
