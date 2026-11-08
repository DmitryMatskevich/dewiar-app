"""Free Yandex + Google search in an isolated browser process.

Adapted from china_search's browser_search provider: rendered DOM, normalized
external links, deduplication, bounded time and explicit CAPTCHA diagnostics.
No paid API, user browser profile, login, stealth or CAPTCHA solving.
"""
import asyncio
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode, urlsplit, parse_qs, urldefrag

ENGINES={
    'yandex':'https://yandex.ru/search/?',
    'google':'https://www.google.com/search?',
}


def search_url(engine, query):
    return ENGINES[engine]+urlencode({'text':query} if engine=='yandex' else {'q':query,'hl':'ru'})


def normalize_hit(item, engine):
    url=str(item.get('href') or '')
    if url.startswith('/'):url=ENGINES[engine].split('/search')[0]+url
    parsed=urlsplit(url)
    for key in ('url','q','uddg'):
        target=parse_qs(parsed.query).get(key,[''])[0]
        if target.startswith(('http://','https://')):url=target;break
    parsed=urlsplit(url);host=(parsed.hostname or '').lower()
    if parsed.scheme not in {'https','http'} or not host or parsed.username or parsed.password:return None
    if any(host==s or host.endswith('.'+s) for s in ('google.com','yandex.ru','yandex.com','ya.ru','google.ru','googleusercontent.com','gstatic.com')):return None
    title=' '.join(str(item.get('title') or '').split())
    if len(title)<3:return None
    return {'url':urldefrag(url)[0],'title':title[:240],'provider':engine}


async def search_rendered(query, engines=None):
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        context=await browser.new_context(locale='ru-RU')
        async def one(engine):
            page=await context.new_page()
            try:
                await page.goto(search_url(engine,query),wait_until='domcontentloaded',timeout=12000)
                await page.wait_for_timeout(800)
                text=(await page.locator('body').inner_text(timeout=2000))[:8000].lower()
                if ('showcaptcha' in page.url or '/sorry/' in page.url or any(s in text for s in (
                    'подтвердите, что вы не робот','unusual traffic','verify you are human','проверка пользователя'))):
                    return [],{'engine':engine,'status':'captcha','message':'Поисковик запросил проверку в браузере','url':search_url(engine,query)}
                items=await page.locator('a').evaluate_all("""aa=>aa.filter(a=>a.getClientRects().length)
                    .map(a=>({href:a.href,title:a.innerText,heading:!!a.querySelector('h2,h3')}))
                    .sort((a,b)=>Number(b.heading)-Number(a.heading))""")
                hits=[];seen=set()
                for item in items:
                    hit=normalize_hit(item,engine)
                    if hit and hit['url'] not in seen:seen.add(hit['url']);hits.append(hit)
                return hits[:5],{'engine':engine,'status':'ok' if hits else 'empty','count':len(hits[:5])}
            except Exception as exc:
                return [],{'engine':engine,'status':'error','message':type(exc).__name__}
            finally:await page.close()
        try:
            results=await asyncio.gather(*(one(e) for e in (ENGINES if engines is None else engines) if e in ENGINES))
            hits=[];seen=set()
            # Interleave so one search engine cannot consume all page slots.
            for rank in range(5):
                for rows,_ in results:
                    if rank<len(rows) and rows[rank]['url'] not in seen:
                        seen.add(rows[rank]['url']);hits.append(rows[rank])
            return {'hits':hits,'engines':[d for _,d in results]}
        finally:await browser.close()


def search(query, engines=None):
    payload={'query':query}
    if engines is not None:payload['engines']=list(engines)
    return invoke(payload)


def invoke(payload, script=None):
    runtime=Path.home()/'Library/Application Support/Dewiar/runtimes/song-search/bin/python'
    if not runtime.is_file():return {'hits':[],'engines':[{'status':'unavailable','message':'Браузерный модуль поиска не установлен'}]}
    process=subprocess.Popen([str(runtime),'-B',str(script or Path(__file__).resolve())],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
    try:
        stdout,_=process.communicate(json.dumps(payload),timeout=30)
    except subprocess.TimeoutExpired:
        # Chromium belongs to this isolated process group, never the user's
        # browser. A timed-out worker must not leave its browser behind.
        os.killpg(process.pid,signal.SIGTERM)
        try:process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);process.communicate()
        raise RuntimeError('Браузерный поиск превысил время ожидания') from None
    if process.returncode:raise RuntimeError('Браузерный поиск недоступен; проверьте установку Chromium')
    return json.loads(stdout)


if __name__=='__main__':
    payload=json.load(sys.stdin)
    query=str(payload.get('query',''))[:1000]
    print(json.dumps(asyncio.run(search_rendered(query,payload.get('engines'))),ensure_ascii=False))
