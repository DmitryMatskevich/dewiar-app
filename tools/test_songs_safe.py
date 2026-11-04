"""Адресные проверки песен с изоляцией и контролем рабочей конфигурации.

Не запускает Dewiar/микрофон/TTS и не устанавливает модели. Изменение рабочего
файла обнаруживается, но не откатывается: его мог изменить сам пользователь.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'core'))
from platform_info import data_paths
TESTS=('song_pipeline_unittest.py','song_capture_unittest.py','song_cached_model_unittest.py','song_benchmark_unittest.py','song_full_unittest.py','song_evidence_unittest.py')


def snapshot():
    config,state=data_paths()
    settings=config/'settings.json'
    return {'settings_sha256':hashlib.sha256(settings.read_bytes()).hexdigest() if settings.exists() else None,
            'models':{str(p.relative_to(state)):[p.stat().st_size,p.stat().st_mtime_ns]
                      for folder in ('models','voices') for p in (state/folder).rglob('*') if p.is_file()}}


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--report',type=Path)
    args=parser.parse_args(); before=snapshot(); outcomes=[]
    with tempfile.TemporaryDirectory(prefix='dewiar-song-tests-') as tmp:
        env=dict(os.environ,DEWIAR_CONFIG_DIR=str(Path(tmp)/'config'),DEWIAR_STATE_DIR=str(Path(tmp)/'state'),
                 DEWIAR_WORKSPACE_DIR=str(Path(tmp)/'workspace'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
        for name in TESTS:
            start=time.monotonic()
            r=subprocess.run([sys.executable,str(ROOT/'tests'/name)],env=env,cwd=ROOT,capture_output=True,text=True,timeout=60)
            outcomes.append({'test':name,'returncode':r.returncode,'seconds':round(time.monotonic()-start,3)})
            print(r.stdout+r.stderr,end='')
    after=snapshot()
    report={'tests':outcomes,'working_settings_unchanged':before['settings_sha256']==after['settings_sha256'],
            'installed_model_files_unchanged':before['models']==after['models']}
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
    if not report['working_settings_unchanged'] or not report['installed_model_files_unchanged']:
        raise SystemExit('Рабочие файлы изменились во время проверки. Не откатываю: изменение мог сделать пользователь.')
    raise SystemExit(1 if any(r['returncode'] for r in outcomes) else 0)

if __name__ == '__main__': main()
