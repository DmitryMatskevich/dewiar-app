"""Keep video requests out of music and app-name shortcuts."""
import re
from urllib.parse import urlsplit

TRANSCRIBE = re.compile(r'транс+кри(?:б|пц)\w*|расшифр\w*|(?:извле\w*|получи\w*)\s+(?:мне\s+)?(?:текст|голос|речь)', re.I)
PROCESS = re.compile(r'(?:сдела\w*|состав\w*|подготов\w*)\s+(?:мне\s+)?(?:конспект|выжимк\w*|резюме)|проанализируй\s+(?:этот\s+)?(?:ролик|видео)|обработ(?:ай|ать)\s+(?:этот\s+)?(?:ролик|видео)', re.I)
OPEN = re.compile(r'откро\w*|откры\w*|покажи\w*|посмотр\w*|включи\w*|воспроизве\w*', re.I)
DOWNLOAD = re.compile(r'\b(?:скачай|скачать|скачивай|загрузи)\b', re.I)
CHOICE_QUESTION = 'Что сделать с роликом: открыть для просмотра, скачать видеофайл, расшифровать речь или обработать — например, сделать конспект или анализ?'


def request_scope(via=None, chat='legacy'):
    return 'remote:' + str(via.get('from') or 'remote') if via else 'chat:' + str(chat or 'legacy')


def active_pending(pending, scope, now):
    return pending if pending and pending.get('scope') == scope and 0 <= now - pending.get('time', 0) <= 600 else None


def reply_choice(text):
    value = ' '.join(str(text).casefold().replace('ё','е').strip(' .,!?:«»"').split())
    value = re.sub(r'^(?:пожалуйста[, ]+|давай\s+)', '', value)
    choices = {
        'open': ('посмотреть','посмотри','открыть','открой','просмотр','посмотреть ролик','открой ролик'),
        'download': ('скачать','скачай','скачай ролик','скачать ролик','скачать видео','скачай видео'),
        'transcribe': ('транскрибировать','транскрибируй','транскрибация','расшифровать','расшифруй','расшифровка','извлеки текст','извлечь текст'),
        'process': ('обработать','обработай','обработка','обработать видео','обработай ролик','конспект','анализ','резюме'),
        'cancel': ('отмена','отмени','не надо','ничего','ничего не делай'),
    }
    return next((key for key, phrases in choices.items() if value in phrases), None)


def resolve_followup(text, pending, scope, now):
    """Bind a short answer to the offered link, within its chat and lifetime only."""
    pending = active_pending(pending, scope, now)
    if not pending or not pending.get('urls') or video_urls(text):
        return text
    choice = reply_choice(text)
    if choice == 'cancel':
        return text
    action = choice or ('transcribe' if TRANSCRIBE.search(text) else 'process' if PROCESS.search(text) else None)
    if not action:
        return text
    if choice:
        value = str(text).strip().casefold()
        request = {'open':'Открой ролик для просмотра', 'download':'Скачай исходный видеофайл',
                   'transcribe':'Расшифруй речь из ролика', 'process':'Обработай ролик'}[action]
        if value in ('конспект','анализ','резюме'):
            request = {'конспект':'Сделай конспект ролика', 'анализ':'Проанализируй ролик', 'резюме':'Подготовь резюме ролика'}[value]
    else:
        request = text.strip()
    return request + ':\n' + '\n'.join(pending['urls'])

def video_urls(text):
    urls = []
    for raw in re.findall(r'https?://[^\s<>"«»]+', text or '', re.I):
        url = raw.rstrip('.,;!?)]')
        try:
            host = (urlsplit(url).hostname or '').lower()
        except ValueError:
            continue
        if any(host == d or host.endswith('.' + d) for d in ('youtube.com', 'youtu.be', 'rutube.ru', 'vkvideo.ru', 'vimeo.com', 'dailymotion.com')) or (host in ('vk.com', 'www.vk.com') and '/video' in url):
            if url not in urls:
                urls.append(url)
    return urls

def intent(text):
    # A question about an existing process is not authorization to start it again.
    if (TRANSCRIBE.search(text or '')
            and (re.search(r'\b(?:статус|запущен\w*|ид[её]т|выполня\w*|готов\w*|заверш\w*|законч\w*)\b', text or '', re.I)
                 or re.match(r'\s*(?:как|где|почему|когда|что\s+с)\b', text or '', re.I))
            and not re.search(r'\b(?:запусти|начни|сделай|давай|хочу|извлеки|транскрибируй|расшифруй)\b', text or '', re.I)):
        return 'status'
    if re.match(r'\s*(?:что\s+(?:такое|значит)|объясни|как\b|можно\s+ли\b|статус\b|где\s+задач)', text or '', re.I):
        return None
    if OPEN.search(text or '') and re.search(r'не\s+(?:транскриб\w*|расшифр\w*)', text or '', re.I):
        return 'open'
    if re.search(r'(?:не|без)\s+(?:дела\w*\s+)?(?:транс+кри(?:б|пц)\w*|расшифр\w*)', text or '', re.I):
        return None
    if TRANSCRIBE.search(text or ''):
        return 'transcribe'
    urls = video_urls(text)
    if urls and DOWNLOAD.search(text) and not re.search(r'\bне\s+(?:скачи\w*|скача\w*|загру\w*)', text, re.I):
        return 'download'
    if urls and PROCESS.search(text):
        return 'process'
    if urls and OPEN.search(text):
        return 'open'
    return 'clarify' if urls else None

def protected(text):
    return bool(TRANSCRIBE.search(text or '') or video_urls(text))
