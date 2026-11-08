#!/usr/bin/env python3
"""Manage the independent local monitor. Never starts the Dewiar Engine."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import hashlib
import shutil
import shlex
import subprocess
import sys
import time
import webbrowser

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP/'core'))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('action', choices=['start','open','install','status','stop'])
    a=p.parse_args()
    from settings import STATE_DIR, CONFIG_DIR
    from sites.client import endpoint
    root=STATE_DIR/'sites-monitor'
    label='com.dewiar.sites-monitor'
    plist=Path.home()/'Library/LaunchAgents'/f'{label}.plist'
    if a.action=='install':
        if sys.platform!='darwin':
            raise SystemExit('Автозапуск этой командой поддерживается на macOS')
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        plist.parent.mkdir(parents=True,exist_ok=True)
        # Install a small independent release outside the Documents development tree.
        runtime=CONFIG_DIR/'sites-runtime'
        release=runtime/'current'
        (release/'core').mkdir(parents=True,exist_ok=True)
        (release/'ui').mkdir(exist_ok=True)
        shutil.copytree(APP/'core/sites',release/'core/sites',dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(APP/'ui/sites',release/'ui/sites',dirs_exist_ok=True)
        for name in ('vault','crypt','settings','platform_info','mail','mail_keychain','telegram','maxbot','egress'):
            shutil.copy2(APP/'core'/(name+'.py'),release/'core'/(name+'.py'))
        manifest={str(f.relative_to(release)):hashlib.sha256(f.read_bytes()).hexdigest() for f in release.rglob('*') if f.is_file() and f.name!='manifest.json'}
        (release/'manifest.json').write_text(json.dumps(manifest,indent=2))
        python=runtime/'venv/bin/python'
        if not python.exists():
            subprocess.run([str(Path(sys.executable).resolve()),'-m','venv',str(runtime/'venv')],check=True)
            subprocess.run([str(python),'-m','pip','install','httpx==0.28.1'],check=True)
        subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        spec={'Label':label,'ProgramArguments':[str(python),'-B','-m','sites.server','--root',str(root),'--config',str(CONFIG_DIR)],'WorkingDirectory':str(release/'core'),'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':15,'Nice':5,'StandardOutPath':str(root/'service.log'),'StandardErrorPath':str(root/'service.log'),'EnvironmentVariables':{'PYTHONDONTWRITEBYTECODE':'1','DEWIAR_CONFIG_DIR':str(CONFIG_DIR),'DEWIAR_STATE_DIR':str(STATE_DIR)}}
        plist.write_bytes(plistlib.dumps(spec));plist.chmod(0o600)
        shortcut=runtime/'Открыть сайты.command'
        shortcut.write_text('#!/bin/zsh\ncd '+shlex.quote(str(release/'core'))+' || exit 1\nexec '+shlex.quote(str(python))+' -B -m sites.client\n')
        shortcut.chmod(0o700)
        # bootout returns before launchd has necessarily released the label.
        for attempt in range(10):
            result=subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(plist)],capture_output=True,text=True)
            if result.returncode==0:
                break
            if attempt==9:
                raise SystemExit('Не удалось зарегистрировать автозапуск: '+result.stderr.strip())
            time.sleep(1)
        for attempt in range(30):
            try:
                endpoint(start=False)
                break
            except ValueError:
                if attempt==29:
                    raise SystemExit('Автозапуск зарегистрирован, но служба не ответила. Проверьте service.log.')
                time.sleep(.5)
        print('Установлен автозапуск при входе текущего пользователя. Сон компьютера останавливает наблюдение.')
    elif a.action=='stop':
        if plist.exists():
            subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{label}'],check=False)
            plist.unlink()
            print('Автозапуск отключён; данные сохранены')
        else:
            print('Для паузы используйте веб-интерфейс. Неизвестные процессы не завершаются.')
    else:
        ep=endpoint(start=a.action!='status')
        if a.action=='open':
            webbrowser.open(ep['url'])
        print('Служба доступна: '+ep['base'])


if __name__=='__main__':main()
