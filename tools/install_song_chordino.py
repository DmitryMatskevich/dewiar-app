"""Явная сборка Chordino для локального Mac; настроек/голосов не касается.

Нужны clang++, cmake и Homebrew libsndfile/boost. Сохраняет лицензии и
точные исходники GPL рядом с отдельным исполняемым файлом, вне .app.
"""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SOURCES={
    'nnls-chroma':('https://github.com/c4dm/nnls-chroma','4c5f214a75cb354f8d8c933e161377c5b4d83713'),
    'vamp-plugin-sdk':('https://github.com/vamp-plugins/vamp-plugin-sdk','44c2487763eb248a933e9eff9169cfadee375009'),
}


def run(*args,**kwargs):subprocess.run(args,check=True,**kwargs)


if __name__=='__main__':
    work=ROOT/'work/song-verification';work.mkdir(parents=True,exist_ok=True)
    brew=Path(subprocess.check_output(['brew','--prefix'],text=True).strip())
    if not (brew/'include/boost/tokenizer.hpp').exists() or not (brew/'include/sndfile.h').exists():
        raise SystemExit('Для сборки нужны boost и libsndfile: brew install boost libsndfile')
    for name,(url,revision) in SOURCES.items():
        folder=work/name
        if not folder.exists():
            run('git','clone','--no-checkout',url,str(folder))
            run('git','-C',str(folder),'checkout',revision)
        actual=subprocess.check_output(['git','-C',str(folder),'rev-parse','HEAD'],text=True).strip()
        if actual!=revision or subprocess.check_output(['git','-C',str(folder),'diff','--name-only'],text=True):
            raise SystemExit('Исходник отличается от закреплённой версии: '+name)
    build=work/'vamp-build'
    run('cmake','-S',str(work/'vamp-plugin-sdk'),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release','-DBUILD_SHARED_LIBS=OFF')
    run('cmake','--build',str(build),'--target','vamp-hostsdk','-j','4')
    destination=Path.home()/'Library/Application Support/Dewiar/runtimes/chordino'
    (destination/'bin').mkdir(parents=True,exist_ok=True)
    temporary=destination/'bin/chordextract.new'
    run('clang++','-std=c++17','-D_VAMP_PLUGIN_IN_HOST_NAMESPACE','-O2',
        '-I'+str(work/'vamp-plugin-sdk'),'-I'+str(brew/'include'),
        *[str(work/'nnls-chroma'/p) for p in ('chordextract.cpp','Chordino.cpp','NNLSBase.cpp','chromamethods.cpp','viterbi.cpp','nnls.c')],
        str(build/'libvamp-hostsdk.a'),'-L'+str(brew/'lib'),'-lsndfile','-lboost_iostreams',
        '-framework','Accelerate','-o',str(temporary))
    temporary.replace(destination/'bin/chordextract')
    for name,(_,revision) in SOURCES.items():
        target=destination/'sources'/(name+'-'+revision)
        if not target.exists():shutil.copytree(work/name,target,ignore=shutil.ignore_patterns('.git','*.o','chordextract'))
    (destination/'dictionary').mkdir(exist_ok=True) # Use Chordino's built-in dictionary.
    manifest={'sources':{k:{'url':v[0],'revision':v[1]} for k,v in SOURCES.items()},
              'sha256':hashlib.sha256((destination/'bin/chordextract').read_bytes()).hexdigest(),
              'dependencies':['Homebrew libsndfile','Homebrew boost_iostreams','Apple Accelerate']}
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Chordino installed:',destination)
