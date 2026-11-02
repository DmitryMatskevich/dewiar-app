"""Assistant screenshot messages; Notes are created only by an explicit action."""
import base64
import io
import re
import threading
import uuid
from datetime import datetime

LOCK = threading.RLock()
CAPTURE_LOCK = threading.Lock()


def public(item):
    att = item['att'][0]
    return dict(att['capture'], id=item['id'], title=att['name'], url=att['thumb'],
                image=att['path'], note_path=att.get('note_path', ''))


def capture(engine):
    from eyes import capture_screen, prepare
    from tools import desktop_env
    from platform_info import MAC
    from ocr import recognize_result
    from PIL import Image
    if not engine.toolbox.policy('save_screenshot')[0]:
        raise ValueError('Снимок экрана отключён в настройках Dewiar')
    if not CAPTURE_LOCK.acquire(blocking=False):
        raise ValueError('Снимок уже обрабатывается. Дождитесь готовой карточки.')
    try:
        engine.note('Делаю снимок экрана и распознаю текст локально…')
        data = capture_screen(desktop_env(), exclude_dewiar=True) if MAC else capture_screen(desktop_env())
        image = Image.open(io.BytesIO(data)); image.load()
        png = io.BytesIO(); image.save(png, 'PNG'); data = png.getvalue()
        title = f'Снимок экрана {datetime.now():%Y-%m-%d %H-%M-%S} {uuid.uuid4().hex[:6]}'
        rel = f'Входящие/Снимки экрана/{title}.png'
        path = engine.workspace.safe(rel); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        try:
            result = recognize_result(data)
        except Exception as error:
            result = dict(status='error', text='', engine='', error=str(error))
        preview, _, _ = prepare(data)
        summary = {'done': 'Текст распознан локально.', 'empty': 'Читаемый текст не найден.',
                   'error': 'Распознать текст не удалось; изображение сохранено.'}.get(result['status'], 'Снимок готов.')
        att = dict(kind='image', name=title, path=rel, thumb='data:image/jpeg;base64,' + preview,
                   capture=dict(text=result['text'], status=result['status'], engine=result.get('engine', ''),
                                error=result.get('error', ''), summary=summary))
        item = engine.chatlog.add('assistant', 'Снимок экрана. ' + summary,
                                  who=engine.s.get('persona'), att=[att])
        engine.emit('chat', item=item)
        value = public(item)
        engine.emit('screenshot_message', screenshot=value)
        return value
    finally:
        engine.note('')
        CAPTURE_LOCK.release()


def command(engine, cmd, args):
    if cmd == 'screenshot_messages':
        rows = engine.chatlog.page(limit=500, chat_id=engine.chatlog.active)['items']
        return [public(row) for row in rows if row.get('att') and row['att'][0].get('capture')][-10:]
    with LOCK:
        item = engine.chatlog.screenshot(args.get('id'))
        value = public(item)
        if cmd == 'screenshot_image':
            data = engine.workspace.safe(value['image']).read_bytes()
            return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')
        if cmd == 'screenshot_dismiss':
            engine.chatlog.screenshot(value['id'], dismiss=True)
            engine.emit('screenshot_removed', id=value['id'])
            engine.emit('chat_changed')
            return True
        if cmd != 'screenshot_to_note':
            raise ValueError('Неизвестное действие со снимком')
        if value['note_path'] and engine.workspace.safe(value['note_path']).is_file():
            return {'path': value['note_path']}
        workspace = engine.workspace
        rel = workspace.save_note(value['title'], 'Перенос снимка…', folder='Снимки экрана')
        attachment = workspace.note_attach(rel, 'Экран.png', workspace.safe(value['image']).read_bytes())
        text = value['text']
        fence = '`' * max(3, max((len(x) + 1 for x in re.findall(r'`+', text)), default=3))
        body = (f"Движок: {value['engine']}. Текст извлечён автоматически; возможны ошибки.\n\n{fence}text\n{text}\n{fence}\n"
                if text else value['summary'] + '\n')
        content = f"# {value['title']}\n\n## Снимок экрана\n\n{attachment['line']}\n\n## Распознанный текст\n\n{body}"
        if text:
            txt = workspace.note_attach(rel, 'Распознанный текст.txt', text.encode('utf-8'))
            content += '\n' + txt['line'] + '\n'
        workspace.write(rel, content)
        engine.chatlog.screenshot(value['id'], note_path=rel)
        engine.emit('note_saved', kind='note', title=value['title'], path=rel, text='Снимок и текст перенесены в заметки')
        engine.emit('screenshot_note_saved', id=value['id'], path=rel)
        engine.emit('chat_changed')
        return {'path': rel}
