"""Source discovery and audio acquisition, with explicit recording identity."""
import json
import hashlib
import html
import ipaddress
import re
import socket
import threading
import uuid
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen
from urllib.error import HTTPError


def _tokens(value):
    return re.findall(r"[a-zа-яё0-9]+", str(value or "").casefold().replace('ё', 'е'))


def _rank(candidate, title, artist):
    wanted_title, wanted_artist = _tokens(title), _tokens(artist)
    got_title, got_artist = _tokens(candidate.get('title')), _tokens(candidate.get('artist'))
    def overlap(a, b):
        return len(set(a) & set(b)) / max(1, len(set(a)))
    inferred_artist = [token for token in wanted_title if token in got_artist]
    clean_title = [token for token in wanted_title if token not in inferred_artist]
    title_tokens = clean_title or wanted_title
    title_score = max(overlap(title_tokens, got_title), SequenceMatcher(None, ' '.join(title_tokens), ' '.join(got_title)).ratio())
    artist_target = wanted_artist or inferred_artist
    artist_pool = got_artist + (got_title if wanted_artist else [])
    artist_score = overlap(artist_target, artist_pool) if artist_target else 0.5
    candidate['match_score'] = round(.78 * title_score + .22 * artist_score, 3)
    candidate['artist_match'] = round(artist_score, 3)
    return candidate


class _VisibleText(HTMLParser):
    """Small dependency-free text reader for verifying a direct reference page."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.title_depth = 0
        self.title = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        # <sup> holds footnote marks («Гоп³», «приком⁴»): glued to a word they
        # turn every line into a false correction.
        if tag in {'script', 'style', 'noscript', 'svg', 'sup'}:
            self.hidden += 1
        elif tag == 'title':
            self.title_depth += 1
        if tag in {'br', 'p', 'div', 'li', 'tr', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript', 'svg', 'sup'} and self.hidden:
            self.hidden -= 1
        elif tag == 'title' and self.title_depth:
            self.title_depth -= 1
        if tag in {'p', 'div', 'li', 'tr', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            text = html.unescape(data)
            self.parts.append(text)
            if self.title_depth:
                self.title.append(text)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _embedded_lyrics(document, url):
    """Read Musixmatch's public hydration data, without running page scripts.

    Its initial HTML can contain only a title; the same public JSON is used by
    the browser to render the lyrics. Never treat arbitrary scripts, metadata,
    search snippets or suggested tracks as the requested song's text.
    """
    host=(urlsplit(url).hostname or '').lower()
    if host in {'stihi.ru', 'www.stihi.ru', 'proza.ru', 'www.proza.ru'}:
        class Poem(HTMLParser):
            def __init__(self):
                super().__init__(); self.depth=0; self.parts=[]
            def handle_starttag(self, tag, attrs):
                if tag=='div' and dict(attrs).get('class','').split()==['text'] and not self.depth:
                    self.depth=1; return
                if self.depth:
                    if tag=='div': self.depth+=1
                    if tag in {'br','p'}: self.parts.append('\n')
            def handle_endtag(self, tag):
                if self.depth and tag=='div': self.depth-=1
            def handle_data(self, data):
                if self.depth: self.parts.append(data)
        parser=Poem(); parser.feed(document)
        return re.sub(r'\n{3,}', '\n\n', ''.join(parser.parts)).strip()
    if host == 'amdm.ru' or host.endswith('.amdm.ru'):  # mirrors: 496.amdm.ru
        # The song's text with chords; the rest of the page is site menus.
        block = re.search(r'<pre[^>]*itemprop="chordsBlock"[^>]*>(.*?)</pre>', document, re.S)
        return html.unescape(re.sub(r'<[^>]+>', '', block.group(1))).strip() if block else ''
    if host in {'mychords.net', 'www.mychords.net'}:
        # Lyrics with chords: one <div class="single-line"> per line inside
        # <div class="w-words__text">; chords are spans, removed later with
        # every other chord-only line (lyrics_only).
        class Words(HTMLParser):
            def __init__(self):
                super().__init__(); self.depth=0; self.parts=[]
            def handle_starttag(self, tag, attrs):
                classes=(dict(attrs).get('class') or '').split()
                if tag=='div' and 'w-words__text' in classes and not self.depth:
                    self.depth=1; return
                if self.depth:
                    if tag=='div':
                        self.depth+=1; self.parts.append('\n')
                    elif tag=='br':
                        self.parts.append('\n')
            def handle_endtag(self, tag):
                if self.depth and tag=='div': self.depth-=1
            def handle_data(self, data):
                if self.depth: self.parts.append(data)
        parser=Words(); parser.feed(document)
        text='\n'.join(line.strip() for line in ''.join(parser.parts).replace('\xa0',' ').splitlines())
        return re.sub(r'\n{3,}', '\n\n', text).strip()
    if host in {'genius.com', 'www.genius.com'}:
        class Lyrics(HTMLParser):
            def __init__(self):
                super().__init__(); self.depth=0; self.hidden=0; self.parts=[]
            def handle_starttag(self, tag, attrs):
                if dict(attrs).get('data-lyrics-container') == 'true':
                    self.depth=1; self.parts.append('\n'); return
                if self.depth:
                    if dict(attrs).get('data-exclude-from-selection')=='true':self.hidden=self.depth+1
                    if tag == 'br': self.parts.append('\n')
                    elif tag not in {'img','input','hr','wbr','meta','link'}: self.depth+=1
            def handle_endtag(self, tag):
                if self.depth and tag not in {'br','img','input','hr','wbr','meta','link'}:
                    if self.depth==self.hidden:self.hidden=0
                    self.depth-=1
            def handle_data(self, data):
                if self.depth and not self.hidden: self.parts.append(data)
        parser=Lyrics(); parser.feed(document)
        text=''.join(parser.parts).strip()
        # Section headings are not sung words; retain the actual line breaks.
        text=re.sub(r'^\s*\[[^\]\n]+\]\s*$', '', text, flags=re.M)
        return re.sub(r'\n{3,}', '\n\n', text).strip()
    if host not in {'musixmatch.com','www.musixmatch.com'}:
        return ''
    class Data(HTMLParser):
        def __init__(self):
            super().__init__();self.active=False;self.parts=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='script':self.active=attrs.get('id')=='__NEXT_DATA__' and attrs.get('type')=='application/json'
        def handle_data(self,data):
            if self.active:self.parts.append(data)
        def handle_endtag(self,tag):
            if tag=='script':self.active=False
    parser=Data();parser.feed(document)
    try:
        data=json.loads(''.join(parser.parts))
        info=data['props']['pageProps']['data']['trackInfo']['data']
        lyrics=info['lyrics']['body']
        if not isinstance(lyrics,str) or not 20<=len(lyrics)<=50000:return ''
        return lyrics.strip()
    except (KeyError,ValueError,TypeError):
        return ''


def _public_http_url(url):
    parsed = urlsplit(str(url or '').strip())
    host = (parsed.hostname or '').lower().rstrip('.')
    if parsed.scheme not in {'http', 'https'} or not host or parsed.username or parsed.password:
        raise ValueError('Ссылка должна вести на публичную страницу http или https')
    if host in {'localhost', 'localhost.localdomain'}:
        raise ValueError('Локальные адреса нельзя проверять как внешний источник')
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addresses = {ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80))}
        except OSError as exc:
            raise ValueError('Не удалось определить адрес сайта') from exc
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError('Ссылка ведёт на непубличный сетевой адрес')
    return parsed


def _decode_reference_html(body, content_type=''):
    """Respect legacy poetry pages' declared encoding instead of losing Cyrillic."""
    from email.message import Message
    header = Message()
    header['Content-Type'] = content_type
    declared = header.get_content_charset()
    # HTML5 charset and legacy http-equiv declarations both contain charset=.
    # Inspect only meta tags, not scripts or text quoting another document.
    meta = next((m.group(1).decode('ascii', 'ignore')
                 for tag in re.findall(br'<meta\b[^>]*>', body[:8192], re.I)
                 for m in [re.search(br'charset\s*=\s*[\"\']?\s*([a-z0-9_.:-]+)', tag, re.I)]
                 if m), None)
    candidates = (['utf-8-sig'] if body.startswith(b'\xef\xbb\xbf') else [])
    candidates += [declared, meta, 'utf-8']
    for encoding in dict.fromkeys(c for c in candidates if c):
        try:
            return body.decode(encoding), encoding
        except (LookupError, UnicodeError, ValueError):
            continue
    return body.decode('utf-8', 'replace'), 'utf-8-replacement'


def check_reference(url, candidate_kind='reference'):
    """Fetch a direct source page, validate redirects, and extract only visible chord lines."""
    current = str(url or '').strip()
    opener = build_opener(_NoRedirect())
    for _ in range(6):
        parsed = _public_http_url(current)
        request = Request(current, headers={'User-Agent':'Mozilla/5.0 Dewiar-Songs/0.1',
                                             'Accept':'text/html,application/xhtml+xml'})
        try:
            response = opener.open(request, timeout=12)
        except HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308} and exc.headers.get('Location'):
                current = urljoin(current, exc.headers['Location'])
                continue
            return {'status':'unavailable','http_status':exc.code,
                    'error':f'Страница ответила кодом HTTP {exc.code}'}
        with response:
            status = int(response.status)
            content_type = (response.headers.get('Content-Type') or '').lower()
            body = response.read(2_000_001)
            final_url = response.geturl()
        if len(body) > 2_000_000:
            return {'status':'unavailable','http_status':status,'error':'Страница слишком большая для безопасной проверки'}
        _public_http_url(final_url)
        if status < 200 or status >= 300:
            return {'status':'unavailable','http_status':status,'error':f'Страница ответила кодом HTTP {status}'}
        if 'html' not in content_type:
            return {'status':'unavailable','http_status':status,'error':'По ссылке нет страницы HTML'}
        parser = _VisibleText()
        decoded, encoding = _decode_reference_html(body, content_type)
        parser.feed(decoded)
        visible = re.sub(r'[ \t\xa0]+', ' ', ''.join(parser.parts))
        visible = re.sub(r'\n\s*\n+', '\n', visible).strip()
        lyric_body = _embedded_lyrics(decoded, final_url)
        if lyric_body:
            visible = lyric_body
        if not visible:
            return {'status':'unavailable','http_status':status,'error':'На странице не удалось прочитать текст'}
        chord_pattern = re.compile(r'^[A-G](?:#|b)?(?:m7b5|m(?:aj)?\d*|maj\d*|dim\d*|aug\d*|sus\d*|add\d*|\d+)?(?:/[A-G](?:#|b)?)?$')
        chord_names = []
        for line in visible.splitlines():
            line = line.strip().strip('|')
            if len(line) > 120:
                continue
            tokens = re.findall(r'(?<!\w)[A-G](?:#|b)?(?:m7b5|m(?:aj)?\d*|maj\d*|dim\d*|aug\d*|sus\d*|add\d*|\d+)?(?:/[A-G](?:#|b)?)?(?!\w)', line)
            residue = re.sub(r'(?<!\w)[A-G](?:#|b)?(?:m7b5|m(?:aj)?\d*|maj\d*|dim\d*|aug\d*|sus\d*|add\d*|\d+)?(?:/[A-G](?:#|b)?)?(?!\w)', '', line)
            if tokens and not residue.strip(' ,;|') and all(chord_pattern.fullmatch(chord) for chord in tokens):
                chord_names.extend(tokens)
        chord_names = list(dict.fromkeys(chord_names))[:80]
        page_title = re.sub(r'\s+', ' ', ''.join(parser.title)).strip()[:240]
        return {'status':'available','http_status':status,'final_url':final_url,
                'text_encoding':encoding,
                'page_title':page_title,'checked_at':__import__('time').time(),
                'has_readable_text':True,'chord_names':chord_names,
                'text_extraction':('poem_text_container' if (urlsplit(final_url).hostname or '').removeprefix('www.') in {'stihi.ru','proza.ru'} else 'genius_lyrics_container' if 'genius.com' in (urlsplit(final_url).hostname or '') else 'musixmatch_public_json') if lyric_body else 'html_visible',
                'has_lyrics_text':bool(lyric_body),
                'chords_extractable':len(chord_names)>=2,
                'extracted_text':visible[:50000],
                'note':'Доступность страницы проверена; соответствие исполнению всё ещё нужно сверить.'}
    return {'status':'unavailable','error':'Слишком много перенаправлений'}


def discover(query, references=None, title=None, artist=''):
    import yt_dlp
    candidates, errors = [], []
    ranking_artist = artist
    if not ranking_artist and re.search(r'[а-яё]', query, re.I):
        trailing_latin = re.search(r'([a-z][a-z0-9._-]*)\s*$', query, re.I)
        if trailing_latin:
            ranking_artist = trailing_latin.group(1)
    # Search title/artist both together and separately: YouTube's flat search can
    # bury an exact match when the query contains transliteration or extra words.
    queries = list(dict.fromkeys([query, re.sub(r"\s+", " ", query.replace('ZZarubin', 'Z Zarubin')), query.split(' — ')[0]]))
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'skip_download': True,
                              'extract_flat': True, 'socket_timeout': 15, 'retries': 1}) as dl:
            entries = []
            for search_query in queries:
                result = dl.extract_info('ytsearch10:'+search_query, download=False)
                entries.extend((result or {}).get('entries') or [])
        for item in entries:
            video_id = item.get('id', '')
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
                continue
            candidates.append(_rank({'candidate_id': 'youtube:'+video_id, 'kind': 'youtube',
                'title': item.get('title', ''), 'artist': item.get('channel') or item.get('uploader') or '',
                'duration': item.get('duration'), 'url': 'https://www.youtube.com/watch?v='+video_id,
                'version_verified': False}, title or query, ranking_artist))
    except Exception as exc:
        errors.append({'kind': 'youtube', 'error': str(exc)[:300]})
    try:
        request = Request('https://lrclib.net/api/search?'+urlencode({'q': query}),
                          headers={'User-Agent': 'Dewiar-Songs/0.1 (personal music study)'})
        with urlopen(request, timeout=15) as response:
            rows = json.loads(response.read(2_000_001))
        for row in rows[:10]:
            candidates.append({'candidate_id': 'lrclib:'+str(row['id']), 'kind': 'lyrics',
                'title': row.get('trackName'), 'artist': row.get('artistName'),
                'duration': row.get('duration'), 'url': 'https://lrclib.net/api/get/'+str(row['id']),
                'plain_lyrics': str(row.get('plainLyrics') or '')[:100_000],
                'synced_lyrics': str(row.get('syncedLyrics') or '')[:100_000],
                'version_verified': False})
    except Exception as exc:
        errors.append({'kind': 'lrclib', 'error': str(exc)[:300]})
    # Preserve supplied URLs and search the chord source explicitly. AMDM pages
    # are references only; they are never treated as a verified recording.
    for url in references or []:
        candidates.append({'candidate_id': 'reference:'+hashlib.sha256(url.encode('utf-8')).hexdigest()[:16], 'kind': 'reference',
            'title': 'Источник, указанный пользователем', 'artist': '', 'url': url,
            'note': 'Ссылка пользователя — проверьте соответствие версии песни.', 'version_verified': False})
    from urllib.parse import quote
    slug = re.sub(r'[^a-z0-9а-яё]+', '_', query.casefold()).strip('_')
    if slug:
        candidates.append({'candidate_id': 'yandex-search:'+slug, 'kind': 'search',
            'title': query+' — поиск в Яндекс Музыке', 'artist': '',
            'url': 'yandexmusic://search?text='+quote(query),
            'note': 'Откройте результаты в приложении, выберите нужное исполнение, затем повторно считайте текущий трек.',
            'version_verified': False})
        for suffix, label, note in [
            ('аккорды site:amdm.ru/akkordi', 'Поиск аккордов AMDM', 'Это поисковая выдача, а не найденная страница с аккордами. Откройте результат и проверьте его.'),
            ('текст песни', 'Поиск текста песни', 'Это поисковая выдача. Откройте конкретную публикацию, затем проверьте доступность и версию.'),
        ]:
            candidates.append({'candidate_id': 'web-search:'+slug+':'+label, 'kind': 'search',
                'title': query+' — '+label, 'artist': '',
                'url': 'https://www.google.com/search?q='+quote(query+' '+suffix),
                'note': note, 'version_verified': False})
    unique = {}
    for item in candidates:
        unique.setdefault(item['candidate_id'], item)
    candidates = [c for c in unique.values() if c.get('kind') != 'youtube' or (
        c.get('match_score', 0) >= .4 and (not ranking_artist or c.get('artist_match', 0) >= .25))]
    candidates = sorted(candidates, key=lambda c: (c.get('match_score', 0), c.get('kind') == 'reference'), reverse=True)[:30]
    return candidates, errors


class SongSources:
    def __init__(self, store, emit, analyzer=None):
        self.store, self.emit, self.analyzer = store, emit, analyzer
        self._lock, self._jobs, self._checks = threading.RLock(), {}, {}

    def running(self, song_id):
        with self._lock:
            return song_id in self._jobs

    def cancel(self, song_id):
        with self._lock:
            if song_id in self._jobs:
                self._jobs[song_id].set()

    def check_candidate(self, song_id, candidate_id):
        with self._lock:
            if song_id in self._checks and self._checks[song_id].is_alive():
                raise ValueError('Проверка страницы для этой песни уже выполняется')
            row = self.store.get(song_id)
            if not row:
                raise ValueError('Песня не найдена')
            candidate = next((c for c in row.get('source_candidates', [])
                              if c.get('candidate_id') == candidate_id), None)
            if not candidate or candidate.get('kind') not in {'reference', 'lyrics', 'chords'}:
                raise ValueError('Выберите прямую ссылку на страницу, а не поисковый запрос')
            url = candidate.get('url') or ''
            if not url.startswith(('https://', 'http://')):
                raise ValueError('Эту ссылку нельзя проверить как веб-страницу')
            candidate['source_check'] = {'status':'checking'}
            updated = self.store.patch(song_id, {'source_candidates':row['source_candidates']})
            thread = threading.Thread(target=self._check_candidate, args=(song_id,candidate_id,url),
                                      name='song-source-check-'+song_id[:8], daemon=True)
            self._checks[song_id] = thread
            thread.start()
        self.emit('song_analysis', song_id=song_id, running=False, song=updated)
        return updated

    def _check_candidate(self, song_id, candidate_id, url):
        try:
            result = check_reference(url)
            with self.store._lock:
                row = self.store.get(song_id)
                if not row:
                    return
                candidates = row.get('source_candidates', [])
                candidate = next((c for c in candidates if c.get('candidate_id') == candidate_id), None)
                if not candidate:
                    return
                candidate['source_check'] = result
                if result.get('status') == 'unavailable':
                    candidate['note'] = 'Страница не прошла проверку доступности; ссылка скрывается из итоговых источников.'
                updated = self.store.patch(song_id, {'source_candidates':candidates})
            self.emit('song_analysis', song_id=song_id, running=False, song=updated)
        except Exception as exc:
            with self.store._lock:
                row = self.store.get(song_id)
                if not row:
                    return
                candidates = row.get('source_candidates', [])
                candidate = next((c for c in candidates if c.get('candidate_id') == candidate_id), None)
                if candidate:
                    candidate['source_check'] = {'status':'unavailable','error':str(exc)[:240]}
                    candidate['note'] = 'Страница не прошла проверку доступности; ссылка скрывается из итоговых источников.'
                    updated = self.store.patch(song_id, {'source_candidates':candidates})
                else:
                    return
            self.emit('song_analysis', song_id=song_id, running=False, song=updated)

    def start(self, song_id, candidate_id=None, process=False):
        with self._lock, self.store._lock:
            row = self.store.get(song_id)
            if not row:
                raise ValueError('Песня не найдена')
            if self.running(song_id) or row.get('status') in {'running', 'recording'}:
                raise ValueError('Сначала завершите текущий этап')
            candidate = None
            if candidate_id:
                if row.get('audio', {}).get('path') or row.get('phrases'):
                    raise ValueError('Для другой записи создайте отдельную карточку')
                candidate = next((c for c in row.get('source_candidates', [])
                                  if c.get('candidate_id') == candidate_id and c.get('kind') == 'youtube'), None)
                if not candidate:
                    raise ValueError('Выберите найденную запись YouTube')
            run = str(uuid.uuid4())
            cancel = threading.Event()
            self._jobs[song_id] = cancel
            updated = self.store.patch(song_id, {'status': 'running', 'stage': 'sources',
                'active_run_id': run, 'stage_label': 'Сохраняем выбранную запись' if candidate else 'Ищем записи и опубликованный текст'})
            threading.Thread(target=self._run, args=(row, run, cancel, candidate, bool(process)),
                             name='song-sources-'+song_id[:8], daemon=True).start()
            return updated

    def select_reference(self, song_id, candidate_id):
        with self.store._lock:
            row = self.store.get(song_id)
            if not row:
                raise ValueError('Песня не найдена')
            if row.get('status') in {'running','recording'}:
                raise ValueError('Дождитесь завершения текущего этапа')
            candidates = row.get('source_candidates', [])
            candidate = next((c for c in candidates if c.get('candidate_id') == candidate_id), None)
            if not candidate or candidate.get('kind') not in {'reference','lyrics','chords','youtube'}:
                raise ValueError('Выберите конкретную запись или страницу с текстом/аккордами')
            for item in candidates:
                item['selected'] = item is candidate
            has_recording = bool(row.get('audio',{}).get('path') or row.get('phrases'))
            label = ('Источник выбран для сверки с текущей записью; пакетный разбор этой версии доступен в отдельной карточке' if has_recording
                     else 'Запись выбрана; нажмите «Обработать пакетно», чтобы начать разбор') if candidate.get('kind') == 'youtube' else 'Источник выбран для сверки; проверьте совпадение текста и версии с записью'
            updated = self.store.patch(song_id, {'source_candidates':candidates,
                'selected_source_id':candidate_id,'stage_label':label})
        self.emit('song_analysis', song_id=song_id, running=False, song=updated)
        return updated

    def _run(self, row, run, cancel, candidate, process=False):
        song_id = row['song_id']
        process_audio = False
        try:
            if candidate:
                from media_transcription import _audio
                folder = self.store.root/song_id/'audio'/run
                folder.mkdir(parents=True, exist_ok=True)
                path, info = _audio(candidate['url'], folder, cancel)
                changes = {'audio': {'path': str(path.relative_to(self.store.root)),
                    'format': path.suffix[1:], 'duration_ms': round(float(info.get('duration') or 0)*1000),
                    'owned': True, 'original_name': info.get('title') or path.name},
                    'source': 'url', 'source_ref': candidate['url'],
                    'recording_version': info.get('title') or candidate['title'],
                    'sources': [*row.get('sources', []), {**candidate, 'selected': True}],
                    'status': 'queued', 'stage': 'lyrics', 'stage_label': 'Запись сохранена — готовим пакетный разбор' if process else 'Запись сохранена — можно начать полный разбор',
                    'capabilities': {**row.get('capabilities', {}), 'audio': True}}
                process_audio = process
            else:
                search_query = ' '.join([row.get('search_query') or row['title'], row.get('artist', '')]).strip()
                candidates, errors = discover(search_query,
                    [s.get('url') for s in row.get('sources', []) if s.get('kind') == 'reference' and s.get('url')],
                    title=row['title'], artist=row.get('artist', ''))
                has_audio = bool(row.get('audio',{}).get('path'))
                retained = [c for c in row.get('source_candidates', []) if c.get('kind') == 'reference' or str(c.get('candidate_id','')).startswith('reference:')]
                ids = {c.get('candidate_id') for c in retained}
                changes = {'source_candidates': [*retained, *[c for c in candidates if c.get('candidate_id') not in ids]],
                    'status': row.get('status','review') if has_audio else 'needs_input',
                    'stage': row.get('stage','review') if has_audio else 'sources',
                    'stage_label': ('Источники для сверки найдены' if has_audio else 'Выберите исполнение: версии песни могут отличаться') if candidates else 'Источники не найдены; сохранённая запись доступна' if has_audio else 'Источники не найдены; можно импортировать запись',
                    'sources': [*row.get('sources', []), *[{'kind': 'source_error', 'provider':e['kind'], 'error':e['error']} for e in errors]]}
            with self.store._lock:
                fresh = self.store.get(song_id) or {}
                if cancel.is_set() or fresh.get('active_run_id') != run:
                    return
                updated = self.store.patch(song_id, {**changes, 'active_run_id': None})
            self.emit('song_analysis', song_id=song_id, running=False, song=updated)
        except Exception as exc:
            with self.store._lock:
                fresh = self.store.get(song_id) or {}
                if cancel.is_set() or fresh.get('active_run_id') != run:
                    return
                updated = self.store.patch(song_id, {'status': 'failed', 'active_run_id': None,
                    'stage_label': 'Ошибка источника: '+str(exc)[:180]})
            self.emit('song_analysis', song_id=song_id, running=False, song=updated)
        finally:
            with self._lock:
                self._jobs.pop(song_id, None)
        if process_audio and self.analyzer:
            try:
                self.analyzer.start(song_id, full=True)
            except Exception as exc:
                fresh = self.store.get(song_id) or {}
                if fresh.get('audio', {}).get('path'):
                    updated = self.store.patch(song_id, {'status': 'failed',
                        'stage_label': 'Запись загружена, пакетный разбор не запустился: '+str(exc)[:180]})
                    self.emit('song_analysis', song_id=song_id, running=False, song=updated)
