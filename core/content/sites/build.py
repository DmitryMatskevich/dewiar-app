"""Build a private source snapshot with existing Astro dependencies.

Unchanged public media is read from the configured source during local preview;
it is not duplicated into every build and this artifact is not a full deploy.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from . import astro,base


def build(plan,edition,site,root,builds):
    baseline=astro.capture(site,root);base.validate_plan(plan,edition,site,baseline);root=Path(baseline['root'])
    cli=root/'node_modules/astro/astro.js';node=shutil.which('node')
    if not node or not cli.is_file():raise ValueError('Для локальной сборки нужны Node.js и уже установленные зависимости Astro этого проекта')
    builds=Path(builds);builds.mkdir(mode=0o700,parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='astro-preview-',dir=builds));os.chmod(stage,0o700)
    shutil.copytree(root/'src',stage/'src')
    for name in ('package.json','package-lock.json','tsconfig.json','jsconfig.json'):
        if (root/name).is_file():shutil.copy2(root/name,stage/name)
    shutil.copy2(root/'astro.config.mjs',stage/'source.config.mjs')
    # Detect source edits during the copy before patching only this snapshot.
    for relative,digest in baseline['hashes'].items():
        target=stage/('source.config.mjs' if relative=='astro.config.mjs' else relative)
        if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise ValueError('stale_baseline: источник изменился во время создания снимка')
    (stage/'src/data/pages.json').write_text(json.dumps(astro.patched_pages(plan,baseline),ensure_ascii=False,indent=2),encoding='utf-8')
    (stage/'node_modules').symlink_to(root/'node_modules',target_is_directory=True)
    (stage/'empty-public').mkdir()
    config="import original from './source.config.mjs';\nexport default {...original, publicDir:'./empty-public', outDir:'./dist', vite:{...(original.vite||{}),cacheDir:'./.vite-content-cache'}};\n"
    (stage/'astro.config.mjs').write_text(config,encoding='utf-8')
    env={**os.environ,'ASTRO_TELEMETRY_DISABLED':'1'}
    log=stage/'build.log'
    try:
        with log.open('wb') as output:
            result=subprocess.run([node,str(cli),'build'],cwd=stage,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,timeout=180)
    except subprocess.TimeoutExpired as e:raise ValueError('Сборка превысила 180 секунд; журнал: '+str(log)) from e
    if result.returncode:raise ValueError('Сборка Astro завершилась ошибкой; журнал: '+str(log))
    dist=stage/'dist';relative=plan['changes']['path'].strip('/')+'/index.html';article=dist/relative
    category=dist/(plan['changes']['category_path'].strip('/')+'/index.html');sitemap=dist/'sitemap.xml'
    if not article.is_file() or not category.is_file() or not sitemap.is_file():raise ValueError('Сборка не создала статью, рубрику или sitemap')
    if 'https://'+site['domain']+plan['changes']['path'] not in sitemap.read_text():raise ValueError('Адрес статьи отсутствует в sitemap')
    if plan['changes']['path'] not in category.read_text():raise ValueError('Статья отсутствует в списке рубрики')
    receipt={'status':'preview_built','publication_status':'not_published','site_id':site['id'],'package_hash':plan['package_hash'],
             'edition_revision':edition['revision'],'source_baseline_sha256':baseline['sha256'],'stage':str(stage),'dist':str(dist),
             'public_root':str(root/'public'),'article_path':plan['changes']['path'],'category_path':plan['changes']['category_path'],
             'article_sha256':hashlib.sha256(article.read_bytes()).hexdigest(),'html_count':sum(1 for _ in dist.rglob('*.html')),
             'scope':'local_preview_with_reused_public_assets','deployment_ready':False}
    (stage/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    return receipt
