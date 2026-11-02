"""Assistant screenshot messages; Notes are created only by an explicit action."""
import base64
import io
import re
import threading
import uuid
import time
from datetime import datetime

LOCK = threading.RLock()
CAPTURE_LOCK = threading.Lock()


def public(item):
    att = item['att'][0]
    return dict(att['capture'], id=item['id'], title=att['name'], url=att['thumb'],
                image=att['path'], note_path=att.get('note_path', ''))


def sources():
    from platform_info import MAC
    if not MAC:return {'windows': [], 'message': 'Список окон приложений доступен на macOS. Можно снять весь экран или выбрать область.'}
    from screenshot_sources import visible_windows
    return {'windows': visible_windows(), 'message': ''}


def prepare_region(engine):
    """A private in-memory preview; no chat, files or OCR until explicit confirmation."""
    from eyes import capture_screen
    from platform_info import MAC
    from tools import desktop_env
    from PIL import Image
    if not engine.toolbox.policy('save_screenshot')[0]:raise ValueError('Снимок экрана отключён в настройках Dewiar')
    data = capture_screen(desktop_env(), exclude_dewiar=True) if MAC else capture_screen(desktop_env())
    with Image.open(io.BytesIO(data)) as image:width, height = image.size
    token = uuid.uuid4().hex
    with LOCK:
        engine._screenshot_drafts = {token: {'data': data, 'width': width, 'height': height,
                                           'created': time.monotonic(), 'chat_id': engine.chatlog.active}}
    def expire():
        with LOCK:getattr(engine, '_screenshot_drafts', {}).pop(token, None)
    timer = threading.Timer(600, expire); timer.daemon = True; timer.start()
    return {'id': token}


def draft(engine, token, region=None, consume=False):
    from PIL import Image
    with LOCK:
        item = getattr(engine, '_screenshot_drafts', {}).get(str(token))
        if not item or time.monotonic()-item['created'] > 600:raise ValueError('Предварительный снимок устарел. Выберите область заново.')
        if item['chat_id'] != engine.chatlog.active:raise ValueError('Чат изменился. Выберите область заново в нужном чате.')
        if region is None:return {'id': token, 'url': 'data:image/png;base64,' + base64.b64encode(item['data']).decode('ascii'), 'width': item['width'], 'height': item['height']}
        try:x,y,w,h = [region[k] for k in ('x','y','width','height')]
        except (TypeError, KeyError):raise ValueError('Выделите область снимка') from None
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (x,y,w,h)) or x < 0 or y < 0 or w < 2 or h < 2 or x+w > item['width'] or y+h > item['height']:
            raise ValueError('Область выходит за границы снимка или слишком мала')
        with Image.open(io.BytesIO(item['data'])) as image:
            output = io.BytesIO(); image.crop((x,y,x+w,y+h)).save(output, 'PNG'); data = output.getvalue()
        if consume:getattr(engine, '_screenshot_drafts', {}).pop(token, None)
        return data


def capture(engine, source='screen', application='', window_id=None, draft_id='', region=None):
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
        if draft_id:
            if region is None:raise ValueError('Выделите область и подтвердите снимок')
            data = draft(engine, draft_id, region, consume=True)
        elif source == 'application':
            if not MAC:raise ValueError('Снимок отдельного окна пока доступен на macOS')
            from screenshot_sources import capture_window
            data = capture_window(application, window_id, engine.s.get('voice_commands'))
        elif source == 'screen':
            data = capture_screen(desktop_env(), exclude_dewiar=True) if MAC else capture_screen(desktop_env())
        else:raise ValueError('Выберите весь экран, область или окно приложения')
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
