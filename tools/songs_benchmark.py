"""Подготовка и фиксация эталона песен, WER/CER против независимой гипотезы.

Исходный ASR — только черновик. Эталон нельзя заморозить без подтверждения
всех строк и прослушивания полной записи (иначе пропуски ASR невидимы).
Команды не запускают приложение, модели, сетевые запросы или скачивания.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def write_new(path, obj):
    # Не затираем отредактированный эталон при повторном запуске подготовки.
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2); f.write('\n')


def prepare(capture, output):
    capture = Path(capture)
    recognition = json.loads((capture/'recognition.json').read_text())
    metadata = json.loads((capture/'metadata.json').read_text())
    audio = capture/'audio.wav'
    rows = [{'start_ms':round(s['start']*1000), 'end_ms':round(s['end']*1000),
             'text':s['text'], 'reviewed':False, 'section':'unknown'} for s in recognition['segments']]
    write_new(output, {'schema_version':1, 'source':metadata.get('source'), 'title':metadata.get('title'),
        'audio_sha256':digest(audio), 'draft_from':metadata.get('method'),
        'reviewed_audio_complete':False, 'reviewer':'', 'segments':rows,
        'instructions':'Прослушайте всю запись, добавьте пропущенные фразы, исправьте слова и отметьте строки. Время сегмента ASR не равно времени слов.'})


def freeze(draft, audio, output):
    ref = json.loads(Path(draft).read_text())
    if ref.get('reviewed_audio_complete') is not True or not str(ref.get('reviewer') or '').strip():
        raise ValueError('Эталон не проверен целиком по записи или не указан проверяющий')
    segments = ref.get('segments') or []
    if not segments or any(s.get('reviewed') is not True for s in segments):
        raise ValueError('Остались непроверенные строки')
    for s in segments:
        if not isinstance(s.get('text'), str) or not s['text'].strip() or s['start_ms'] < 0 or s['end_ms'] <= s['start_ms']:
            raise ValueError('Некорректная фраза эталона')
    if digest(audio) != ref.get('audio_sha256'):
        raise ValueError('Это другая запись: SHA-256 аудио не совпадает')
    payload = json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()
    write_new(output, {'reference':ref, 'reference_sha256':hashlib.sha256(payload).hexdigest()})


def normalize(text):
    return re.findall(r'\w+', str(text).casefold().replace('ё','е'), re.UNICODE)


def distance(a, b):
    previous = list(range(len(b)+1))
    for i,x in enumerate(a,1):
        current = [i]
        for j,y in enumerate(b,1):
            current.append(min(current[-1]+1, previous[j]+1, previous[j-1]+(x!=y)))
        previous = current
    return previous[-1]


def score(reference, hypothesis, audio):
    frozen = json.loads(Path(reference).read_text()); ref = frozen['reference']
    payload = json.dumps(ref, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()
    if hashlib.sha256(payload).hexdigest() != frozen.get('reference_sha256'):
        raise ValueError('Замороженный эталон изменён')
    if digest(audio) != ref['audio_sha256']:
        raise ValueError('Другая версия аудио')
    hypo = json.loads(Path(hypothesis).read_text())
    expected = normalize(' '.join(s['text'] for s in ref['segments']))
    actual = normalize(hypo.get('text') or ' '.join(s['text'] for s in hypo.get('segments', [])))
    if not expected: raise ValueError('Пустой эталон')
    chars, hyp_chars = ''.join(expected), ''.join(actual)
    return {'reference_sha256':frozen['reference_sha256'], 'audio_sha256':ref['audio_sha256'],
            'hypothesis_sha256':digest(hypothesis), 'reference_words':len(expected), 'hypothesis_words':len(actual),
            'wer':distance(expected,actual)/len(expected), 'cer':distance(chars,hyp_chars)/len(chars),
            'normalization':'casefold, ё=е, punctuation ignored; CER excludes spaces',
            'scope':'whole recording text; timing and section quality are not measured'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    p = sub.add_parser('prepare'); p.add_argument('capture',type=Path); p.add_argument('output',type=Path)
    p = sub.add_parser('freeze'); p.add_argument('draft',type=Path); p.add_argument('audio',type=Path); p.add_argument('output',type=Path)
    p = sub.add_parser('score'); p.add_argument('reference',type=Path); p.add_argument('hypothesis',type=Path); p.add_argument('audio',type=Path)
    args = parser.parse_args()
    if args.command == 'prepare': prepare(args.capture,args.output)
    elif args.command == 'freeze': freeze(args.draft,args.audio,args.output)
    else: print(json.dumps(score(args.reference,args.hypothesis,args.audio),ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
