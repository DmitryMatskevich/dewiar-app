"""Real stream wrapper: clean prose before display/TTS without mutating code or tool arguments."""
import sys
import json
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from response_text import clean_response, ResponseTextFilter
import llm

cases = {
    r'Первая строка.\nВторая.\tДальше.': 'Первая строка.\nВторая. Дальше.',
    r'Дважды\\nэкранировано.': 'Дважды\nэкранировано.',
    r'\*Важно\*': '*Важно*',
    r'{"content": "a\nb"}': r'{"content": "a\nb"}',
    r'Первая.\r\nВторая.': 'Первая.\nВторая.',
    r'Привет\uD83D\uDE00': 'Привет😀',
    r'Он сказал: \"да\".': 'Он сказал: "да".',
    r'\u041f\u0440ивет\u00a0мир.': 'Привет\u00a0мир.',
    r'Файл «C:\new\test.txt» и \\server\new\test.txt': r'Файл «C:\new\test.txt» и \\server\new\test.txt',
    '```python\nprint("a\\nb")\n```\nДальше.\\nКонец.': '```python\nprint("a\\nb")\n```\nДальше.\nКонец.',
    r'Пример `первая\nвторая` и затем\nтекст.': 'Пример `первая\\nвторая` и затем\nтекст.',
    r'Пример ``a ` b\nc`` и затем\nтекст.': 'Пример ``a ` b\\nc`` и затем\nтекст.',
    r'Символ \ называется обратная черта.': r'Символ \ называется обратная черта.',
    'Текст \x1b[31mцветной\x1b[0m без\x00мусора.': 'Текст цветной безмусора.',
}
for raw, want in cases.items():
    assert clean_response(raw) == want, (raw, clean_response(raw), want)
    # Includes a split in every escape, marker, inline delimiter and path.
    for pos in range(len(raw) + 1):
        f = ResponseTextFilter()
        got = f.feed(raw[:pos]) + f.feed(raw[pos:]) + f.flush()
        assert got == want, (pos, raw, got, want)
    f = ResponseTextFilter()
    assert ''.join(f.feed(ch) for ch in raw) + f.flush() == want

voice_cases = {
    r'Первая.\nВторая.\tДальше.': 'Первая. Вторая. Дальше.',
    r'\*Важно\*': 'Важно',
    r'Это backslash, бэкслэш, бек слеж и бэк-слеш.': 'Это обратная черта, обратная черта, обратная черта и обратная черта.',
    r'Знак \ — обратная черта.': 'Знак обратная черта — обратная черта.',
    r'Файл C:\new\test.txt готов.': 'Файл готов.',
    r'Служебный пример `a\nb` остаётся на экране.': 'Служебный пример остаётся на экране.',
}
for raw, want in voice_cases.items():
    assert llm.speakable(raw) == want, (raw, llm.speakable(raw), want)

# The browser/device speech preparation uses the same Russian wording.
mobile = (Path(__file__).resolve().parents[1] / 'android/assets/www/app.js').read_text()
start = mobile.index('const FILE_EXT')
end = mobile.index('\n}\n', mobile.index('function speechText')) + 3
js = mobile[start:end] + '\nconst EMOJI=/[\\u2600-\\u27BF]/g;\nconsole.log(JSON.stringify(' + json.dumps(list(voice_cases)) + '.map(speechText)));'
result = subprocess.run(['node', '-e', js], capture_output=True, text=True, check=True)
assert json.loads(result.stdout) == list(voice_cases.values()), result.stdout

tools = [{'id': 'x', 'name': 'write_file', 'args': {'content': r'a\nb', 'path': r'C:\new\x.py'}}]
events = [('text', 'Ответ.\\'), ('text', 'nЕщё.'), ('tools', tools)]
with patch.object(llm, '_stream_events_raw', return_value=iter(events)):
    got = list(llm.stream_events({}, '', []))
assert ''.join(v for k, v in got if k == 'text') == 'Ответ.\nЕщё.'
assert got[-1][1] is tools  # real argument identity, not a decoded copy
cancel = threading.Event()
def source(*a):
    yield 'text', 'Недоговорённое'
    cancel.set()
with patch.object(llm, '_stream_events_raw', side_effect=source):
    assert list(llm.stream_events({}, '', [], cancel=cancel)) == []
def broken_source(*a):
    yield 'text', 'Сохрани этот хвост'
    raise llm.LLMError('test stream disconnected')
with patch.object(llm, '_stream_events_raw', side_effect=broken_source):
    stream = llm.stream_events({}, '', [])
    assert next(stream) == ('text', 'Сохрани этот ')
    assert next(stream) == ('text', 'хвост')
    try:
        next(stream)
    except llm.LLMError:
        pass
    else:
        raise AssertionError('The original transport error must propagate')

print('✅ Prose escapes cleaned across chunk boundaries; code/paths/tools preserved; Russian symbol names in speech')
print('OK')
