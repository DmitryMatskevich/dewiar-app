"""Read-only real-library ASR experiment with resumable per-song artifacts.

Never starts Engine, edits a card/configuration, downloads models or replaces
the installed app. Each audio decoder/recognizer runs in the existing isolated
song runtime. One failed recording does not block the remainder.
"""
import argparse
import concurrent.futures
import difflib
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from songs.runtime import analyze
from songs.worker import save
from songs.consensus import normalized,tokens


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()


def inventory(root):
    songs=[]
    for path in sorted(root.glob('*/song.json')):
        row=json.loads(path.read_text())
        if row.get('deleted') or row.get('status')=='deleted':continue
        audio=(root/str(row.get('audio',{}).get('path',''))).resolve()
        songs.append(dict(song_id=row['song_id'],title=row.get('title',''),
            card_sha256=sha(path),audio=str(audio) if audio.is_file() and root in audio.parents else None,
            audio_sha256=sha(audio) if audio.is_file() and root in audio.parents else None,
            duration_ms=row.get('audio',{}).get('duration_ms'),manual=bool(row.get('phrase_history')),
            original_phrases=row.get('phrases',[])))
    return songs


def process(row,output,phase):
    folder=(output/'qwen-large-refined' if phase=='refine-large' else output)/row['song_id']
    folder.mkdir(parents=True,exist_ok=True)
    result_path=folder/'result.json'
    if phase=='refine-large' and not result_path.exists():
        baseline=json.loads((output/row['song_id']/'result.json').read_text())
        large=json.loads((output/row['song_id']/'qwen-large.json').read_text())
        baseline['hypotheses']=[h for h in baseline['hypotheses'] if h['model'].startswith('Whisper')]+[large]
        baseline.pop('selected',None)
        baseline.update(errors=[],completed=False)
        save(result_path,baseline)
    receipt_path=folder/(phase+'.json')
    implementation=hashlib.sha256(''.join(sha(ROOT/'core/songs'/name) for name in
        ('worker.py','reconciliation.py','alignment_words.py','consensus.py')).encode()).hexdigest()
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_text())
        if (receipt.get('audio_sha256')==row['audio_sha256'] and receipt.get('ok') and
                (phase not in ('finalize','refine-large') or receipt.get('implementation_sha256')==implementation)):
            return receipt
    start=time.monotonic()
    receipt={k:v for k,v in row.items() if k!='original_phrases'}
    receipt['implementation_sha256']=implementation
    try:
        if not row['audio']:raise ValueError('Нет сохранённого аудио')
        if phase!='recognize' and not result_path.exists():raise ValueError('Нет первого распознавания')
        print('START',phase,row['title'],flush=True)
        result=analyze(Path(row['audio']),result_path,row['song_id'],threading.Event(),
            lambda label:print(row['title'],label,flush=True),
            steps='whisper,qwen' if phase=='recognize' else 'consensus,align',
            resume=phase!='recognize')
        selected=result.get('selected',{})
        receipt.update(ok=bool(result.get('completed')) and not result.get('errors'),
            errors=result.get('errors',[]),
            models=[dict(model=h['model'],words=len(' '.join(p['text'] for p in h.get('phrases',[])).split()),
                         phrases=len(h.get('phrases',[])),seconds=h.get('seconds')) for h in result['hypotheses']],
            alignment=selected.get('alignment'),spine_model=selected.get('spine_model'),
            comparisons={k:result.get('comparison',{}).get(k) for k in ('accepted','needs_review','errors')},
            audio_windows=len(result.get('comparison',{}).get('rechecks',[])))
    except Exception as exc:
        receipt.update(ok=False,error=str(exc))
    receipt['elapsed_seconds']=round(time.monotonic()-start,2)
    save(receipt_path,receipt)
    print('DONE',phase,row['title'],receipt['ok'],receipt['elapsed_seconds'],flush=True)
    return receipt


def report(output,model='1.7'):
    inventory_rows=json.loads((output/'inventory.json').read_text())
    lines=['# Проверка всей библиотеки песен — 06.11.2026','',
        f'Повторное распознавание настоящих аудиофайлов: Whisper large-v2 + Qwen3-ASR {model}B. '
        'Рабочие карточки и ручные правки сохранены. Согласие моделей не равно точности по эталону.','',
        '| Песня | Секунды | Согласие ASR | Проверки звука | Принятые правки | Спорные варианты | Выравнивание фраз: полное / частичное / всего |',
        '|---|---:|---:|---:|---:|---:|---:|']
    summaries=[]
    for row in inventory_rows:
        folder=(output/'qwen-large-refined' if model=='1.7' else output)/row['song_id'];path=folder/'result.json'
        if not path.exists():continue
        d=json.loads(path.read_text());hs=d.get('hypotheses',[])
        a,b=[list(map(normalized,tokens(h['phrases']))) for h in hs[:2]]
        agreement=sum(m.size for m in difflib.SequenceMatcher(None,a,b,autojunk=False).get_matching_blocks())/max(len(a),len(b),1)
        c=d.get('comparison',{});s=d.get('selected',{});al=s.get('alignment',{})
        measured=sum(len(p.get('words',[])) for p in s.get('phrases',[]))
        total=len(tokens(s.get('phrases',[])))
        invalid=[]
        for phrase in s.get('phrases',[]):
            start,end=phrase.get('start_ms',0),phrase.get('end_ms',0)
            if not 0<=start<end<=row['duration_ms']+1000:
                invalid.append(dict(phrase_id=phrase.get('phrase_id'),reason='phrase_bounds'))
            for word in phrase.get('words',[]):
                if not start<=word['start_ms']<word['end_ms']<=end:
                    invalid.append(dict(phrase_id=phrase.get('phrase_id'),reason='word_bounds',word=word['text']))
        summary=dict(title=row['title'],song_id=row['song_id'],agreement=round(agreement,4),
            windows=len(c.get('rechecks',[])),accepted=c.get('accepted'),unresolved=c.get('needs_review'),
            quarantined_credits=len(c.get('quarantined_phrases',[])),
            aligned_words=measured,total_words=total,alignment=al,errors=d.get('errors',[]),
            recheck_errors=c.get('errors',[]),complete=d.get('completed'),manual_protected=row['manual'],
            timing_violations=invalid)
        summaries.append(summary)
        lines.append(f"| {row['title']} | {round((row.get('duration_ms') or 0)/1000)} | {agreement:.0%} | "
            f"{summary['windows']} | {summary['accepted']} | {summary['unresolved']} | "
            f"{al.get('phrases_aligned',0)} / {al.get('phrases_partially_aligned',0)} / {al.get('phrases_total',0)} |")
    complete=sum(bool(s['complete']) and not s['errors'] and not s['recheck_errors'] for s in summaries)
    lines+=['',f"Завершено без ошибок выполнения: {complete} из {len(inventory_rows)} записей.",
        '',f"Проверок звука: {sum(s['windows'] for s in summaries)}; "
        f"принятых правок: {sum(s['accepted'] or 0 for s in summaries)}; "
        f"нарушений временных границ: {sum(len(s['timing_violations']) for s in summaries)}.",
        f"Служебных вставок, сохранённых отдельно для проверки: {sum(s['quarantined_credits'] for s in summaries)}.",
        '', 'Последняя колонка относится к меткам времени слов, а не к полноте текста фразы. '
        'Временные метки — оценки выравнивателя, а не ручная разметка. '
        'Неизмеренные слова остаются в тексте с приблизительной подсветкой. '
        'Отсутствие спорных вариантов не доказывает отсутствие ошибок: обе модели могут ошибиться одинаково.',
        '', 'Число принятых правок показывает только замены, независимо подтверждённые обеими моделями '
        'при повторном чтении звука. Это не количество всех исправленных ошибок относительно предыдущих карточек.',
        '', 'Тексты, сырые гипотезы и журнал проверок: отдельный каталог UUID каждой песни, файл result.json.'
        + (' Результаты старшей модели находятся в qwen-large-refined/.' if model=='1.7' else ''),
        '', '«Прошлое — Виктор» из первоначального списка в текущих 16 карточках не найдено; '
        'результат для отсутствующей записи не заявляется.']
    errors=[dict(song_id=s['song_id'],title=s['title'],errors=s['errors'],
                 recheck_errors=s['recheck_errors'],complete=s['complete'])
            for s in summaries if s['errors'] or s['recheck_errors'] or not s['complete']]
    if errors:
        lines+=['','Незавершённые записи или ошибки выполнения:',
                '```json',json.dumps(errors,ensure_ascii=False,indent=2),'```']
    prefix='' if model=='1.7' else 'baseline-'
    save(output/(prefix+'quality-summary.json'),summaries)
    (output/(prefix+'report.md')).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('REPORT',output/(prefix+'report.md'),flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--phase',choices=('recognize','refine','finalize','refine-large','report'),required=True)
    p.add_argument('--jobs',type=int,default=2)
    p.add_argument('--song',action='append',help='Проверить только указанную карточку; инвентаризация остаётся полной')
    p.add_argument('--report-model',choices=('0.6','1.7'),default='1.7')
    args=p.parse_args();root=args.root.resolve();output=args.output.resolve()
    if root==output or root in output.parents:raise SystemExit('Вывод должен быть вне рабочей библиотеки')
    output.mkdir(parents=True,exist_ok=True)
    if args.phase=='report':
        report(output,args.report_model);return
    inventory_rows=inventory(root);save(output/'inventory.json',inventory_rows)
    rows=[r for r in inventory_rows if not args.song or r['song_id'] in args.song]
    os.environ.setdefault('OMP_NUM_THREADS','4');os.environ.setdefault('MKL_NUM_THREADS','4')
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(2,args.jobs))) as pool:
        receipts=list(pool.map(lambda r:process(r,output,args.phase),rows))
    save(output/(args.phase+'-summary.json'),receipts)
    before={r['song_id']:r['card_sha256'] for r in inventory_rows}
    after={r['song_id']:r['card_sha256'] for r in inventory(root)}
    save(output/'card-integrity.json',dict(unchanged=before==after,before=before,after=after))
    print('TOTAL',len(receipts),'OK',sum(r['ok'] for r in receipts),flush=True)


if __name__=='__main__':main()
