"""Explicit offline comparison of installed Qwen sizes on isolated library outputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'core'))
from songs.worker import qwen_transcribe,save,snapshot


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--experiment',type=Path,required=True)
    parser.add_argument('--song',action='append',required=True)
    args=parser.parse_args()
    rows=json.loads((args.experiment/'inventory.json').read_text())
    repo='Qwen/Qwen3-ASR-1.7B'
    model=Path(snapshot(repo))
    # Hugging Face LFS blob filenames are the published SHA256 identities.
    # Verify the newly installed weights before this larger-model trial.
    checks={}
    for path in model.glob('*.safetensors'):
        resolved=path.resolve();digest=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
        actual=digest.hexdigest()
        if len(resolved.name)!=64 or actual!=resolved.name:
            raise ValueError('Контрольная сумма весов не совпала: '+path.name)
        checks[path.name]=actual
    if not checks:raise ValueError('Нет локальных весов модели')
    verified=args.experiment/f'qwen-large-integrity-{os.getpid()}.json'
    save(verified,checks)
    verified.replace(args.experiment/'qwen-large-integrity.json')
    for row in rows:
        if row['song_id'] not in args.song:continue
        target=args.experiment/row['song_id']/'qwen-large.json'
        if target.is_file():continue
        print('START',row['title'],flush=True)
        started=time.monotonic()
        with tempfile.TemporaryDirectory(prefix='dewiar-qwen-comparison-') as tmp:
            decoded=Path(tmp)/'audio.wav'
            subprocess.run([shutil.which('ffmpeg'),'-v','error','-nostdin','-i',row['audio'],
                            '-vn','-c:a','pcm_f32le',str(decoded)],check=True,timeout=90)
            hyp=qwen_transcribe(decoded,lambda s:print(row['title'],s,flush=True),model_repo=repo)
        hyp.update(seconds=round(time.monotonic()-started,2),audio_sha256=row['audio_sha256'],
                   model_revision=model.name)
        save(target,hyp)
        print('DONE',row['title'],hyp['seconds'],flush=True)


if __name__=='__main__':main()
