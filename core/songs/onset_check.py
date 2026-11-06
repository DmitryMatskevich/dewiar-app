"""Conservative retry of an aligner stretching a first word over a musical break."""
import hashlib
import json
import re
from pathlib import Path
from .alignment_words import measured_words


def retry_stem_alignment(model, text, words, start_ms, end_ms, vocals):
    """Check a failed mixture alignment against two crops of the same vocal stem.

    Cropping just before the second word loses a first word followed by a pause.
    Keep the whole original window instead. Accept only complete, stable lexical
    alignments that retain the later anchors and have vocal evidence at onset.
    """
    count = len(re.findall(r'\S+', text))
    suspicious = suspicious_onset(words, vocals)
    if vocals is None or (not suspicious and len(words) >= count):
        return words, None
    import numpy as np
    samples, sr = vocals
    trials = []
    for offset in (0, 240):
        start = start_ms + offset
        if end_ms <= start:
            return words, None
        units = list(model.align(
            audio=(samples[round(start*sr/1000):round(end_ms*sr/1000)], sr),
            text=text, language='Russian')[0])
        trials.append(measured_words(text, units, start, end_ms))
    report = dict(reason='incomplete_or_stretched_mixture_alignment', applied=False,
                  status='vocal_alignment_not_confirmed',
                  retry_starts_ms=[t[0]['start_ms'] if t else None for t in trials],
                  trials=trials)
    if any(len(t) != count for t in trials) or not count:
        report['rejected']='missing_words'
        return words, report
    first, second = trials
    # Word attacks must be stable; sung tails/reverb have a wider boundary.
    if any(abs(a['start_ms']-b['start_ms']) > 160 or
           abs(a['end_ms']-b['end_ms']) > 600 for a,b in zip(first, second)):
        report['rejected']='unstable_words'
        return words, report
    # A window boundary is not evidence for the first syllable.
    if first[0]['start_ms'] < start_ms + 340 or suspicious_onset(first, vocals):
        report['rejected']='unconfirmed_onset'
        return words, report
    # Reliable later words keep this retry on the same occurrence of a chorus.
    anchors = {w['char_start']:w for w in first}
    original = words[1:] if suspicious else words
    if any(w['char_start'] not in anchors or
           abs(anchors[w['char_start']]['start_ms']-w['start_ms']) > 600 or
           abs(anchors[w['char_start']]['end_ms']-w['end_ms']) > 900 for w in original):
        report['rejected']='moved_later_words'
        return words, report
    if suspicious:
        # Do not turn a genuinely sustained vowel into an instrumental break.
        onset = first[0]['start_ms']
        def rms(a, b):
            block=samples[max(0,round(a*sr/1000)):min(len(samples),round(b*sr/1000))]
            return float(np.sqrt(np.mean(block**2))) if len(block) else 0
        voice=rms(onset, min(onset+700, first[0]['end_ms']+200))
        if voice <= 1e-4 or rms(onset-900, onset-100) >= voice*.22:
            report['rejected']='no_preceding_vocal_pause'
            return words, report
    chosen=min(trials,key=lambda t:sum(w['end_ms']-w['start_ms'] for w in t))
    report.update(applied=True, status='stable_vocal_alignment', start_ms=chosen[0]['start_ms'])
    return chosen, report


def existing_vocals(audio):
    """Reuse only a ready stem of the card's exact recording; never download."""
    for root in list(Path(audio).resolve().parents)[:6]:
        card, manifest = root/'song.json', root/'karaoke/manifest.json'
        if not card.is_file() or not manifest.is_file():
            continue
        try:
            row=json.loads(card.read_text()); meta=json.loads(manifest.read_text())
            source=(root.parent/row['audio']['path']).resolve()
            if root not in source.parents or not source.is_file():
                return None
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            candidate=next(c for c in meta['candidates'] if
                           c.get('candidate_id')==meta.get('selected_candidate') and
                           c.get('technical_status')=='ready' and c.get('source_sha256')==digest)
            path=(root/'karaoke'/candidate['artifact']).resolve().with_name('vocals.wav')
            return path if (root/'karaoke').resolve() in path.parents and path.is_file() else None
        except (OSError,ValueError,KeyError,StopIteration):
            return None
    return None


def suspicious_onset(words, vocals):
    if len(words)<3 or words[0]['char_start']!=0:
        return False
    first=words[0]
    if first['end_ms']-first['start_ms']>=4000:
        return True
    if vocals is None or first['end_ms']-first['start_ms']<1200:
        return False
    import numpy as np
    samples,sr=vocals
    def energy(a,b):
        block=samples[max(0,round(a*sr/1000)):min(len(samples),round(b*sr/1000))]
        return float(np.sqrt(np.mean(block**2))) if len(block) else 0
    next_voice=energy(words[1]['start_ms'],words[2]['end_ms'])
    return next_voice>1e-4 and energy(first['start_ms'],first['end_ms'])<next_voice*.2


def onset_supported(original, trials, vocals):
    """Two stable local alignments AND a preceding quiet vocal stem are required."""
    if len(original)<3 or len(trials)!=2 or any(len(w)<3 for w in trials) or vocals is None:
        return False
    first=original[0]
    if not suspicious_onset(original,vocals):
        return False
    for trial in trials:
        word=trial[0]
        if (word['char_start'],word['char_end'])!=(0,first['char_end']):
            return False
        if not 60 <= word['end_ms']-word['start_ms'] <= 2000:
            return False
        if word['start_ms']<first['start_ms']+1500:
            return False
        # The retry cannot move the later words to another occurrence.
        later={w['char_start']:w for w in trial[1:]}
        if any(w['char_start'] not in later or
               abs(later[w['char_start']]['start_ms']-w['start_ms'])>250 or
               abs(later[w['char_start']]['end_ms']-w['end_ms'])>350 for w in original[1:]):
            return False
    if abs(trials[0][0]['start_ms']-trials[1][0]['start_ms'])>160:
        return False
    import numpy as np
    samples,sr=vocals
    onset=max(t[0]['start_ms'] for t in trials)
    a,b=round((onset-900)*sr/1000),round((onset-100)*sr/1000)
    c,d=round(onset*sr/1000),round(min(onset+700,trials[0][0]['end_ms']+200)*sr/1000)
    if a<0 or b<=a or d<=c or d>len(samples):
        return False
    quiet=float(np.sqrt(np.mean(samples[a:b]**2)))
    voice=float(np.sqrt(np.mean(samples[c:d]**2)))
    return voice>1e-4 and quiet < voice*.22


def retry_long_onset(model, samples, sr, text, words, end_ms, vocals):
    if not suspicious_onset(words,vocals):
        return words,None
    report={'reason':'long_or_unvoiced_first_word','original_start_ms':words[0]['start_ms'],'applied':False}
    if vocals is None:
        report['status']='needs_vocal_evidence'
        return words,report
    trials=[]
    for context in (2500,1500):
        start=max(words[0]['start_ms'],words[1]['start_ms']-context)
        units=list(model.align(audio=(samples[round(start*sr/1000):round(end_ms*sr/1000)],sr),
                               text=text,language='Russian')[0])
        trials.append(measured_words(text,units,start,end_ms))
    report['retry_starts_ms']=[trial[0]['start_ms'] if trial else None for trial in trials]
    if onset_supported(words,trials,vocals):
        report.update(applied=True,status='stable_alignment_and_vocal_pause',start_ms=trials[0][0]['start_ms'])
        return trials[0],report
    report['status']='not_confirmed'
    return words,report
