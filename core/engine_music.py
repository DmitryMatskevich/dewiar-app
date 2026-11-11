"""Движок Dewiar: быстрые команды без модели — дела по расписанию и утренняя сводка, музыка и радио, умный дом,
сейф, громкость и питание компьютера; плюс фоновые циклы ночного дневника и проверки радиостанций.
Примесь класса Engine (core/engine.py): методы смешиваются в него, self — это Engine.
Команды *_cmd(text) разбирают фразу своим парсером (jobs.py, music.py, smarthome.py, vault.py, pc.py) и возвращают
True, если ответили сами; False — фразу получит модель. Ответ одной фразой — через _say_quick (engine_tools.py)."""
from engine_base import *  # noqa: F401,F403 — общие импорты и помощники движка


class MusicMixin:
    @staticmethod
    def _explicit_external_music(text):
        source=r'(?:яндекс|yandex|youtube|ютуб|spotify|радио)'
        low=text.casefold().strip(' .!?')
        if not re.search(source,low) or re.search(r'(?:не|без)\b.{0,35}'+source,low):return False
        return bool(re.fullmatch(source+r'(?:\s+(?:музыка|музыке|music))?',low) or
                    re.search(r'\b(?:найди|ищи|поищи|открой|включи|поставь|играй|запусти|переключи|искать)\b.*'+source,low))

    def _local_song_context(self):
        context=getattr(self,'_song_context',None)
        scope=getattr(getattr(self,'chatlog',None),'active','legacy')
        if context and context['scope']==scope and time.monotonic()<context['until']:
            return context
        return None

    def _remember_local_songs(self, rows, action):
        self._song_context={'ids':[r['song_id'] for r in rows], 'action':action,
                            'scope':getattr(getattr(self,'chatlog',None),'active','legacy'),
                            'until':time.monotonic()+1800}

    def _local_song_control(self, action='play'):
        context=self._local_song_context()
        if not context:return None
        ids=context['ids']
        if len(ids)!=1:
            return 'В локальном сборнике найдено несколько песен. Назовите песню или её номер в списке.'
        if action=='play':return self._song_library_action({'action':'play','song_id':ids[0]})
        if action in ('pause','stop','play_pause'):
            self.emit('song_library_action',action=action,songs=[{'song_id':ids[0]}])
            return 'Команда передана локальному проигрывателю.'
        return 'Уточните, какую песню локального сборника включить.'

    def _guard_local_music(self, kind, args):
        if not self._local_song_context():return None
        if kind=='media':return self._local_song_control(args.get('action','play'))
        query=str(args.get('query') or '').strip()
        if query:return self._song_library_action({'action':'play','query':query})
        return self._local_song_control('play')

    def _local_song_followup(self, text):
        context=self._local_song_context()
        if not context:return False
        low=text.casefold().strip(' .!?')
        if re.search(r'яндекс|yandex|youtube|ютуб|spotify|радио',low):return False
        from music import control
        action=control(text)
        if not action and re.search(r'\b(?:играй|плей|play)\b',low) and not re.search(r'\b(?:песню|трек)\s+\S+|\bне\s+(?:играй|включай|запускай|нажимай)',low):
            action='play'
        if action:
            self._say_quick(self._local_song_control(action),text)
            return True
        if len(context['ids'])>1:
            ordinals={'первую':0,'первая':0,'вторую':1,'вторая':1,'третью':2,'третья':2,'четвертую':3,'четвёртую':3,'пятую':4}
            choice=ordinals.get(low)
            if low.isdigit():choice=int(low)-1
            if choice is not None and 0<=choice<len(context['ids']):
                result=self._song_library_action({'action':context['action'] if context['action'] in ('play','open') else 'open','song_id':context['ids'][choice]})
                self._say_quick(result,text);return True
            # A short title following a list is a selection within that list.
            if not getattr(self,'_music_source_pending',None) and len(low.split())<=8 and low not in ('да','нет','спасибо','отмена','хорошо') and not re.search(r'найди|открой|покажи|включи|поставь|напомни|погода|сколько|почему',low):
                result=self._song_library_action({'action':context['action'] if context['action'] in ('play','open') else 'open','query':text,'candidate_ids':context['ids']})
                if result.startswith('Не нашёл'):return False
                self._say_quick(re.sub(r'\s*\[id:[^\]]+\]','',result),text);return True
        return False

    def _song_library_action(self, args):
        """Run a guarded, fuzzy lookup against the user's own saved song cards."""
        import difflib
        import unicodedata
        action=str(args.get('action') or 'search')
        search_in=str(args.get('search_in') or 'auto')
        store=self.song_store
        query=str(args.get('query') or '').strip()
        query=re.sub(r'^(?:по\s+(?:словам|тексту|фразе)|со\s+словами|где\s+поется)\s*[:—-]?\s*','',query,flags=re.I)
        def norm(value):
            value=unicodedata.normalize('NFKD',str(value or '').casefold().replace('ё','е'))
            return ' '.join(''.join(ch for ch in value if not unicodedata.combining(ch)).split())
        def tokens(value):
            return [re.sub(r'[^a-zа-я0-9]','',part) for part in norm(value).split() if re.sub(r'[^a-zа-я0-9]','',part)]
        if action=='playlist':
            tag=str(args.get('tag') or query).strip()
            aliases={'шансон':'Шансон','шансончик':'Шансон','мужчинам':'Мужская','мужская':'Мужская',
                     'для мужчин':'Мужская','любовь':'Любовь','грустное':'Грусть','веселое':'Юмор'}
            tag=aliases.get(norm(tag),tag)
            available=self.song_store.list(limit=500,compact=True)
            known=list(dict.fromkeys(item for row in available for item in row.get('tags',[])))
            if tag not in known:
                near=sorted(((difflib.SequenceMatcher(None,norm(tag),norm(item)).ratio(),item) for item in known),reverse=True)
                if near and near[0][0]>=.72:tag=near[0][1]
            rows=store.list(limit=100,compact=True,tag=tag)
            rows=[r for r in rows if r.get('audio',{}).get('path')]
            if not rows:return f'В библиотеке нет песен с тегом «{tag}» и доступной записью.'
            self.emit('song_library_action',action='playlist',tag=tag,songs=[{'song_id':r['song_id'],'title':r['title'],'artist':r.get('artist','')} for r in rows])
            return f'Запускаю подборку «{tag}»: {len(rows)} песен.'
        rows=store.list(limit=500,compact=True)
        if args.get('candidate_ids'):
            rows=[r for r in rows if r['song_id'] in args['candidate_ids']]
        exact=next((r for r in rows if args.get('song_id') and r['song_id']==args['song_id']),None)
        if action=='edit':
            target=exact
            if target is None and query:
                target=next((r for r in rows if norm(r.get('title'))==norm(query)),None)
            if not target:return 'Не удалось однозначно определить песню. Сначала найди её и уточни название.'
            changes={k:args[k] for k in ('title','artist','tags') if k in args}
            if not changes:return 'Назови, что изменить: название, исполнителя или теги.'
            saved=store.patch(target['song_id'],changes,target['revision'])
            self.emit('song_library_updated',song_id=target['song_id'])
            return f'Обновил карточку «{saved["title"]}».'
        if not exact:
            if not query:return 'Назови песню или исполнителя.'
            nq=norm(query);query_tokens=tokens(query);scored=[]
            for r in rows:
                identity=norm(r.get('title','')+' '+r.get('artist',''))
                lyric_phrases=[str(p.get('text') or '').strip() for p in r.get('phrases',[]) if isinstance(p,dict) and p.get('text')]
                lyrics=norm(' '.join(lyric_phrases))
                hay=(norm(r.get('title','')) if search_in=='title' else
                     norm(r.get('artist','')) if search_in=='artist' else
                     lyrics if search_in=='lyrics' else identity+' '+lyrics if search_in=='meaning' else identity)
                row_tokens=tokens(hay)
                token_coverage=0.0
                if len(query_tokens)>1:
                    matched=sum(any(token==candidate or (len(token)>=5 and difflib.SequenceMatcher(None,token,candidate).ratio()>=.82)
                                    for candidate in row_tokens) for token in query_tokens)
                    token_coverage=.9*matched/len(query_tokens)
                field_scores=([difflib.SequenceMatcher(None,nq,norm(r.get(k,''))).ratio() for k in ('title','artist')]
                              if search_in=='auto' else [])
                score=max([token_coverage,difflib.SequenceMatcher(None,nq,hay).ratio(),*field_scores])
                if search_in in ('auto','artist') and len(query_tokens)==1 and len(nq)>=5:
                    # A spoken surname may be inflected: «Высоцкого» vs
                    # «Владимир Высоцкий». Compare it to individual name tokens.
                    score=max(score,max((difflib.SequenceMatcher(None,nq,part).ratio()
                                         for part in tokens(hay) if len(part)>=5),default=0))
                if nq in hay:score=max(score,.88)
                if search_in in ('auto','lyrics') and lyrics:
                    meaningful=[t for t in query_tokens if t not in {'и','а','но','в','на','у','с','по','что','как','из','не','я','ты'}]
                    for index,phrase in enumerate(lyric_phrases):
                        window=norm(' '.join(lyric_phrases[index:index+2]))
                        if nq in window:score=max(score,.95)
                        if len(meaningful)>=2:
                            words=tokens(window)
                            coverage=sum(any(t==w or (len(t)>=5 and difflib.SequenceMatcher(None,t,w).ratio()>=.8) for w in words) for t in meaningful)/len(meaningful)
                            if coverage>=.65:score=max(score,.9*coverage)
                if search_in=='meaning':score=max(score,.01)
                if score>=.34 or search_in=='meaning':scored.append((score,r))
            scored.sort(key=lambda x:(-x[0],norm(x[1].get('title'))))
            if not scored:return f'Не нашёл «{query}». Уточни название или исполнителя.'
            if search_in=='meaning':
                self._remember_local_songs([r for _,r in scored[:25]],action)
                choices=[]
                for _,r in scored[:25]:
                    snippets=[str(p.get('text') or '').strip() for p in r.get('phrases',[]) if isinstance(p,dict) and str(p.get('text') or '').strip()]
                    if len(snippets)>3:
                        excerpts=[snippets[0],snippets[len(snippets)//2],snippets[-1]]
                    else:excerpts=snippets
                    excerpt=' … '.join(p[:75] for p in excerpts)
                    choices.append(f'{r["title"]} — {r.get("artist") or "исполнитель не указан"} [id:{r["song_id"]}]'+(f': «{excerpt[:220]}»' if excerpt else ''))
                return 'Кандидаты для поиска по смыслу (сверь описание пользователя с фрагментами; пока ничего не включай): '+'; '.join(choices)
            # A weak single hit is still uncertain. Return candidates to the model so it
            # can ask the user instead of silently opening the wrong song.
            if scored[0][0]<.56 or (len(scored)>1 and scored[1][0]>=scored[0][0]-.08):
                options=[item for item in scored if item[0]>=scored[0][0]-.12][:5]
                self._remember_local_songs([r for _,r in options],action)
                return 'Неоднозначное название. Возможные варианты: '+ '; '.join(f'{r["title"]}'+(f' — {r["artist"]}' if r.get('artist') else '')+f' [id:{r["song_id"]}]' for _,r in options)+'. Какую песню выбрать?'
            exact=scored[0][1]
        self._remember_local_songs([exact],action)
        if action=='search':return f'Нашёл: «{exact["title"]}» — {exact.get("artist") or "исполнитель не указан"} [id:{exact["song_id"]}].'
        if action not in ('open','play'):return 'Неизвестное действие с библиотекой песен.'
        if action=='play' and not exact.get('audio',{}).get('path'):return f'У «{exact["title"]}» пока нет сохранённой аудиозаписи.'
        self.emit('song_library_action',action=action,songs=[{'song_id':exact['song_id'],'title':exact['title'],'artist':exact.get('artist','')}])
        return f'{"Открываю" if action=="open" else "Включаю"} «{exact["title"]}».'

    # ---------- дела по расписанию и утренняя сводка ----------

    def _job_cmd(self, text, via=None):
        """«Каждое утро присылай в Telegram …» — завести дело по расписанию; «сводку сейчас» — выполнить сразу.
        Бот не подключён — дело всё равно заводится, но с ответом голосом."""
        from jobs import parse
        from when import describe_repeat, human
        c = parse(text)
        if not c:
            return False
        if c["op"] == "brief_now":
            if via:
                return False                              # из Telegram — обычным ответом модели (там же и сводка)
            self.emit("heard", text=text)
            threading.Thread(target=self._run_job, args=({"text": "Утренняя сводка", "brief": True, "deliver": "voice"},),
                             daemon=True).start()
            return True
        deliver = c["deliver"]
        note = ""
        bot = self._bot_for(deliver)
        if bot is not None and not bot.paired():
            deliver, note = "voice", f" {bot.TITLE} пока не подключён — пока буду говорить голосом (подключить: студия → «Подключение»)."
        item = self.reminders.add(c["at"], c["task"], c["repeat"], "job", {"deliver": deliver, "brief": c["brief"]})
        # подтверждение — что это делает помощник сам, а не напоминание человеку (экзамен 25.10: «каждый час буду говорить
        # голосом — „Проверяй почту…“» судья принял за напоминание «проверь почту» — так и человек мог понять)
        tell = f"пришлю в {bot.TITLE}" if bot is not None and deliver != "voice" else "скажу голосом и покажу в виджете"
        msg = (f"Хорошо, беру это на себя: {describe_repeat(c['repeat'])} — «{item['text']}». Что получится — {tell}. "
               f"Первый раз — {human(c['at'])}.{note} Отменить — в студии, «Напоминания».")
        self.emit("reminder_set", item=self._reminder_public(item))
        self._say_quick(msg, text, via)
        return True

    def _bot_for(self, deliver):
        """Куда присылать дело: telegram / max → бот; голосом — None."""
        return {"telegram": self.telegram, "max": self.max}.get(deliver or "")

    def _brief_context(self):
        """Данные для утренней сводки текстом: дата, дела и события на сегодня, деньги, фоновые задачи.
        Погоду здесь не берём — её ищет модель по городу из памяти (см. _job_prompt)."""
        from money import fmt
        now = datetime.now()
        today = now.date().isoformat()
        cal = self.calendar_month(now.year, now.month)
        items = cal["days"].get(today, [])
        todo = [f"{x.get('time', '')} {x['title']}".strip() for x in items if x["type"] == "reminder" and not x.get("done")]
        meet = [f"{x.get('time') or 'весь день'} {x['title']}" + (f" ({x['place']})" if x.get("place") else "") for x in items if x["type"] == "event"]
        lines = [f"Сегодня {now:%d.%m.%Y}, {['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье'][now.weekday()]}, сейчас {now:%H:%M}."]
        lines.append("Дела и напоминания на сегодня: " + ("; ".join(todo) if todo else "нет") + ".")
        if meet:
            lines.append("Встречи и события из календаря: " + "; ".join(meet) + ".")
        y = (now.date() - timedelta(days=1)).isoformat()
        sy = self.money.summary(y, y)
        sm = self.money.summary(now.date().replace(day=1).isoformat(), today)
        for c in sm["by_currency"][:1]:
            cy = next((x for x in sy["by_currency"] if x["currency"] == c["currency"]), None)
            lines.append(f"Деньги: вчера потрачено {fmt(cy['expense'] if cy else 0, c['currency'])}; с начала месяца доходы "
                         f"{fmt(c['income'], c['currency'])}, расходы {fmt(c['expense'], c['currency'])}.")
        try:
            tasks = [t for t in self.tasks.list() if t.get("status") in ("running", "waiting", "sleeping")]
            if tasks:
                lines.append("Фоновые задачи в работе: " + "; ".join(t.get("title", "") for t in tasks[:3]) + ".")
            wait = [t for t in tasks if t.get("status") == "waiting" and t.get("question")]
            if wait:
                lines.append("Задача ждёт ответа пользователя: " + "; ".join(f"«{t.get('title', '')}» — {t.get('question', '')[:120]}" for t in wait[:2]) + ".")
        except Exception:  # noqa: BLE001
            pass
        try:
            night = self.night.morning_note()             # один раз: что было ночью (night.py)
            if night:
                lines.append("Ночной обход (скажи одной спокойной фразой, без технических слов): " + night)
        except Exception:  # noqa: BLE001
            pass
        return "\n".join(lines)

    def _job_prompt(self, item):
        """Поручение модели для дела по расписанию (или утренней сводки) — модель выполняет его сама, без вопросов."""
        if item.get("brief"):
            return (self._brief_context() + "\n\n"
                    "[Утренняя сводка] Составь по этим данным короткую утреннюю сводку для пользователя: поздоровайся по "
                    "имени, если знаешь; погода на сегодня в его городе (город — из памяти о нём; узнай инструментом "
                    "поиска; не знаешь города — пропусти погоду); дела и напоминания на сегодня; коротко про деньги; "
                    "одна добрая фраза на день. 4–7 коротких предложений, без списков с символами, без вопросов. Если "
                    "есть навык morning-brief — следуй его указаниям (use_skill).")
        return (f"[Дело по расписанию] Поручение пользователя: «{item.get('text', '')}». Выполни его сам прямо сейчас — "
                "используй инструменты (поиск в интернете, чтение страниц, заметки, календарь calendar_list, бюджет "
                "money_report). Ответь готовым коротким сообщением с результатом: без вопросов, без «я могу…», без "
                f"пересказа поручения. Сейчас {datetime.now():%d.%m.%Y %H:%M}.")

    JOB_RETRY_MIN = (5, 15)            # дело не удалось (модель или сеть) — ещё попытки через столько минут

    def _run_job(self, item, attempt=0):
        """Выполнить дело (в своём потоке): подождать до 3 минут, пока ассистент свободен (не перебивать разговор
        и будильник), и ответить туда, куда просили; бот не подключён — голосом. Не удалось — повтор через
        JOB_RETRY_MIN; все попытки мимо — уведомление и запись в журнал сбоев. Итог — в самом деле (last_run,
        last_ok, last_error: студия, «Напоминания», и ночной разбор)."""
        end = time.monotonic() + 180
        while time.monotonic() < end and (self.turn is not None or self.state in ("speaking", "thinking") or self.alarm):
            time.sleep(1.0)
        title = "Утренняя сводка" if item.get("brief") else item.get("text", "Дело по расписанию")
        prompt = self._job_prompt(item)
        log.info("🗓 По расписанию: %s%s", title, f" (попытка {attempt + 1})" if attempt else "")
        bot = self._bot_for(item.get("deliver"))
        to_bot = bot is not None and bot.paired()
        res, ev = {}, threading.Event()

        def report(ok, text):
            res.update(ok=ok, text=text)
            ev.set()
        if to_bot:
            self.ask(prompt, via={"done": lambda _a: None, "speak": False}, raw=True, shown=f"🗓 {title}", report=report)
        else:
            self.ask(prompt, raw=True, shown=f"🗓 {title}", report=report)
        ev.wait(900)
        ok, text = res.get("ok"), res.get("text") or "нет ответа за 15 минут"
        if ok and to_bot:
            bot.send(f"🗓 {title}\n\n{text}")
            self.emit("push", title=f"🗓 {title}", text=text[:300], critical=False)
        if ok is None and ev.is_set():
            return                                       # ход перебили (человек заговорил) — не сбой и не повтор
        try:
            self.reminders.update(item.get("id"), {"last_run": datetime.now().isoformat(timespec="seconds"), "last_ok": bool(ok),
                                                   "last_error": "" if ok else text[:200]})
        except Exception:  # noqa: BLE001
            pass
        if ok:
            return
        self.health_problem(f"Дело по расписанию: {text[:200]}", kind="job")
        if attempt < len(self.JOB_RETRY_MIN):
            log.info("🗓 «%s» не удалось (%s) — повторю через %d мин", title, text[:120], self.JOB_RETRY_MIN[attempt])
            if not self.stopping.wait(self.JOB_RETRY_MIN[attempt] * 60 * float(os.environ.get("DEWIAR_JOB_RETRY_SCALE", "1"))):
                self._run_job(item, attempt + 1)
            return
        msg = f"Не получилось выполнить «{title}»: {text[:200]}. Попробую в следующий раз по расписанию."
        if to_bot:
            bot.send(f"⚠ {msg}")
        self.emit("push", title="⚠ Дело по расписанию не выполнено", text=msg[:300], critical=True)

    # ---------- музыка: свой список радиостанций, без модели ----------

    def _music_service(self):
        """Где включать музыку: выбор в «Руках»; «сам выберу» — в Яндекс Музыке, если её открывали за последние полчаса."""
        sv = self.s.get("music", "service") or "auto"
        if sv in ("radio", "yandex", "youtube"):
            return sv
        name, at = getattr(self, "_music_app", ("", 0.0))
        return name if name and time.monotonic() - at < 1800 else "radio"

    def _player_cmd(self, act, text):
        """Кнопки открытого плеера (Яндекс Музыка, браузер): «играй», «пауза», «следующий трек» — без модели и без кликов."""
        import tools as _tools
        try:
            msg = _tools.t_media({"action": act}, self.toolbox.hooks)
        except _tools.ToolError as e:
            msg = str(e)[:1].upper() + str(e)[1:]
        msg = self._g(msg)
        log.info("🎛 %s → %s", text, msg)
        self.emit("heard", text=text)
        self.emit("answer", text=msg + ".", done=True)          # без голоса — музыку и так слышно
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": msg + ".", "who": self.s.get("persona")}]
        self.set_state("idle")
        return True

    def _yandex_cmd(self, query, text, exact=False, authorize_external=False):
        """Открыть поиск в Яндекс Музыке (приложение или сайт) и ответить без модели. Пустой query — просто открыть.
        Из запроса убираются лишние слова, а «электронную музыку» превращаем в «электронная музыка» — как жанр."""
        import tools as _tools
        if authorize_external or self._explicit_external_music(text):
            self._song_context=None
        elif self._local_song_context():
            self._say_quick(self._guard_local_music('search',{'query':query}),text)
            return True
        words = query.split()
        q = " ".join(re.sub(r"(\w{3,})ую$", r"\1ая", w) for w in words       # «электронную» → «электронная»
                     if w not in ("музыку", "музыка", "музычку", "мне", "пожалуйста", "что-нибудь", "какую-нибудь", "*"))
        if q and any(w.startswith("музык") for w in words) and re.search(r"ая$", q):
            q += " музыка"
        if exact:q = query.strip()
        _tools.t_yandex_music({"query": q}, self.toolbox.hooks)
        app = _tools.yandex_music_installed()
        msg = (f"Открываю в Яндекс Музыке: «{q}». Нажмите на трек — или скажите «играй»." if q else
               "Открываю Яндекс Музыку." if app else "Открываю Яндекс Музыку на сайте.")
        self._say_quick(msg, text)
        return True

    def _run_local_song_choice(self, query, text, action='play'):
        result=self._song_library_action({'action':action,'query':query})
        if result.startswith(('Включаю','Открываю','Нашёл:')):
            self._say_quick(result,text)
            return True
        if result.startswith('Неоднозначное название'):
            self._say_quick(re.sub(r'\s*\[id:[^\]]+\]', '', result),text)
            return True
        self._music_source_pending={'kind':'local_miss','query':query,'until':time.monotonic()+180}
        reason=(f'В локальной библиотеке у песни «{result.split("«",1)[1].split("»",1)[0]}» нет аудиозаписи. '
                if result.startswith('У «') and 'нет сохранённой аудиозаписи' in result else
                f'Не нашёл «{query}» в разделе «Задачи → Песни». ')
        self._say_quick(reason+'Искать в Яндекс Музыке?',text)
        return True

    def _music_source_followup(self, text):
        pending=getattr(self,'_music_source_pending',None)
        if not pending:
            return False
        if time.monotonic()>pending.get('until',0):
            self._music_source_pending=None
            return False
        low=text.casefold().replace('ё','е')
        if re.search(r'\b(?:найди|поищи|ищи|отыщи|открой|покажи|включи|поставь|сыграй|воспроизведи)\b',low):
            self._music_source_pending=None
            return False
        if re.search(r'яндекс\s*[- ]?музык|yandex\s*music|в\s+яндекс|^яндекс[.! ]*$',low):
            self._music_source_pending=None
            return self._yandex_cmd(pending['query'],text)
        if re.search(r'локал|сборник|библиотек|сохраненн|мои\s+песн|моя\s+коллекц',low):
            if pending.get('kind')=='local_miss':
                self._music_source_pending=None
                self._say_quick('Понял, буду искать только в локальной библиотеке.',text)
                return True
            self._music_source_pending=None
            return self._run_local_song_choice(pending['query'],text)
        if pending.get('kind')=='local_miss' and re.fullmatch(r'\s*(?:да|ага|давай|ищи|поищи|хорошо|ок(?:ей)?)[.! ]*',low):
            self._music_source_pending=None
            return self._yandex_cmd(pending['query'],text,authorize_external=True)
        if pending.get('kind')=='local_miss' and re.fullmatch(r'\s*(?:нет|не надо|не ищи|только локально|отмена)[.! ]*',low):
            self._music_source_pending=None
            self._say_quick('Хорошо, останусь в локальной библиотеке.',text)
            return True
        if pending.get('kind')=='source' and re.fullmatch(r'\s*(?:да|нет|не знаю|без разницы|все равно)[.! ]*',low):
            self._music_source_pending=pending
            self._say_quick('Выберите источник: локальная библиотека песен или Яндекс Музыка?',text)
            return True
        # A new unrelated request must not inherit a stale song/source question.
        self._music_source_pending=None
        return False

    def _music_cmd(self, text):
        """«Включи музыку» → спросим жанр; «поставь джаз», «что-нибудь спокойное», «дальше», «выключи музыку».
        «Играй», «пауза», «следующий трек» — кнопки открытого плеера; «… в Яндекс Музыке» — там."""
        from music import control, genre_tag, parse
        from media_intent import protected
        if protected(text):
            return False
        if self._music_source_followup(text):
            return True
        # Explicit playlist/tag requests refer to the user's curated song library,
        # not to a radio station. Keep station requests on the existing radio path.
        low=text.casefold().replace('ё','е')
        wants_playlist=bool(re.search(r"плейлист|подборк|включи музыку|поставь музыку|включи песни",low))
        if wants_playlist and not re.search(r"радио|станци|волн|яндекс|yandex|youtube|ютуб",low):
            tag=''
            for pattern,value in ((r"шансон",'Шансон'),(r"для мужчин|мужчин|мужская",'Мужская'),
                                  (r"любов",'Любовь'),(r"груст",'Грусть'),(r"юмор|весел",'Юмор')):
                if re.search(pattern,low):tag=value;break
            if not tag:
                for row in self.song_store.list(limit=500,compact=True):
                    tag=next((item for item in row.get('tags',[]) if item.casefold().replace('ё','е') in low), '')
                    if tag:break
            if tag:
                result=self._song_library_action({'action':'playlist','tag':tag})
                self._say_quick(result,text)
                return True
        # Search/open commands are often phrased outside music.parse(), so handle
        # them here before the model can route an unspecified source to Yandex.
        find_match=re.search(r'\b(найди(?:те)?|поищи(?:те)?|ищи|отыщи|открой(?:те)?|покажи(?:те)?)\b\s*(.*)',low)
        play_match=re.search(r'\b(включи(?:те)?|поставь(?:те)?|сыграй(?:те)?|играй|запусти(?:те)?|вруби(?:те)?|воспроизведи(?:те)?)\b\s*(.*)',low)
        request_match=find_match or play_match
        if request_match and re.search(r'песн|трек|композици|музык|библиотек|сборник',low) and not re.search(r'радио|станци|волн',low):
            query=request_match.group(2).strip().rstrip('.,!?»"\'')
            query=re.sub(r'\s+(?:(?:в|на)\s+)?песн\w*\s*$','',query,flags=re.I).strip()
            query=re.sub(r'\s+(?:(?:в|на)\s+)?(?:раздел\w*\s+)?задач\w*\s*$','',query,flags=re.I).strip()
            query=re.sub(r'\s+(?:(?:на|в|из)\s+)?(?:мо[её]й\s+|мо[её]м\s+)?(?:локальн\w*\s+)?(?:диск\w*|библиотек\w*|сборник\w*|коллекци\w*|раздел\w*(?:\s+задач\w*)?|песн\w*|сохраненн\w*)\s*$','',query,flags=re.I).strip()
            query=re.sub(r'\s+(?:(?:в|на|через)\s+)?(?:приложени\w+\s+)?яндекс(?:\s*[- ]?музык\w*)?\s*$','',query,flags=re.I).strip()
            query=re.sub(r'^(?:(?:песню|песня|песни|трек|треки|композицию|композиция)\s+)+','',query,flags=re.I).strip()
            query=re.sub(r'^(?:музыку|музыка|пожалуйста|мне)\s+','',query,flags=re.I).strip()
            query=re.sub(r'^(?:мне|пожалуйста)\s+','',query,flags=re.I).strip().strip(' «»\"\'.,!?')
            explicit_track=bool(re.search(r'песн|трек|композици|локальн|библиотек|сборник|диск|задач|яндекс',low))
            tokens=[word for word in re.findall(r'[a-zа-яё0-9]+',query) if word not in {'музыку','музыка','песню','песни','что','нибудь','какую','пожалуйста','мне'}]
            named_play=bool(play_match and tokens and (len(tokens)>1 or not genre_tag(query)))
            if query and (find_match or explicit_track or named_play):
                if re.search(r'яндекс\s*[- ]?музык|(?:в|на|через)\s+яндекс|yandex\s*music',low):
                    return self._yandex_cmd(query,text)
                action='open' if find_match else 'play'
                return self._run_local_song_choice(query,text,action)
        asked=time.monotonic()<self._music_asked
        failed_recently=time.monotonic()-getattr(self,'_music_failed',-1e9)<120
        c=parse(text,active=self.radio.playing or bool(self.radio.paused) or failed_recently,asked=asked)
        if c and c.get('op')=='play' and c.get('query') not in ('','*') and not re.search(r'радио|станци|волн',low):
            query=re.sub(r'^(?:(?:песню|песня|песни|трек|треки|композицию|композиция)\s+)+','',c['query'],flags=re.I).strip()
            query=re.sub(r'^(?:пожалуйста|мне)\s+','',query,flags=re.I).strip()
            query=re.sub(r'\s+(?:(?:в|из|на)\s+)?(?:мо[её]й\s+|мо[её]м\s+)?(?:локальн\w*\s+)?(?:библиотек\w*|сборник\w*|коллекци\w*|сохраненн\w*)\s*$','',query,flags=re.I).strip()
            query=re.sub(r'\s+(?:локально|в\s+локальном)\s*$','',query,flags=re.I).strip()
            if query and query not in ('музыку','музыка','песни','песню') and not genre_tag(query):
                local_source=bool(re.search(r'локал|сборник|библиотек|сохраненн|мои\s+песн|моя\s+коллекц',low))
                yandex_source=bool(re.search(r'яндекс\s*[- ]?музык|yandex\s*music|(?:в|на|через)\s+яндекс',low))
                other_source=bool(re.search(r'youtube\s*music|ютуб\s*музык|spotify|спотиф|вк\s*музык',low))
                if local_source:
                    return self._run_local_song_choice(query,text)
                if yandex_source:
                    return self._yandex_cmd(query,text)
                if not other_source:
                    return self._run_local_song_choice(query,text)
        act = control(text)
        if act == "play" and self.radio.playing and not self.radio.sounding and time.monotonic() - self.radio.last_at > 8:
            last = self.radio.station or self.radio.last         # «играет», а звука нет — станция зависла: включаем заново
            log.info("📻 «%s» — радио молчит (звук не приходит), перезапускаю", text)
            self.radio.stop()
            if last:
                rest = [x for x in self.music.stations() if x["url"] != last.get("url")]
                self._play_station([last] + rest[:3], text, "Радио молчало — включаю заново: «{}».")
                return True
        if act == "play" and self.radio.playing:          # «играй», а радио уже играет — скажем что и как громко
            st, v = self.radio.station or {}, round(self.radio.volume * 100)
            msg = f"Уже играет «{st.get('name', 'радио')}»" + (f", но тихо — громкость радио {v}%. Скажите «громче»." if v <= 20
                                                                else f", громкость радио {v}%.")
            log.info("📻 %s → %s", text, msg)
            self._say_quick(msg, text)
            return True
        if act and not (self.radio.playing or self.radio.paused):
            import tools as _tools
            explicit = re.search(r"нажми|жми|кнопк|play|плей|трек|песн|воспроизвед|проигрыв|пауз", text.lower())
            if explicit or (_tools.media_players() or 0) > 0 or self._music_service() == "yandex":
                return self._player_cmd(act, text)
            if act == "play":                             # ни радио, ни открытого плеера: снова последняя станция
                last = self.radio.last
                if last:
                    tags = set(last.get("tags", "").split())
                    rest = [x for x in self.music.stations() if x["url"] != last.get("url")]
                    rest.sort(key=lambda x: not (tags & set(x["tags"].split())))
                    log.info("📻 «%s» — плеера нет, включаю последнюю станцию «%s»", text, last.get("name"))
                    self._play_station([last] + rest[:3], text, "Включаю «{}».")
                    return True
                g = self.music.genres()[:6]                # ничего ещё не играло — спросим, что включить
                self._music_asked = time.monotonic() + 45
                self._say_quick("Что включить? Например: " + ", ".join(g) + " — или скажите название станции.", text)
                return True
        if not c:
            return False
        op = c["op"]
        if op == "app":
            return self._yandex_cmd("", text)
        if op == "play" and not self.radio.playing and (c.get("service") or self._music_service()) in ("yandex", "youtube"):
            sv = c.get("service") or self._music_service()
            if sv == "yandex":
                query=re.sub(r'^(?:(?:песню|песня|песни|трек|треки|композицию|композиция)\s+)+','',c["query"],flags=re.I).strip()
                return self._yandex_cmd(query, text)
            if not c["strict"] and not re.search(r"радио|станц|волн", text.lower()):
                import tools as _tools
                q = " ".join(w for w in c["query"].split() if w not in ("музыку", "музыка", "музычку"))
                _tools.t_play_music({"query": q}, self.toolbox.hooks)
                self._say_quick(f"Открываю YouTube Music{': «' + q + '»' if q else ''}.", text)
                return True
        if op == "ask" and self._music_service() == "yandex":
            return self._yandex_cmd("", text)
        if op == "stop":
            if not (self.radio.playing or self.radio.paused):
                return False                              # играет что-то другое (браузер, плеер) — решит модель
            self.radio.stop()
            self.radio.paused = None
            self._say_quick("Выключил музыку.", text)
            self.emit("radio", **self.radio.status())
            return True
        if op == "ask":
            g = self.music.genres()[:6]
            self._music_asked = time.monotonic() + 45
            self._say_quick("Какую музыку включить? Например: " + ", ".join(g) + " — или скажите название станции.", text)
            return True
        if op == "next":
            cur = self.radio.station or self.radio.paused or self.radio.last
            nxt = self.music.next_after(cur)
            if not nxt:
                return False
            tags = set((cur or {}).get("tags", "").split())
            rest = [x for x in self.music.stations() if x["url"] not in (nxt["url"], (cur or {}).get("url"))]
            rest.sort(key=lambda x: not (tags & set(x["tags"].split())))        # сначала тот же жанр
            self._play_station([nxt] + rest[:3], text, "Переключаю: «{}».")
            return True
        q = c["query"]
        if re.search(r"ютуб|youtube|spotify|спотиф|яндекс|\bвк\b|вконтакт|песн|трек|клип|альбом|видео|фильм|сериал", q):
            return False                                  # конкретная песня или сервис — это к модели (YouTube и т. п.)
        found = self.music.match(q)
        tag = "" if c["strict"] else genre_tag(q)
        if not found:
            if c["strict"] or not (re.search(r"радио|станц|волн|\bфм\b|\bfm\b", text.lower()) or tag):
                return False
            try:                                          # «включи радио Европа плюс», «включи электронную музыку» — каталог
                from radio import pick, search
                found = search(tag, as_tag=True) if tag and not re.search(r"радио|станц|волн|\bфм\b|\bfm\b", text.lower()) else search(q)
                best = pick(found, q)
                found = [best] + [x for x in found if x is not best] if best else []
            except Exception as e:  # noqa: BLE001
                log.info("Радио: каталог недоступен: %s", e)
                found = []
            if not found:
                return False
        self._music_asked = 0.0
        self._play_station(found[:4], text, "Включаю «{}».")
        return True

    def _home_cmd(self, text, via=None):
        """Умный дом Яндекса без модели: «включи свет в спальне», «весь свет на 30 процентов», «кондиционер на 22»,
        «запусти сценарий Спокойной ночи». Только если такое устройство (или сценарий) правда есть."""
        if not self.home.connected():
            return False
        from smarthome import HomeError, parse
        c = parse(text)
        if not c:
            return False
        try:
            info = self.home.info()
            if c["op"] == "scenario":
                sc = self.home.scenario(c["target"], info)
                if not sc:
                    return False
                self.home.run_scenario(sc)
                msg = f"Запустил сценарий «{sc['name']}»."
            else:
                devs = self.home.match(c["target"], info)
                if not devs:
                    return False
                n = self.home.act(devs, c["op"], c.get("value"))
                rooms = sorted({d.get("room_name") for d in devs if d.get("room_name")})
                what = (devs[0]["name"] if len(devs) == 1 else
                        ("свет" if all(str(d.get("type", "")).startswith("devices.types.light") for d in devs) else f"{len(devs)} устройства"))
                where = f" ({', '.join(rooms)})" if rooms and len(devs) > 1 else ""
                verb = {"on": "Включил", "off": "Выключил", "brighter": "Сделал ярче", "dimmer": "Приглушил",
                        "warm": "Сделал теплее" if (c.get("value") or 0) < 0 else "Сделал холоднее"}.get(c["op"])
                if c["op"] == "brightness":
                    msg = f"{what[:1].upper() + what[1:]}{where} — яркость {c['value']}%."
                elif c["op"] == "temperature":
                    msg = f"{what[:1].upper() + what[1:]} — {c['value']}°."
                else:
                    msg = f"{verb}: {what}{where}." if n == len(devs) else f"{verb}: {what}{where} — ответили {n} из {len(devs)}."
        except HomeError as e:
            msg = f"Умный дом: {e}."
        log.info("🏠 %s → %s", text, msg)
        self._say_quick(msg, text, via)
        return True

    def _vault_cmd(self, text, via=None):
        """Сейф: «запомни пароль от вайфая: кот123», «какой пароль от вайфая?». Безопасность: значение не читается
        вслух, не пишется в журнал и не попадает в историю разговора (а значит — и в модель); на экране ответ помечен
        secret. Из мессенджеров (via: Telegram, MAX) значение НЕ отдаём: переписка хранится у мессенджера и на его
        серверах, телефон с открытым чатом видят другие — только «есть в сейфе, посмотрите на экране компьютера»
        (аудит 19.10, SECURITY.md «Ключи и пароли»). Положить в сейф оттуда можно — с советом удалить сообщение."""
        from vault import parse
        c = parse(text)
        if not c:
            return False
        try:
            if c["op"] == "save":
                self.vault.put(c["name"], c["value"])
                log.info("🔐 В сейф: «%s»", c["name"])
                said = f"{c['name']}: •••"
                msg = f"Положил в сейф: «{c['name']}». Это видно только вам — студия, «Сейф паролей»."
                if via:
                    msg += " Сообщение с паролем осталось в переписке — удалите его там."
                spoken = f"Положил в сейф: {c['name'].lower()}."
            else:
                gen = re.sub(r"[^\wё ]", "", c["name"].lower()).strip() in ("пароль", "пароли", "пин", "пин-код", "пинкод", "коды", "логин")
                x = None if gen else self.vault.find(c["name"])
                said = text
                names = [i["name"] for i in self.vault.list()]
                if x is None and (not gen or not names):
                    return False                         # в сейфе нет — пусть ответит модель (может, это в заметках)
                if x is None:
                    msg = ("В сейфе: " + ", ".join(f"«{n}»" for n in names[:8]) + (" и другие" if len(names) > 8 else "") +
                           ". Какой нужен?") if names else "В сейфе пока пусто. Скажите, например: «запомни пароль от вайфая: …»."
                    spoken = msg
                else:
                    log.info("🔐 Из сейфа: «%s»%s", x["name"], " — в мессенджер не отдаю" if via else "")
                    if via:
                        msg = spoken = (f"🔐 «{x['name']}» есть в сейфе, но в переписку пароли не отправляю — их видно другим и они "
                                        f"хранятся у мессенджера. Посмотрите на экране компьютера: студия → «Сейф паролей».")
                    else:
                        msg = f"🔐 {x['name']}: {x['value']}"
                        spoken = f"{x['name']} — на экране."
        except Exception as e:  # noqa: BLE001
            log.warning("Сейф: %s", e)
            msg = spoken = f"Сейф не открылся: {e}"
            said = f"{c['name']}: •••" if c["op"] == "save" else text     # и при ошибке пароль на экран не выводим
        msg, spoken = self._g(msg), self._g(spoken)
        if via:
            via["done"](msg)
            return True
        self.emit("heard", text=said if c["op"] == "save" else text)
        self.emit("answer", text=msg, done=True, secret=True)
        q = queue.Queue()
        q.put(spoken)
        q.put(None)
        with self._turn_lock:
            self._cancel_locked()
            self.turn_n += 1
            turn = Turn(self.turn_n)
            self.turn = turn

        def run():
            self._speak_worker(turn, q)
            with self._turn_lock:
                if self.turn is turn:
                    self.turn = None
                    self.set_state("idle")
        threading.Thread(target=run, daemon=True).start()
        return True

    def _video_request(self, text, via=None):
        """Acknowledge transcription only after a real task exists; clarify bare links."""
        from media_intent import intent, video_urls, TRANSCRIBE, TRANSLATE, CHOICE_QUESTION, active_pending, request_scope, reply_choice
        action = intent(text)
        if action == 'status':
            items = [t for t in self.tasks.list() if t.get('workflow') in ('media-transcription', 'video-translation')
                     or TRANSCRIBE.search(str(t.get('title') or '') + ' ' + str(t.get('goal') or ''))]
            urls = video_urls(text)
            if urls:
                items = [t for t in items if any(u in str(t.get('goal') or '') or u in (t.get('media_sources') or []) for u in urls)]
            active = [t for t in items if t.get('status') in ('running','waiting','sleeping')]
            task = (active or items or [None])[0]
            if not task:
                message = 'Задача транскрибации не создана; запущенной обработки нет. Пришлите ссылку или файл с просьбой извлечь текст.'
            elif task.get('status') == 'done':
                label = 'Перевод подготовлен' if task.get('workflow') == 'video-translation' else 'Транскрибация завершена'
                message = f'{label}. В задаче «{task["title"]}» сохранено результатов: {len(task.get("outputs") or [])}. Откройте её кнопкой задач в нижней панели.'
            elif task.get('status') in ('failed','stopped','paused'):
                message = f'Задача «{task["title"]}»: {task.get("status_ru") or task["status"]}. ' + (task.get('error') or 'Обработка сейчас не выполняется. Подробности — в журнале задачи.')
            else:
                message = f'Задача «{task["title"]}»: {task.get("status_ru") or task["status"]}. ' + (task.get('activity') or 'Текущий этап — в карточке задачи.')
            self._say_quick(message, text, via)
            return True
        scope = request_scope(via, getattr(getattr(self, 'chatlog', None), 'active', 'legacy'))
        pending = active_pending(getattr(self, '_video_pending', None), scope, time.monotonic())
        if pending and reply_choice(text) == 'cancel':
            self._video_pending = None
            self._say_quick('Хорошо, ничего с этим роликом не запускаю.', text, via)
            return True
        urls = video_urls(text)
        if pending and not urls and (TRANSCRIBE.search(text) or TRANSLATE.search(text)):
            urls = pending['urls']
        if action in ('transcribe', 'translate') and not urls and not via:
            for message in reversed(getattr(self, 'history', [])[-8:]):
                if message.get('role') == 'user':
                    previous = video_urls(str(message.get('content') or ''))
                    if previous:
                        urls = previous
                        break
        if not action and pending and str(text).strip().casefold().rstrip('.!') in ('да','хорошо','давай'):
            self._say_quick(CHOICE_QUESTION, text, via)
            return True
        if not action:
            return False
        if action == 'process' and re.fullmatch(r'\s*обработ(?:ай|ать)\s+(?:ролик|видео)\s*:?\s*', re.sub(r'https?://\S+', '', text), re.I):
            self._video_pending = dict(scope=scope, time=time.monotonic(), urls=urls, action='process')
            self._say_quick('Как обработать ролик: сделать конспект, анализ, резюме или что-то другое? Уточните нужный результат.', text, via)
            return True
        if action in ('open', 'download', 'process'):
            self._video_pending = None
            return False  # normal tools preserve the requested viewing/analysis and their permissions
        if action == 'clarify' and pending and pending.get('action') == 'transcribe':
            action = 'transcribe'
        if action == 'clarify':
            self._video_pending = dict(scope=scope, time=time.monotonic(), urls=urls, action='clarify')
            self._say_quick(CHOICE_QUESTION, text, via)
            return True
        if not urls:
            pending_action = 'translate' if action == 'translate' else 'transcribe'
            self._video_pending = dict(scope=scope, time=time.monotonic(), urls=[], action=pending_action)
            message = ('Пришлите ссылку на ролик или аудио/видеофайл — подготовлю перевод и субтитры.'
                       if action == 'translate' else 'Пришлите ссылку на ролик или аудио/видеофайл — извлеку текст речи.')
            self._say_quick(message, text, via)
            return True
        on, confirm = self.toolbox.policy('start_task')
        if not on or confirm:
            return False
        try:
            if action == 'translate':
                target = 'en' if re.search(r'на\s+(?:англ|english)|по-англий', text, re.I) else 'ru'
                source = 'en' if re.search(r'(?:с|из)\s+(?:англ|english)', text, re.I) else ('ru' if re.search(r'(?:с|из)\s+(?:рус|russian)', text, re.I) else 'auto')
                task = self.tasks.create_video_translation(urls, source_language=source, target_language=target, goal=text)
            else:
                task = self.tasks.create_transcription(urls, goal=text)
        except Exception as error:
            self._say_quick(('Не удалось запустить перевод: ' if action == 'translate' else 'Не удалось запустить транскрибацию: ') + str(error), text, via)
            return True
        self._video_pending = None
        ready = getattr(task, 'status', '') == 'done'
        message = (f'Этот источник уже обработан. Результаты — в задаче «{task.title}»; повторная загрузка не нужна.' if ready else
                   f'Задача «{task.title}» запущена. Ход обработки — по кнопке задач в нижней панели.')
        self._say_quick(message, text, via, acts=[] if ready else ['start_task'])
        return True

    def _pc_cmd(self, text):
        """«Громче», «тише», «громкость 40», «выключи звук», «выключи компьютер», «отмени выключение» — без модели."""
        from pc import parse
        from media_intent import protected
        if protected(text):
            return False
        if getattr(self,'_local_song_followup',lambda _:False)(text):return True
        import tools as _tools
        from voice_commands import parse as parse_voice
        settings = getattr(self, 's', None)
        configured = parse_voice(text, settings.get('voice_commands') if settings else None)
        # Голосовые команды «найти/поискать/обработать песню …» добавляют
        # карточку и сразу запускают фоновый поиск источников и пакетный разбор.
        # Это локальная операция студии, поэтому она не должна превращаться
        # в открытие Яндекс Музыки или ждать закрытого окна интерфейса.
        if configured and configured['action'] == 'song_process':
            if not configured['enabled']:
                self._say_quick('Эта голосовая команда выключена в настройках «Руки → Голосовые команды».', text)
                return True
            query = configured.get('query', '').strip()
            try:
                row = self.song_store.create(query, source='query')
                self.song_sources.start(row['song_id'], process=True)
            except Exception as error:
                self._say_quick('Не удалось запустить обработку песни: ' + str(error), text)
                return True
            self._say_quick(f'Песня «{query}» добавлена. Запустила пакетную обработку в фоне.', text,
                            acts=['start_task'])
            return True
        if configured and configured['action'] not in ('yandex_search', 'song_library') and not _tools.MAC:
            configured = None
        if configured and not configured['enabled']:
            self._say_quick('Эта голосовая команда выключена в настройках «Руки → Голосовые команды».', text)
            return True
        if configured and configured['action'] == 'song_library':
            on, needs_confirm = self.toolbox.policy('song_library')
            if not on:
                self._say_quick('Локальная библиотека отключена в настройках умений.', text)
                return True
            def run_local():
                if needs_confirm and not self.confirm('Открыть песню из локальной библиотеки: «' + configured['query'] + '»?', timeout=30):
                    return
                if not self.toolbox.policy('song_library')[0]:return
                try:
                    self._run_local_song_choice(configured['query'], text, configured['song_action'])
                except Exception as error:
                    self._say_quick('Не удалось обратиться к локальной библиотеке: ' + str(error), text)
            if needs_confirm:
                threading.Thread(target=run_local, name='local-song-confirm', daemon=True).start()
            else:
                run_local()
            return True
        if configured and hasattr(self, 'confirm'):
            tool = 'yandex_music' if configured['action'] == 'yandex_search' else 'control_mac_app'
            on, needs_confirm = self.toolbox.policy(tool)
            if not on:
                self._say_quick('Управление недоступно или выключено в настройках «Руки → Умения».', text)
                return True
            if needs_confirm:
                args = {'query': configured['query']} if tool == 'yandex_music' else {k:v for k,v in configured.items() if k not in ('enabled','title')}
                def run_confirmed():
                    question = self.toolbox.describe(tool, args) if tool != 'yandex_music' else 'Открыть поиск в Яндекс Музыке: «' + args['query'] + '»?'
                    if not self.confirm(question, timeout=30):return
                    if not self.toolbox.policy(tool)[0]:return
                    try:
                        result = _tools.t_yandex_music(args, self.toolbox.hooks) if tool == 'yandex_music' else _tools.t_control_mac_app(args, self.toolbox.hooks)
                    except Exception as error:result = str(error)
                    self._say_quick(result, text)
                threading.Thread(target=run_confirmed, name='voice-command-confirm', daemon=True).start()
                return True
        if configured and configured['action'] == 'yandex_search':
            on, confirm = self.toolbox.policy('yandex_music')
            if not on or confirm:return False
            try:return self._yandex_cmd(configured['query'], text, exact=True)
            except Exception as error:
                self._say_quick(str(error), text)
                return True
        if _tools.MAC:
            from mac_control import parse as parse_app, AppNotFound
            request = {k:v for k,v in configured.items() if k not in ('enabled','title')} if configured else parse_app(text)
            if not configured and request and request['action'] in ('open','quit','focus','minimize','dnd_on','dnd_off'):
                request = None  # Removed templates must not return through the built-in parser.
            if request:
                on, confirm = self.toolbox.policy("control_mac_app")
                if on and not confirm:
                    try:
                        result = _tools.t_control_mac_app(request, self.toolbox.hooks)
                    except AppNotFound as error:
                        from voice_commands import config
                        known = configured and configured.get('application') in {x['target'] for x in config(settings.get('voice_commands') if settings else None)['applications']}
                        if known or re.search(r'\b(?:приложение|программу)\b', text, re.I):
                            self._say_quick(str(error) + '. Могу помочь проверить название в «Руки → Голосовые команды». Настроим?', text)
                            return True
                        return False  # Other commands (web, music, tasks) retain their normal routing.
                    except Exception as error:
                        result = str(error)
                    self._say_quick(result, text)
                    return True
        c = parse(text)
        if not c:
            return False
        if c["op"] == "volume" and c.get("change") and self.radio.playing \
                and self._radio_step(c["change"] > 0, text):
            return True                                        # играет радио — громче/тише само радио, как у колонки
        if c["op"] == "volume":
            if getattr(self, "server", False):             # сервер: своего звука нет — честно и с уточнением
                self._say_quick("Здесь, на сервере, у меня нет звука — делать тише нечего. Что именно сделать тише: "
                                "музыку или звук на телефоне, компьютере?", text)
                return True
            on, _conf = self.toolbox.policy("set_volume")
            if not on:
                return False
            args = {k: v for k, v in c.items() if k != "op"}
            try:
                msg = _tools.t_volume(args, {})
            except Exception as e:  # noqa: BLE001
                log.warning("громкость: %s", e)
                msg = "Не получилось изменить громкость: " + _tools.human_error(e)
            log.info("🔊 %s → %s", text, msg)
            self.emit("heard", text=text)
            self.emit("answer", text=msg + ".", done=True)          # без голоса — громкость слышно и так
            self.set_state("idle")
            return True
        on, _conf = self.toolbox.policy("power")
        if not on:
            return False
        if c["op"] == "power_cancel":
            was = self.power.cancel()
            self.emit("power", cancelled=True)
            self._say_quick("Отменил, компьютер не выключаю." if was else "Выключение и так не запланировано.", text)
            return True
        act = c["action"]
        msg = self.power.schedule(act, on_error=lambda m: self.note(m))
        self.emit("power", action=act, seconds=60 if act in ("shutdown", "reboot", "logout") else 0)
        log.info("⏻ %s → %s", text, act)
        self._say_quick(msg, text)
        return True

    def _obsidian_index_info(self):
        """Данные для оглавления DEWIAR.md в хранилище Obsidian: имя персонажа и последние 7 дней дневника."""
        from diary import human_date
        titles = (getattr(self, "diary", None) and self.diary.data.get("titles")) or {}
        days = []
        for p in sorted((self.workspace.root / "Дневник").glob("*.md"), reverse=True)[:7]:
            t = titles.get(p.stem) or {}
            try:
                days.append({"day": p.stem, "label": human_date(p.stem), "title": t.get("title", ""), "mood": t.get("mood", "")})
            except ValueError:
                continue
        return {"name": self.persona_name(), "days": days}

    def diary_ask(self, instruction, material):
        """Ночная запись: модель пишет голосом текущего персонажа (его подсказка и «Обо мне»), без инструментов."""
        prof = self.s.active_llm()
        if not prof:
            raise ValueError("нет модели — подключите её в разделе «Модели»")
        return "".join(stream_chat(dict(prof, max_tokens=1600), self._system(False),
                                   [{"role": "user", "content": instruction + "\n\n---\n\n" + material}]))

    def _diary_loop(self):
        """Фоновый поток: ночью (после diary.hour, по умолчанию 3:00) — или утром, как только компьютер проснулся, —
        запись о прожитом дне; раз в неделю по дневнику обновляется «душа» персонажа. Пишем, только когда
        с ассистентом не говорили 10 минут. Проверка — раз в 10 минут; первая — через 90 с после запуска
        (DEWIAR_DIARY_WAIT — для проверок; при вводе из wav-файла, т. е. в тестах, цикл фактически не работает)."""
        first = float(os.environ.get("DEWIAR_DIARY_WAIT") or (90 if not self.wav_input else 3600 * 24))
        if self.stopping.wait(first):
            return
        try:
            self.diary.log.prune()
        except Exception:  # noqa: BLE001
            pass
        while not self.stopping.is_set():
            try:
                on = self.s.get("diary", "night")
                quiet = self.turn is None and (not self.last_turn_time or time.monotonic() - self.last_turn_time > 600)
                hour = self.s.get("diary", "hour")
                day = self.diary.pending(hour=3 if hour in (None, "") else int(hour)) if on is not False and quiet else None
                if day and self.s.active_llm():
                    got = self.diary.write(day)
                    if got:
                        self.emit("diary", **got)
                        name = self.persona_name()
                        if self.soul.due(name) and self.soul.update(name):     # раз в неделю — и душа по дневнику
                            self.emit("soul", name=name)
            except Exception as e:  # noqa: BLE001
                log.warning("🌙 дневник: ночная запись не получилась: %s", e)
            try:                                         # ночной обход — в то же тихое время (night.py)
                hour = self.s.get("diary", "hour")
                if quiet and self.night.due(hour=3 if hour in (None, "") else int(hour)):
                    got = self.night.run()
                    self.emit("night", **{k: got[k] for k in ("day", "note", "path", "repaired", "left")})
            except Exception as e:  # noqa: BLE001
                log.warning("🌙 ночной обход не получился: %s", e)
            if self.stopping.wait(min(600, first)):
                return

    # ---------- «сам замечает и сам начинает»: вернулись к компьютеру — коротко о главном ----------

    def _welcome_loop(self):
        """Фоновый поток: раз в 5 с — сколько не трогали мышь и клавиатуру (presence.idle_seconds). Отошли дольше
        welcome.away_min минут и вернулись — _welcome_back. При вводе из wav-файла (тесты) не работает."""
        if self.wav_input or self.stopping.wait(30):
            return
        import presence
        self._away = presence.Away()
        while not self.stopping.is_set():
            try:
                self._away.away_after = max(5, int(self.s.get("welcome", "away_min") or 20)) * 60
                gone = self._away.feed(presence.idle_seconds())
                if gone:
                    self._welcome_back(gone)
            except Exception as e:  # noqa: BLE001
                log.debug("приветствие: %s", e)
            if self.stopping.wait(5):
                return

    def _upcoming(self, hours=2):
        """Дела и встречи сегодня в ближайшие hours часов -> [("HH:MM", что)]."""
        now = datetime.now()
        items = self.calendar_month(now.year, now.month)["days"].get(now.date().isoformat(), [])
        lo, hi = f"{now:%H:%M}", f"{now + timedelta(hours=hours):%H:%M}" if (now + timedelta(hours=hours)).date() == now.date() else "23:59"
        out = [(x["time"], x["title"]) for x in items if x.get("type") in ("reminder", "event") and x.get("time")
               and lo <= x["time"] <= hi and not x.get("done")]
        return sorted(out)

    def _welcome_back(self, gone, wait=3.0):
        """Человек вернулся после gone секунд. Молчим, если: выключено, микрофон выключен, созвон, звенит будильник,
        идёт разговор, виджет спрятали и с тех пор не звали, нет модели. Иначе — presence.plan решает, есть ли что
        сказать, и модель говорит это голосом персонажа (1–4 фразы). -> True, если заговорили."""
        import presence
        if self.s.get("welcome", "on") is False or self.muted or self.meeting or self.alarm or self._intro_running:
            return False
        if self.stopping.wait(wait):                     # дать человеку сесть; заговорил сам — не перебиваем
            return False
        if self.turn is not None or self.state not in ("idle", "listening") or not self.s.active_llm():
            return False
        if getattr(self, "_tray_at", 0) > (self.last_turn_time or 0):
            return False                                 # «спрячься» — значит, просили не мешать
        try:
            tasks = self.tasks.list()
        except Exception:  # noqa: BLE001
            tasks = []
        try:
            upcoming = self._upcoming()
        except Exception:  # noqa: BLE001
            upcoming = []
        last = getattr(self, "_welcome_at", None)
        try:
            done = self.done.stats()
        except Exception:  # noqa: BLE001
            done = None
        news_fact = ""
        try:                                             # обновились, а студию с «Что нового» не открывали — скажет сама
            import news as _news
            ui = self.s.get("ui") or {}
            if ui.get("welcome") == "done" and ui.get("news_said") != VERSION and ui.get("news_seen") != VERSION:
                news_fact = _news.say_fact(_news.releases(ui.get("news_seen") or ui.get("news_said") or "", VERSION, "pc", 3))
        except Exception:  # noqa: BLE001
            news_fact = ""
        p = presence.plan(gone, datetime.now() - timedelta(seconds=gone), tasks, upcoming, last, dstat=done,
                          eve_said=getattr(self, "_eve_said", None), news=news_fact)
        if not p:
            log.info("👋 вернулись через %s — сказать нечего, молчим", presence._hm(gone))
            return False
        self._welcome_at = datetime.now()
        if news_fact and news_fact in p["facts"]:
            self.s.update({"ui": {"news_said": VERSION}})
        if p["kind"] == "eve":
            self._eve_said = self._welcome_at.date()
        ctx = ""
        if p["kind"] == "day":
            try:
                ctx = self._brief_context()
            except Exception:  # noqa: BLE001
                ctx = ""
        log.info("👋 вернулись через %s — %s", p["gone"], "сводка дня" if p["kind"] == "day" else "итог дня" if p["kind"] == "eve" else "; ".join(p["facts"]))
        self.ask(presence.prompt(p, ctx), raw=True, shown="👋 С возвращением")
        return True

    def _radio_check_loop(self):
        """Фоновый поток: через 40 с после запуска и затем раз в 6 часов — какие станции списка играют в этой сети
        (результат использует _play_station, чтобы не ждать на молчащих)."""
        if self.stopping.wait(40 if not self.wav_input else 3600 * 24):
            return
        while not self.stopping.is_set():
            try:
                self.radio_health.check_all(self.music.stations())
            except Exception as e:  # noqa: BLE001
                log.debug("проверка станций: %s", e)
            if self.stopping.wait(6 * 3600):
                return

    def _play_station(self, cands, said, phrase):
        """Сказать «Включаю …» и запустить станцию в фоновом потоке; нет 2 секунд звука за 11 с — следующая из cands.
        Станции, которые в этой сети молчат (фоновая проверка), — в конце: не ждём на них. Свой список кончился —
        пробуем каталог radio-browser; молчат две и больше — диагностика (ffmpeg, сеть) и сообщение пользователю."""
        first0 = cands[0]
        cands = self.radio_health.order(cands)
        if cands[0] is not first0:
            log.info("📻 «%s» сейчас не отвечает (проверка) — сначала «%s»", first0.get("name"), cands[0].get("name"))
            phrase = "«" + first0.get("name", "") + "» сейчас не отвечает — включаю «{}»."
        st = cands[0]
        self._say_quick(phrase.format(st["name"]), said)

        def run():
            why = ""
            alts = list(cands)
            first = cands[0]
            tried = set()
            i = -1
            while alts:
                s = alts.pop(0)
                if s["url"] in tried:
                    continue
                tried.add(s["url"])
                i += 1
                try:
                    self.radio.play(s)
                except Exception as e:  # noqa: BLE001
                    log.info("Радио: %s", e)
                    self.note(f"Радио: {e}")
                    return
                gen = self.radio._gen
                from radio import RATE as _R
                end = time.monotonic() + 11.0             # успех — 2 секунды звука подряд (https-поток стартует не сразу)
                while time.monotonic() < end and self.radio.playing and self.radio.got < _R * 2 * 2:
                    if self.radio._gen != gen:
                        return
                    time.sleep(0.25)
                if self.radio._gen != gen or self.radio.paused:   # пользователь сказал «стоп»/«пауза» или включил другое
                    return
                if self.radio.playing and self.radio.got >= _R * 2 * 2:
                    self.radio_health.mark(s["url"], True)
                    v = round(self.radio.volume * 100)
                    quiet = f" Громкость радио всего {v}% — скажите «громче»." if v <= 20 else ""
                    if i:
                        self.note(f"«{first['name']}» не ответила — играет «{s['name']}».{quiet}")
                    elif quiet:
                        self.note(quiet.strip())
                    return
                why = self.radio.failed or why
                log.warning("Радио: станция «%s» не играет: %s", s["name"], self.radio.failed or "нет звука")
                if not (self.radio.failed or "").startswith("ffmpeg"):    # сломан ffmpeg — дело не в станции
                    self.radio_health.mark(s["url"], False, self.radio.failed or "нет звука")
                if self.radio.playing:
                    self.radio.stop()
                if not alts and not s.get("_cat"):                 # свой список молчит — открытый каталог radio-browser
                    try:
                        from radio import search
                        q = (first.get("tags") or "").split()[0:1] or [first.get("name", "")]
                        found = [dict(x, _cat=True) for x in (search(first.get("name", "")) or []) + (search(q[0]) or [])]
                        alts = [x for x in found if x["url"] not in tried][:4]
                        if alts:
                            log.info("Радио: пробую каталог — %s", ", ".join(x["name"] for x in alts))
                    except Exception as e:  # noqa: BLE001
                        log.info("Радио: каталог недоступен: %s", e)
            self._music_failed = time.monotonic()
            if len(tried) >= 2:                               # молчат все — дело не в станциях: где поломка?
                from eyes import ffmpeg_path
                from radio import diagnose
                where, fix = diagnose(ffmpeg_path(), first["url"])
                log.warning("Радио: проверка — %s; %s", where, fix)
                self.note(f"Радио не играет: {where}. {fix[0].upper() + fix[1:]}.")
                self.announce(f"Радио не играет, и дело не в станциях: {where}. {fix[0].upper() + fix[1:]}.")
                return
            self.note("Радиостанции не отвечают" + (f" ({why})" if why else "") + ". Проверьте интернет или скажите другую станцию.")
        threading.Thread(target=run, name="music", daemon=True).start()
