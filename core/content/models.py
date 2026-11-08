"""Small, versioned document model. body is always derived from blocks."""
import hashlib
import html
import re

BLOCK_TYPES = ('paragraph', 'heading', 'quote', 'list', 'unsupported')
MAX_TEXT_BYTES = 1048576


def plain_blocks(body):
    if not isinstance(body, str):
        raise ValueError('body: нужен текст')
    # Deterministic conversion preserves create request idempotency and old revisions.
    return [{'id': 'text-' + hashlib.sha256(body.encode()).hexdigest()[:24],
             'type': 'paragraph', 'text': body, 'locked': False}]


def validate_blocks(blocks):
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 500:
        raise ValueError('Документ должен содержать от 1 до 500 блоков')
    result, ids, size = [], set(), 0
    for b in blocks:
        if not isinstance(b, dict) or set(b) != ({'id', 'type', 'text', 'locked', 'original_ref'} if b.get('type')=='unsupported' else {'id','type','text','locked'}):
            raise ValueError('Некорректные поля блока')
        if not isinstance(b['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', b['id']) or b['id'] in ids:
            raise ValueError('Нужны уникальные ID блоков')
        if b['type'] not in BLOCK_TYPES or type(b['locked']) is not bool:
            raise ValueError('Неизвестный тип или защита блока')
        if b['type']=='unsupported' and (not b['locked'] or not isinstance(b['original_ref'],str) or not re.fullmatch('[0-9a-f]{64}',b['original_ref'])):
            raise ValueError('Неподдерживаемый блок должен быть защищён и связан с оригиналом')
        if not isinstance(b['text'], str) or '\x00' in b['text']:
            raise ValueError('Блок должен содержать текст без нулевого символа')
        size += len(b['text'].encode('utf-8')) + 2
        if size > MAX_TEXT_BYTES + 2:
            raise ValueError('Текст материала превышает 1 МиБ')
        ids.add(b['id']); result.append(dict(b))
    return result


def body_from_blocks(blocks):
    return '\n\n'.join(b['text'] for b in blocks)


def with_blocks(row):
    """Read old immutable snapshots without rewriting their bytes."""
    return {**row, 'blocks': row.get('blocks') or plain_blocks(row['body'])}


def render_html(blocks):
    output = []
    for b in validate_blocks(blocks):
        if b['type']=='unsupported':raise ValueError('Экспорт остановлен: неподдерживаемый блок '+b['id']+'. Скачайте оригинал или обработайте блок вручную.')
        text = html.escape(b['text']).replace('\r\n','\n').replace('\r','\n').replace('\n','<br>')
        if b['type'] == 'list':
            output.append('<ul>' + ''.join('<li>' + html.escape(x) + '</li>' for x in b['text'].splitlines()) + '</ul>')
        else:
            tag = {'paragraph': 'p', 'heading': 'h2', 'quote': 'blockquote'}[b['type']]
            output.append(f'<{tag}>' + text + f'</{tag}>')
    return '\n'.join(output)


def render_markdown(blocks):
    output = []
    for b in validate_blocks(blocks):
        if b['type']=='unsupported':raise ValueError('Экспорт остановлен: неподдерживаемый блок '+b['id']+'. Скачайте оригинал или обработайте блок вручную.')
        # Escape raw HTML and Markdown syntax from text fields; structure comes from type.
        text = re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', html.escape(b['text'], quote=False))
        if b['type'] == 'heading': text = '## ' + text.replace('\n', ' ')
        if b['type'] == 'quote': text = '\n'.join('> ' + x for x in text.splitlines())
        if b['type'] == 'list': text = '\n'.join('- ' + x for x in text.splitlines())
        output.append(text)
    return '\n\n'.join(output)
