"""Read a public lyric page in an isolated browser, without login or CAPTCHA bypass."""
import asyncio
import json
from pathlib import Path
import sys


async def read(url):
    from playwright.async_api import async_playwright
    from songs.sources import _public_http_url, _embedded_lyrics
    _public_http_url(url)
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(headless=True)
        try:
            page=await browser.new_page(locale='ru-RU')
            async def route(request):
                try:
                    _public_http_url(request.request.url)
                    if request.request.resource_type in {'media','image','font'}:
                        await request.abort()
                    else: await request.continue_()
                except (ValueError, OSError): await request.abort()
            await page.route('**/*',route)
            response=await page.goto(url,wait_until='domcontentloaded',timeout=18000)
            await page.wait_for_timeout(1000)
            body=await page.locator('body').inner_text(timeout=2000)
            if any(s in body.lower() for s in ('verify you are human',"make sure you're a human",'подтвердите, что вы не робот','access denied')):
                return {'status':'unavailable','error':'Сайт требует проверку в браузере. Откройте страницу и вставьте текст вручную.'}
            _public_http_url(page.url)
            lyrics=_embedded_lyrics(await page.content(),page.url)
            if not lyrics:return {'status':'unavailable','error':'Текст песни не найден в странице. Его можно вставить вручную.'}
            return {'status':'available','extracted_text':lyrics[:50000],
                    'final_url':page.url,'page_title':await page.title(),
                    'has_lyrics_text':True,'text_extraction':'browser_lyrics_container',
                    'http_status':response.status if response else None}
        finally:await browser.close()


def fetch(url):
    from .browser_search import invoke
    return invoke({'url':url}, script=Path(__file__).resolve())


if __name__=='__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    print(json.dumps(asyncio.run(read(str(json.load(sys.stdin).get('url','')))),ensure_ascii=False))
